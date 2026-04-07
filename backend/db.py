# backend/db.py
# Single-DB connection manager — AWS RDS primary only.
# Provides get_write_connection(), get_read_connection(), return_connection()
# with a simple connection pool and basic health check.

import mysql.connector
from mysql.connector import Error
from config import (
    PRIMARY_DB_HOST, PRIMARY_DB_PORT, PRIMARY_DB_USER,
    PRIMARY_DB_PASSWORD, PRIMARY_DB_NAME,
)
import time
import threading
import logging
import hashlib
import re
from logging.handlers import RotatingFileHandler
import os

# ============================================================
# LOGGING SETUP
# ============================================================

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

logging.getLogger().handlers.clear()

main_logger = logging.getLogger('gtid.main')
main_logger.setLevel(logging.INFO)
main_handler = RotatingFileHandler(
    f'{log_dir}/db_main.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
)
main_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
main_logger.addHandler(main_handler)

error_logger = logging.getLogger('gtid.error')
error_logger.setLevel(logging.ERROR)
error_handler = RotatingFileHandler(
    f'{log_dir}/db_errors.log', maxBytes=5*1024*1024, backupCount=10, encoding='utf-8'
)
error_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s\nFile: %(pathname)s:%(lineno)d\n',
    datefmt='%Y-%m-%d %H:%M:%S'
))
error_logger.addHandler(error_handler)

console_logger = logging.getLogger('gtid.console')
console_logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S'
))
console_logger.addHandler(console_handler)


class DBLogger:
    @staticmethod
    def info(msg):
        main_logger.info(msg)
        console_logger.info(msg)

    @staticmethod
    def warning(msg):
        main_logger.warning(msg)
        console_logger.warning(msg)

    @staticmethod
    def error(msg, exc_info=False):
        main_logger.error(msg)
        console_logger.error(msg)
        error_logger.error(msg, exc_info=exc_info)

    @staticmethod
    def debug(msg):
        main_logger.debug(msg)


# ============================================================
# INPUT VALIDATION
# ============================================================

class ValidationError(Exception):
    pass


