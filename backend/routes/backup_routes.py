# backend/routes/backup_routes.py
#
# Backup system — runs silently in the background.
# No API endpoints for triggering backups — everything is automated.
# The backup loop is started by app.py on startup.
#
# What it does every BACKUP_INTERVAL_HOURS:
#   1. Fetches all business IDs from the DB
#   2. For each business: mysqldump of its data → local .sql → upload to S3
#   3. Full system dump of all tables → local .sql → upload to S3
#
# Reports (CSV exports) are still available to business admins via /api/reports/<type>

import os
import csv
import io
import subprocess
import threading
import time
from datetime import datetime
from flask import jsonify, g, make_response
import db
from config import (
    PRIMARY_DB_HOST, PRIMARY_DB_PORT, PRIMARY_DB_USER,
    PRIMARY_DB_PASSWORD, PRIMARY_DB_NAME,
    AWS_REGION, AWS_BACKUP_BUCKET,
    BACKUP_INTERVAL_MINUTES,
)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

import logging
backup_logger = logging.getLogger('gtid.main')

BACKUP_BASE_DIR = os.path.join('logs', 'backups')
TABLES = ['businesses', 'users', 'customers', 'products', 'orders', 'order_items']
BUSINESS_TABLES = ['customers', 'products', 'orders', 'order_items']


# ──────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────

def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _upload_to_s3(local_path, s3_key):
    """Upload a file to S3 using the EC2 IAM role. Returns (ok, message)."""
    if not S3_AVAILABLE:
        return False, "boto3 not installed"
    if not AWS_BACKUP_BUCKET:
        return False, "AWS_BACKUP_BUCKET not set in .env"
    try:
        s3 = boto3.client('s3', region_name=AWS_REGION)
        s3.upload_file(local_path, AWS_BACKUP_BUCKET, s3_key)
        return True, f"s3://{AWS_BACKUP_BUCKET}/{s3_key}"
    except (BotoCoreError, ClientError) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _mysqldump(tables, where_clause=None, label='dump'):
    """
    Run mysqldump for a list of tables.
    Returns (success, sql_string, error_message).
    where_clause: optional string like 'business_unit_id=3'
    """
    combined = [
        f"-- DB-Squared {label}",
        f"-- Generated: {datetime.now().isoformat()}",
        f"-- Tables: {', '.join(tables)}\n",
    ]

    for table in tables:
        cmd = [
            'mysqldump',
            f'--host={PRIMARY_DB_HOST}',
            f'--port={PRIMARY_DB_PORT}',
            f'--user={PRIMARY_DB_USER}',
            f'--password={PRIMARY_DB_PASSWORD}',
            '--no-tablespaces',
            '--skip-lock-tables',
        ]
        if where_clause:
            cmd.append(f'--where={where_clause}')
        cmd += [PRIMARY_DB_NAME, table]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                err = result.stderr.decode('utf-8', errors='replace').strip()
                err = err.replace(PRIMARY_DB_PASSWORD or '', '***')
                return False, None, f"mysqldump failed on '{table}': {err}"
            combined.append(f"\n-- Table: {table}")
            combined.append(result.stdout.decode('utf-8', errors='replace'))
        except subprocess.TimeoutExpired:
            return False, None, f"mysqldump timed out on '{table}'"
        except FileNotFoundError:
            return False, None, "mysqldump not found — install MySQL client tools"

    return True, '\n'.join(combined), None


def _write_and_upload(sql, folder, filename, s3_prefix):
    """Write sql to disk and upload to S3. Returns a result dict."""
    _ensure_dir(folder)
    path = os.path.join(folder, filename)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(sql)

    size          = os.path.getsize(path)
    s3_key        = f"{s3_prefix}/{filename}"
    s3_ok, s3_msg = _upload_to_s3(path, s3_key)

    return {
        'filename':    filename,
        'size_bytes':  size,
        'local_path':  path,
        's3_uploaded': s3_ok,
        's3_location': s3_msg if s3_ok else None,
        's3_warning':  None if s3_ok else s3_msg,
    }


