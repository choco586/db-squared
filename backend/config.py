import os
from dotenv import load_dotenv

load_dotenv()

# ─── Replica user (used in CHANGE REPLICATION SOURCE TO) ──────────────────────
REPLICA_USER     = os.getenv('REPLICA_USER')
REPLICA_PASSWORD = os.getenv('REPLICA_PASSWORD')

# ─── Primary DB — AWS RDS (always the write master when healthy) ───────────────
PRIMARY_DB_HOST     = os.getenv('PRIMARY_HOST')
PRIMARY_DB_PORT     = int(os.getenv('PRIMARY_PORT'))
PRIMARY_DB_USER     = os.getenv('PRIMARY_USER')
PRIMARY_DB_PASSWORD = os.getenv('PRIMARY_PASSWORD')
PRIMARY_DB_NAME     = os.getenv('PRIMARY_DB',)

# ─── Secondary DB — Docker MySQL (read replica, promoted on RDS failure) ───────
SECONDARY_DB_HOST     = os.getenv('SECONDARY_HOST', 192.168.1.108)
SECONDARY_DB_PORT     = int(os.getenv('SECONDARY_PORT', 3307))
SECONDARY_DB_USER     = os.getenv('SECONDARY_USER')
SECONDARY_DB_PASSWORD = os.getenv('SECONDARY_PASSWORD')
SECONDARY_DB_NAME     = os.getenv('SECONDARY_DB')

# ─── Replication ──────────────────────────────────────────────────────────────
PRIMARY_REPLICATION_HOST = os.getenv('PRIMARY_REPLICATION_HOST')

# ─── Session secret key ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-in-production')

# ─── AWS S3 — IAM role on EC2 handles auth, no keys needed ───────────────────
AWS_REGION        = os.getenv('AWS_REGION', 'eu-north-1')
AWS_BACKUP_BUCKET = os.getenv('AWS_BACKUP_BUCKET')

# ─── Backward-compat aliases ──────────────────────────────────────────────────
MASTER_DB_HOST          = PRIMARY_DB_HOST
MASTER_DB_PORT          = PRIMARY_DB_PORT
MASTER_DB_USER          = PRIMARY_DB_USER
MASTER_DB_PASSWORD      = PRIMARY_DB_PASSWORD
MASTER_DB_NAME          = PRIMARY_DB_NAME
MASTER_REPLICATION_HOST = PRIMARY_REPLICATION_HOST

SLAVE_DB_HOST     = SECONDARY_DB_HOST
SLAVE_DB_PORT     = SECONDARY_DB_PORT
SLAVE_DB_USER     = SECONDARY_DB_USER
SLAVE_DB_PASSWORD = SECONDARY_DB_PASSWORD
SLAVE_DB_NAME     = SECONDARY_DB_NAME