class Validator:
    _DANGEROUS = ["'", '"', ';', '--', '/*', '*/']
    VALID_ORDER_STATUSES = {'placed', 'processing', 'completed', 'cancelled'}
    VALID_ROLES = {'admin', 'employee'}

    @staticmethod
    def sanitize(text, max_len=255):
        if not text or not isinstance(text, str):
            return ""
        cleaned = text
        for char in Validator._DANGEROUS:
            cleaned = cleaned.replace(char, '')
        return cleaned.strip()[:max_len]

    @staticmethod
    def require_string(value, field_name, min_len=1, max_len=255):
        cleaned = Validator.sanitize(str(value) if value is not None else '', max_len)
        if len(cleaned) < min_len:
            raise ValidationError(f"'{field_name}' must be at least {min_len} character(s)")
        return cleaned

    @staticmethod
    def require_positive_int(value, field_name):
        try:
            n = int(value)
            if n <= 0:
                raise ValueError
            return n
        except (TypeError, ValueError):
            raise ValidationError(f"'{field_name}' must be a positive integer")

    @staticmethod
    def require_positive_decimal(value, field_name):
        try:
            n = float(value)
            if n < 0:
                raise ValueError
            return round(n, 2)
        except (TypeError, ValueError):
            raise ValidationError(f"'{field_name}' must be a positive number")

    @staticmethod
    def validate_business_name(name):
        return Validator.require_string(name, 'business_name', min_len=2, max_len=100)

    @staticmethod
    def validate_username(username):
        cleaned = Validator.sanitize(str(username) if username else '', 100)
        if not cleaned:
            raise ValidationError("'username' is required")
        if '@' not in cleaned or '.' not in cleaned.split('@')[-1]:
            raise ValidationError("'username' must be a valid email address")
        return cleaned.lower()

    @staticmethod
    def validate_password(password, field_name='password'):
        if not password or not isinstance(password, str):
            raise ValidationError(f"'{field_name}' is required")
        if len(password) < 6:
            raise ValidationError(f"'{field_name}' must be at least 6 characters")
        if len(password) > 72:
            raise ValidationError(f"'{field_name}' must be at most 72 characters")
        return password

    @staticmethod
    def hash_password(password):
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def validate_role(role):
        if role not in Validator.VALID_ROLES:
            raise ValidationError(f"'role' must be one of: {', '.join(sorted(Validator.VALID_ROLES))}")
        return role

    @staticmethod
    def validate_customer(data):
        return {
            'name':  Validator.require_string(data.get('name'),  'name',  min_len=2, max_len=100),
            'email': Validator.validate_username(data.get('email')),
            'phone': Validator.sanitize(data.get('phone', ''), 20),
        }

    @staticmethod
    def validate_product(data):
        name     = Validator.require_string(data.get('name'),     'name',     min_len=2, max_len=200)
        supplier = Validator.require_string(data.get('supplier'), 'supplier', min_len=1, max_len=100)
        price    = Validator.require_positive_decimal(data.get('price'), 'price')
        stock    = Validator.require_positive_int(data.get('stock_quantity'), 'stock_quantity')
        expiry   = data.get('expiry_date')
        if expiry:
            expiry = Validator.sanitize(str(expiry), 10)
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', expiry):
                raise ValidationError("'expiry_date' must be in YYYY-MM-DD format")
        return {
            'name':           name,
            'supplier':       supplier,
            'price':          price,
            'stock_quantity': stock,
            'expiry_date':    expiry,
        }

    @staticmethod
    def validate_order_status(status):
        if status not in Validator.VALID_ORDER_STATUSES:
            raise ValidationError(f"'status' must be one of: {', '.join(sorted(Validator.VALID_ORDER_STATUSES))}")
        return status

    @staticmethod
    def validate_order_items(items):
        if not items or not isinstance(items, list):
            raise ValidationError("Order must contain at least one item")
        validated = []
        for i, item in enumerate(items):
            product_id = Validator.require_positive_int(item.get('product_id'), f'items[{i}].product_id')
            quantity   = Validator.require_positive_int(item.get('quantity'),   f'items[{i}].quantity')
            validated.append({'product_id': product_id, 'quantity': quantity})
        return validated

    @staticmethod
    def validate_business_unit_id(business_unit_id):
        return Validator.require_positive_int(business_unit_id, 'business_unit_id')


logger = DBLogger()

logger.info("=" * 60)
logger.info("DB-SQUARED STARTING")
logger.info(f"Primary: {PRIMARY_DB_HOST}:{PRIMARY_DB_PORT} (AWS RDS)")
logger.info("=" * 60)


# ============================================================
# CONNECTION MANAGER
# ============================================================