# ──────────────────────────────────────────────────────────────
# CORE BACKUP FUNCTIONS
# Called by the background loop — no Flask request context needed
# ──────────────────────────────────────────────────────────────

def _backup_one_business(business_id):
    """Dump all rows belonging to business_id and upload to S3."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename  = f'backup_bu{business_id}_{timestamp}.sql'
    folder    = os.path.join(BACKUP_BASE_DIR, f'business_{business_id}')
    s3_prefix = f'backups/business_{business_id}'

    ok, sql, err = _mysqldump(
        BUSINESS_TABLES,
        where_clause=f'business_unit_id={business_id}',
        label=f'business_{business_id}',
    )
    if not ok:
        backup_logger.error(f"[Backup] business_{business_id} failed: {err}")
        return False

    result = _write_and_upload(sql, folder, filename, s3_prefix)
    if result['s3_uploaded']:
        backup_logger.info(f"[Backup] business_{business_id} → {result['s3_location']} ({result['size_bytes']} bytes)")
    else:
        backup_logger.warning(f"[Backup] business_{business_id} saved locally but S3 failed: {result['s3_warning']}")
    return True


def _backup_full_system():
    """Dump all tables (full system) and upload to S3."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename  = f'backup_system_{timestamp}.sql'
    folder    = os.path.join(BACKUP_BASE_DIR, 'system')
    s3_prefix = 'backups/system'

    ok, sql, err = _mysqldump(TABLES, label='full_system')
    if not ok:
        backup_logger.error(f"[Backup] System backup failed: {err}")
        return False

    result = _write_and_upload(sql, folder, filename, s3_prefix)
    if result['s3_uploaded']:
        backup_logger.info(f"[Backup] System → {result['s3_location']} ({result['size_bytes']} bytes)")
    else:
        backup_logger.warning(f"[Backup] System saved locally but S3 failed: {result['s3_warning']}")
    return True


def _get_all_business_ids():
    """Fetch all business IDs directly from DB — no Flask context needed."""
    try:
        conn   = db.get_read_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM businesses ORDER BY id")
        ids = [row[0] for row in cursor.fetchall()]
        cursor.close()
        db.return_connection(conn)
        return ids
    except Exception as e:
        backup_logger.error(f"[Backup] Failed to fetch business IDs: {e}")
        return []


def run_backup_cycle():
    """
    Run a full backup cycle:
      - One dump per business (business-scoped data only)
      - One full system dump (all tables)
    Called by the background loop. No Flask context required.
    """
    backup_logger.info("[Backup] Starting backup cycle...")
    started = time.time()

    ids = _get_all_business_ids()
    if not ids:
        backup_logger.warning("[Backup] No businesses found — skipping cycle")
        return

    success = 0
    for bid in ids:
        if _backup_one_business(bid):
            success += 1

    _backup_full_system()

    elapsed = round(time.time() - started, 1)
    backup_logger.info(
        f"[Backup] Cycle complete — {success}/{len(ids)} businesses backed up "
        f"+ system dump in {elapsed}s"
    )


# ──────────────────────────────────────────────────────────────
# BACKGROUND LOOP
# Started once by app.py on startup
# ──────────────────────────────────────────────────────────────

def _backup_loop():
    """
    Runs forever in a daemon thread.
    Re-reads BACKUP_INTERVAL_HOURS from .env after every cycle so you can
    change the interval without restarting the backend — just edit .env and
    the new value takes effect after the current sleep finishes.
    """
    backup_logger.info("[Backup] Loop started")

    while True:
        try:
            run_backup_cycle()
        except Exception as e:
            backup_logger.error(f"[Backup] Unhandled error in backup cycle: {e}", exc_info=True)

        # Re-read .env so interval changes take effect without a restart
        from dotenv import load_dotenv
        load_dotenv(override=True)
        interval_minutes   = int(os.getenv('BACKUP_INTERVAL_MINUTES', 6))
        interval_seconds = interval_minutes * 60

        backup_logger.info(
            f"[Backup] Next cycle in {interval_minutes}m "
            f"({datetime.now().strftime('%H:%M:%S')} now)"
        )
        time.sleep(interval_seconds)


