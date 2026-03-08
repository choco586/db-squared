# backend/routes/backup_routes.py

import os
import subprocess
from datetime import datetime
from flask import jsonify, g
from config import (
    PRIMARY_DB_HOST, PRIMARY_DB_PORT, PRIMARY_DB_USER,
    PRIMARY_DB_PASSWORD, PRIMARY_DB_NAME,
    AWS_REGION, AWS_BACKUP_BUCKET,
)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

BACKUP_BASE_DIR = os.path.join('logs', 'backups')


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _upload_to_s3(local_path, s3_key):
    """
    Upload to S3 using the EC2 IAM role — no keys needed.
    Returns (success: bool, message: str).
    """
    if not S3_AVAILABLE:
        return False, "boto3 not installed — run: pip install boto3"

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


def run_backup():
    """
    Any authenticated user can trigger a backup of their own business data.

    Steps:
      1. mysqldump each business-scoped table with --where=business_unit_id=X
      2. Write combined .sql to logs/backups/business_<id>/
      3. Upload to S3 — failure is a warning, not a hard error
    """
    business_id = g.business_unit_id
    backup_dir  = _ensure_dir(os.path.join(BACKUP_BASE_DIR, f'business_{business_id}'))
    timestamp   = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename    = f'backup_bu{business_id}_{timestamp}.sql'
    output_path = os.path.join(backup_dir, filename)

    tables       = ['customers', 'products', 'orders', 'order_items']
    combined_sql = [
        f"-- DB-Squared backup for business_unit_id={business_id}",
        f"-- Generated: {datetime.now().isoformat()}",
        f"-- Tables: {', '.join(tables)}\n",
    ]

    try:
        for table in tables:
            cmd = [
                'mysqldump',
                f'--host={PRIMARY_DB_HOST}',
                f'--port={PRIMARY_DB_PORT}',
                f'--user={PRIMARY_DB_USER}',
                f'--password={PRIMARY_DB_PASSWORD}',
                '--single-transaction',
                '--no-create-info',
                f'--where=business_unit_id={business_id}',
                PRIMARY_DB_NAME,
                table,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)

            if result.returncode != 0:
                err = result.stderr.decode('utf-8', errors='replace').strip()
                err = err.replace(PRIMARY_DB_PASSWORD or '', '***')
                return jsonify({'success': False, 'error': f"Backup failed on '{table}': {err}"}), 500

            combined_sql.append(f"\n-- Table: {table}")
            combined_sql.append(result.stdout.decode('utf-8', errors='replace'))

        with open(output_path, 'w') as f:
            f.write('\n'.join(combined_sql))

        file_size = os.path.getsize(output_path)

        s3_key        = f'backups/business_{business_id}/{filename}'
        s3_ok, s3_msg = _upload_to_s3(output_path, s3_key)

        return jsonify({
            'success':     True,
            'filename':    filename,
            'size_bytes':  file_size,
            'local_path':  output_path,
            's3_uploaded': s3_ok,
            's3_location': s3_msg if s3_ok else None,
            's3_warning':  None if s3_ok else s3_msg,
        })

    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Backup timed out'}), 500
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'mysqldump not found on this server'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Backup failed: {str(e)}'}), 500


def list_backups():
    """
    List local backup files for the logged-in user's business.
    Checks S3 to mark which files have been uploaded.
    """
    business_id = g.business_unit_id
    backup_dir  = os.path.join(BACKUP_BASE_DIR, f'business_{business_id}')

    if not os.path.exists(backup_dir):
        return jsonify({'success': True, 'backups': []})

    try:
        s3_keys = set()
        if S3_AVAILABLE and AWS_BACKUP_BUCKET:
            try:
                s3        = boto3.client('s3', region_name=AWS_REGION)
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(
                    Bucket = AWS_BACKUP_BUCKET,
                    Prefix = f'backups/business_{business_id}/'
                ):
                    for obj in page.get('Contents', []):
                        s3_keys.add(os.path.basename(obj['Key']))
            except Exception:
                pass

        files = []
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            if not fname.endswith('.sql'):
                continue
            fpath = os.path.join(backup_dir, fname)
            stat  = os.stat(fpath)
            files.append({
                'filename':   fname,
                'size_bytes': stat.st_size,
                'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'in_s3':      fname in s3_keys,
            })

        return jsonify({'success': True, 'backups': files})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to list backups'}), 500


def system_backup():
    """
    Superadmin only (enforced in app.py).
    Lists all backup files across all businesses — both local and in S3.
    """
    result = {'local': {}, 's3': {}}

    if os.path.exists(BACKUP_BASE_DIR):
        try:
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
                    })
                result['local'][entry] = files
        except Exception as e:
            result['local_error'] = str(e)

    if S3_AVAILABLE and AWS_BACKUP_BUCKET:
        try:
            s3        = boto3.client('s3', region_name=AWS_REGION)
            paginator = s3.get_paginator('list_objects_v2')
            s3_files  = {}
            for page in paginator.paginate(Bucket=AWS_BACKUP_BUCKET, Prefix='backups/'):
                for obj in page.get('Contents', []):
                    parts  = obj['Key'].split('/')
                    folder = parts[1] if len(parts) > 2 else 'root'
                    fname  = parts[-1]
                    if folder not in s3_files:
                        s3_files[folder] = []
                    s3_files[folder].append({
                        'filename':   fname,
                        'size_bytes': obj['Size'],
                        'created_at': obj['LastModified'].isoformat(),
                        's3_key':     obj['Key'],
                    })
            result['s3']        = s3_files
            result['s3_bucket'] = AWS_BACKUP_BUCKET
        except Exception as e:
            result['s3_error'] = str(e)
    else:
        result['s3_warning'] = 'S3 not configured'

    return jsonify({'success': True, **result})