class ConnectionManager:
    """
    Simple connection pool for the RDS primary.
    Provides get_connection() and return_connection().
    """

    def __init__(self):
        self._pool      = []
        self._max_size  = 10
        self._lock      = threading.Lock()
        self._config    = {
            'host':               PRIMARY_DB_HOST,
            'port':               PRIMARY_DB_PORT,
            'user':               PRIMARY_DB_USER,
            'password':           PRIMARY_DB_PASSWORD,
            'database':           PRIMARY_DB_NAME,
            'connection_timeout': 10,
            'autocommit':         True,
        }

        # Verify we can connect at startup
        self._verify_connection()

        # Start background monitor
        self._monitor_running = True
        t = threading.Thread(target=self._monitor_loop, daemon=True, name="DB-Monitor")
        t.start()

    def _verify_connection(self):
        """Verify RDS is reachable at startup. Raises on failure."""
        logger.info("Connecting to RDS...")
        for attempt in range(3):
            try:
                conn = mysql.connector.connect(**self._config)
                cursor = conn.cursor()
                cursor.execute("SELECT @@version")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                logger.info(f"RDS connected — MySQL {version}")
                return
            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(3)

        raise Exception(
            f"Cannot connect to RDS at {PRIMARY_DB_HOST}:{PRIMARY_DB_PORT}. "
            f"Check credentials and PRIMARY_DB in .env."
        )

    def _new_connection(self):
        """Open a fresh connection to RDS."""
        try:
            conn = mysql.connector.connect(**self._config)
            return conn
        except Exception as e:
            logger.error(f"Failed to open new connection: {e}")
            return None

    def get_connection(self):
        """
        Get a connection from the pool.
        Creates a new one if the pool is empty or all connections are stale.
        """
        with self._lock:
            while self._pool:
                conn = self._pool.pop()
                try:
                    conn.ping(reconnect=False, attempts=1, delay=0)
                    return conn
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass

        conn = self._new_connection()
        if conn:
            return conn

        raise Exception("Cannot connect to database. Check RDS status.")

    def return_connection(self, conn):
        """Return a connection to the pool."""
        if not conn:
            return
        try:
            if not conn.is_connected():
                conn.close()
                return
            with self._lock:
                if len(self._pool) < self._max_size:
                    self._pool.append(conn)
                else:
                    conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def get_status(self):
        """Return a status dict for the health endpoint."""
        try:
            conn   = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT @@version, @@read_only")
            version, read_only = cursor.fetchone()
            cursor.close()
            self.return_connection(conn)
            return {
                'healthy':  True,
                'host':     PRIMARY_DB_HOST,
                'port':     PRIMARY_DB_PORT,
                'version':  version,
                'writable': read_only == 0,
                'problem':  None,
            }
        except Exception as e:
            return {
                'healthy':  False,
                'host':     PRIMARY_DB_HOST,
                'port':     PRIMARY_DB_PORT,
                'version':  None,
                'writable': False,
                'problem':  str(e),
            }

    def _monitor_loop(self):
        """Ping RDS every 30s and log status."""
        logger.info("DB monitor started")
        check_num = 0
        while self._monitor_running:
            try:
                check_num += 1
                status = self.get_status()
                icon   = "✅" if status['healthy'] else "❌"
                logger.info(
                    f"Check #{check_num} | RDS {icon} | "
                    f"{'UP' if status['healthy'] else 'DOWN'} | "
                    f"Writable: {status['writable']}"
                )
            except Exception as e:
                logger.error(f"Monitor error: {e}")

            for _ in range(30):
                if not self._monitor_running:
                    break
                time.sleep(1)

        logger.info("DB monitor stopped")

    def stop_monitor(self):
        self._monitor_running = False


# ============================================================
# GLOBAL INSTANCE + PUBLIC INTERFACE
# ============================================================

_manager = ConnectionManager()


def get_write_connection():
    return _manager.get_connection()


def get_read_connection():
    return _manager.get_connection()


def return_connection(conn):
    _manager.return_connection(conn)


def get_status():
    status = _manager.get_status()
    # Keep same shape as before so app.py health endpoint doesn't break
    return {
        'current_master':       'primary_db',
        'running_on_rds':       True,
        'failover_in_progress': False,
        'failback_in_progress': False,
        'primary': {
            'host':        status['host'],
            'port':        status['port'],
            'healthy':     status['healthy'],
            'writable':    status['writable'],
            'gtid_ok':     True,
            'replicating': False,
            'lag':         None,
            'problem':     status['problem'],
        },
        'secondary': {
            'host':        'N/A',
            'port':        0,
            'healthy':     False,
            'writable':    False,
            'gtid_ok':     False,
            'replicating': False,
            'lag':         None,
            'problem':     'Secondary not configured',
        },
        'stats':          {'failovers': 0, 'failbacks': 0},
        'recent_history': [],
        'timestamp':      time.time(),
    }


def manual_failover():
    return {'success': False, 'message': 'Failover not available — running primary-only mode'}


def manual_failback():
    return {'success': False, 'message': 'Failback not available — running primary-only mode'}


def stop_monitor():
    _manager.stop_monitor()


if __name__ == "__main__":
    print("DB-Squared Connection Manager")
    print(f"Primary: {PRIMARY_DB_HOST}:{PRIMARY_DB_PORT} (AWS RDS)")