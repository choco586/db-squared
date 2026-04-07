import os
from dotenv import load_dotenv

load_dotenv()

# ─── Primary DB — AWS RDS ─────────────────────────────────────────────────────
PRIMARY_DB_HOST     = os.getenv('PRIMARY_HOST')
PRIMARY_DB_PORT     = int(os.getenv('PRIMARY_PORT'))
PRIMARY_DB_USER     = os.getenv('PRIMARY_USER')
PRIMARY_DB_PASSWORD = os.getenv('PRIMARY_PASSWORD')
PRIMARY_DB_NAME     = os.getenv('PRIMARY_DB')

# ─── Backup schedule ──────────────────────────────────────────────────────────
# How often the background loop runs a full backup cycle (per business + system)
BACKUP_INTERVAL_MINUTES = int(os.getenv('BACKUP_INTERVAL_MINUTES'))

# ─── AWS S3 — IAM role on EC2 handles auth, no keys needed ───────────────────
AWS_REGION        = os.getenv('AWS_REGION')
AWS_BACKUP_BUCKET = os.getenv('AWS_BACKUP_BUCKET')

# ─── Backward-compat aliases ──────────────────────────────────────────────────
MASTER_DB_HOST     = PRIMARY_DB_HOST
MASTER_DB_PORT     = PRIMARY_DB_PORT
MASTER_DB_USER     = PRIMARY_DB_USER
MASTER_DB_PASSWORD = PRIMARY_DB_PASSWORD
MASTER_DB_NAME     = PRIMARY_DB_NAME