def start_backup_loop():
    """
    Call this once from app.py at startup.
    Spawns a daemon thread so it dies cleanly when Flask exits.
    """
    t = threading.Thread(target=_backup_loop, daemon=True, name="Backup-Loop")
    t.start()
    backup_logger.info("[Backup] Background backup loop started")


# ──────────────────────────────────────────────────────────────
# REPORTS — business admin only, called from app.py routes
# CSV export of their own business data. No mysqldump involved.
# ──────────────────────────────────────────────────────────────

def download_report(report_type):
    """
    Generate and stream a CSV report for the logged-in business.
    report_type: customers | products | orders
    """
    business_id = g.business_unit_id

    queries = {
        'customers': {
            'sql':     "SELECT customer_id, name, email, phone, created_at FROM customers WHERE business_unit_id = %s ORDER BY customer_id",
            'headers': ['ID', 'Name', 'Email', 'Phone', 'Created At'],
        },
        'products': {
            'sql':     "SELECT product_id, name, supplier, price, stock_quantity, expiry_date, created_at FROM products WHERE business_unit_id = %s ORDER BY product_id",
            'headers': ['ID', 'Name', 'Supplier', 'Price', 'Stock', 'Expiry Date', 'Created At'],
        },
        'orders': {
            'sql':     """
                SELECT o.order_id, c.name, o.order_date, o.total_amount, o.status
                FROM orders o
                JOIN customers c
                  ON o.customer_id = c.customer_id
                 AND o.business_unit_id = c.business_unit_id
                WHERE o.business_unit_id = %s
                ORDER BY o.order_id
            """,
            'headers': ['Order ID', 'Customer', 'Date', 'Total', 'Status'],
        },
    }

    if report_type not in queries:
        return jsonify({'success': False, 'error': f"Unknown report type '{report_type}'. Use: customers, products, orders"}), 400

    q    = queries[report_type]
    conn = db.get_read_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(q['sql'], (business_id,))
        rows = cursor.fetchall()
        cursor.close()
    except Exception:
        return jsonify({'success': False, 'error': 'Failed to generate report'}), 500
    finally:
        db.return_connection(conn)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(q['headers'])
    writer.writerows(rows)
    output.seek(0)

    filename = f"{report_type}_business{business_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response = make_response(output.getvalue())
    response.headers['Content-Type']        = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


# ──────────────────────────────────────────────────────────────
# SUPERADMIN — list backups (read-only, no trigger endpoint)
# ──────────────────────────────────────────────────────────────

def list_backups():
    """List all local backup files and their S3 status."""
    if not os.path.exists(BACKUP_BASE_DIR):
        return jsonify({'success': True, 'backups': {}})

    try:
        s3_keys = set()
        if S3_AVAILABLE and AWS_BACKUP_BUCKET:
            try:
                s3        = boto3.client('s3', region_name=AWS_REGION)
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=AWS_BACKUP_BUCKET, Prefix='backups/'):
                    for obj in page.get('Contents', []):
                        s3_keys.add(os.path.basename(obj['Key']))
            except Exception:
                pass

        all_backups = {}
        for entry in sorted(os.listdir(BACKUP_BASE_DIR)):
            entry_path = os.path.join(BACKUP_BASE_DIR, entry)
            if not os.path.isdir(entry_path):
                continue
            files = []
            for fname in sorted(os.listdir(entry_path), reverse=True):
                if not fname.endswith('.sql'):
                    continue
                fpath = os.path.join(entry_path, fname)
                stat  = os.stat(fpath)
                files.append({
                    'filename':   fname,
                    'size_bytes': stat.st_size,
                    'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    'in_s3':      fname in s3_keys,
                })
            all_backups[entry] = files

        return jsonify({'success': True, 'backups': all_backups})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500