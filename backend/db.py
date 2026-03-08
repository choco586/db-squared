# backend/db.py
# GTID FAILOVER MANAGER
# Primary: AWS RDS (eu-north-1) — permanent source of truth
# Secondary: Docker MySQL — standby replica, promoted on RDS failure,
#            demoted and re-synced automatically when RDS recovers.

import mysql.connector
from mysql.connector import Error
from config import (
    PRIMARY_DB_HOST, PRIMARY_DB_PORT, PRIMARY_DB_USER,
    PRIMARY_DB_PASSWORD, PRIMARY_DB_NAME,
    SECONDARY_DB_HOST, SECONDARY_DB_PORT, SECONDARY_DB_USER,
    SECONDARY_DB_PASSWORD, SECONDARY_DB_NAME,
    REPLICA_USER, REPLICA_PASSWORD,
    PRIMARY_REPLICATION_HOST
)
import time
import threading
import logging
import random
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

# Main log — INFO level, daily monitoring
main_logger = logging.getLogger('gtid.main')
main_logger.setLevel(logging.INFO)
main_handler = RotatingFileHandler(
    f'{log_dir}/gtid_main.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
)
main_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
main_logger.addHandler(main_handler)

# Detail log — DEBUG level, troubleshooting
detail_logger = logging.getLogger('gtid.detail')
detail_logger.setLevel(logging.DEBUG)
detail_handler = RotatingFileHandler(
    f'{log_dir}/gtid_detail.log', maxBytes=20*1024*1024, backupCount=3, encoding='utf-8'
)
detail_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(threadName)-15s | %(levelname)-8s | %(funcName)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
detail_logger.addHandler(detail_handler)

# Error log — ERROR level, critical issues only
error_logger = logging.getLogger('gtid.error')
error_logger.setLevel(logging.ERROR)
error_handler = RotatingFileHandler(
    f'{log_dir}/gtid_errors.log', maxBytes=5*1024*1024, backupCount=10, encoding='utf-8'
)
error_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s\nFile: %(pathname)s:%(lineno)d\n',
    datefmt='%Y-%m-%d %H:%M:%S'
))
error_logger.addHandler(error_handler)

# Console log
console_logger = logging.getLogger('gtid.console')
console_logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S'
))
console_logger.addHandler(console_handler)


class GTIDLogger:
    """Unified logger — writes to all appropriate targets."""

    @staticmethod
    def info(msg):
        main_logger.info(msg)
        console_logger.info(msg)

    @staticmethod
    def debug(msg):
        detail_logger.debug(msg)

    @staticmethod
    def warning(msg):
        main_logger.warning(msg)
        console_logger.warning(msg)
        error_logger.warning(msg)

    @staticmethod
    def error(msg, exc_info=False):
        main_logger.error(msg)
        console_logger.error(msg)
        error_logger.error(msg, exc_info=exc_info)

    @staticmethod
    def critical(msg):
        main_logger.critical(msg)
        console_logger.critical(msg)
        error_logger.critical(msg, exc_info=True)



# ============================================================
# INPUT VALIDATION
# Kept separate from the failover logic so routes can import
# individual validators without pulling in the whole manager.
# ============================================================

class ValidationError(Exception):
    """Raised by validators when input is invalid."""
    pass


class Validator:
    """
    Input validation and sanitization for all route handlers.

    Design notes:
    - We use parameterized queries throughout (no string interpolation),
      so SQL injection is already prevented at the DB layer.
    - sanitize() is a second line of defence: strips characters that
      have no business in names/emails/prices even if the query is safe.
    - Every validate_* method returns the cleaned value on success or
      raises ValidationError with a human-readable message.
    """

    _DANGEROUS = ["'", '"', ';', '--', '/*', '*/']
    VALID_ORDER_STATUSES = {'placed', 'processing', 'completed', 'cancelled'}
    VALID_ROLES = {'admin', 'employee'}

    @staticmethod
    def sanitize(text, max_len=255):
        """Strip dangerous SQL characters and truncate."""
        if not text or not isinstance(text, str):
            return ""
        cleaned = text
        for char in Validator._DANGEROUS:
            cleaned = cleaned.replace(char, '')
        cleaned = cleaned.strip()
        return cleaned[:max_len]

    @staticmethod
    def require_string(value, field_name, min_len=1, max_len=255):
        """Validate a required string field."""
        cleaned = Validator.sanitize(str(value) if value is not None else '', max_len)
        if len(cleaned) < min_len:
            raise ValidationError(
                f"'{field_name}' must be at least {min_len} character(s)"
            )
        return cleaned

    @staticmethod
    def require_positive_int(value, field_name):
        """Validate a required positive integer."""
        try:
            n = int(value)
            if n <= 0:
                raise ValueError
            return n
        except (TypeError, ValueError):
            raise ValidationError(f"'{field_name}' must be a positive integer")

    @staticmethod
    def require_positive_decimal(value, field_name):
        """Validate a required positive decimal (e.g. price)."""
        try:
            n = float(value)
            if n < 0:
                raise ValueError
            return round(n, 2)
        except (TypeError, ValueError):
            raise ValidationError(f"'{field_name}' must be a positive number")

    @staticmethod
    def validate_business_name(name):
        """Business names: 2-100 chars, no dangerous chars."""
        return Validator.require_string(name, 'business_name', min_len=2, max_len=100)

    @staticmethod
    def validate_username(username):
        """Usernames are emails: must contain @ and a dot after @. Stored lowercase."""
        cleaned = Validator.sanitize(str(username) if username else '', 100)
        if not cleaned:
            raise ValidationError("'username' is required")
        if '@' not in cleaned or '.' not in cleaned.split('@')[-1]:
            raise ValidationError("'username' must be a valid email address")
        return cleaned.lower()

    @staticmethod
    def validate_password(password, field_name='password'):
        """Passwords: 6-72 chars. Not sanitized — just length check."""
        if not password or not isinstance(password, str):
            raise ValidationError(f"'{field_name}' is required")
        if len(password) < 6:
            raise ValidationError(f"'{field_name}' must be at least 6 characters")
        if len(password) > 72:
            raise ValidationError(f"'{field_name}' must be at most 72 characters")
        return password

    @staticmethod
    def hash_password(password):
        """SHA-256 hash. Input must already be validated."""
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def validate_role(role):
        """Role must be one of the allowed values."""
        if role not in Validator.VALID_ROLES:
            raise ValidationError(
                f"'role' must be one of: {', '.join(sorted(Validator.VALID_ROLES))}"
            )
        return role

    @staticmethod
    def validate_customer(data):
        """Validate customer create/update payload."""
        return {
            'name':  Validator.require_string(data.get('name'),  'name',  min_len=2, max_len=100),
            'email': Validator.validate_username(data.get('email')),
            'phone': Validator.sanitize(data.get('phone', ''), 20),
        }

    @staticmethod
    def validate_product(data):
        """Validate product create/update payload."""
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
        """Validate order status value."""
        if status not in Validator.VALID_ORDER_STATUSES:
            raise ValidationError(
                f"'status' must be one of: {', '.join(sorted(Validator.VALID_ORDER_STATUSES))}"
            )
        return status

    @staticmethod
    def validate_order_items(items):
        """Validate a list of order item dicts. Each needs product_id and quantity."""
        if not items or not isinstance(items, list):
            raise ValidationError("Order must contain at least one item")
        validated = []
        for i, item in enumerate(items):
            product_id = Validator.require_positive_int(
                item.get('product_id'), f'items[{i}].product_id'
            )
            quantity = Validator.require_positive_int(
                item.get('quantity'), f'items[{i}].quantity'
            )
            validated.append({'product_id': product_id, 'quantity': quantity})
        return validated

    @staticmethod
    def validate_business_unit_id(business_unit_id):
        """Every route that touches business data must validate this."""
        return Validator.require_positive_int(business_unit_id, 'business_unit_id')


logger = GTIDLogger()

logger.info("=" * 60)
logger.info("DB-SQUARED GTID FAILOVER MANAGER STARTING")
logger.info(f"Primary  : {PRIMARY_DB_HOST}:{PRIMARY_DB_PORT}  (AWS RDS)")
logger.info(f"Secondary: {SECONDARY_DB_HOST}:{SECONDARY_DB_PORT}  (Docker MySQL)")
logger.info("=" * 60)


# ============================================================
# FAILOVER MANAGER
# ============================================================

class GTIDFailoverManager:
    """
    Manages RDS (primary) → Docker MySQL (secondary) failover.

    Normal state  : RDS is writable master, Docker is read-only replica.
    Failover state: RDS unreachable → Docker promoted to writable master.
    Failback      : RDS recovers → Docker demoted, missed GTIDs re-synced,
                    Docker resumes as read-only replica of RDS.
    """

    # --------------------------------------------------------
    # Physical server names used throughout the class
    # --------------------------------------------------------
    PRIMARY   = 'primary_db'    # AWS RDS — never changes meaning
    SECONDARY = 'secondary_db'  # Docker MySQL — never changes meaning

    def __init__(self):
        # Physical server configs
        self.primary_config = {
            'host': PRIMARY_DB_HOST,
            'port': PRIMARY_DB_PORT,
            'user': PRIMARY_DB_USER,
            'password': PRIMARY_DB_PASSWORD,
            'database': PRIMARY_DB_NAME,
            'replica_user': REPLICA_USER,
            'replica_password': REPLICA_PASSWORD,
            'replication_host': PRIMARY_REPLICATION_HOST,
        }
        self.secondary_config = {
            'host': SECONDARY_DB_HOST,
            'port': SECONDARY_DB_PORT,
            'user': SECONDARY_DB_USER,
            'password': SECONDARY_DB_PASSWORD,
            'database': SECONDARY_DB_NAME,
            'replica_user': REPLICA_USER,
            'replica_password': REPLICA_PASSWORD,
            'replication_host': SECONDARY_DB_HOST,
        }

        # Connection pools (keyed by physical server name)
        self._pools = {self.PRIMARY: [], self.SECONDARY: []}
        self._max_pool_size = 10

        # Logical routing:
        #   'primary_db'   → RDS is master  (normal state)
        #   'secondary_db' → Docker is master (failover state)
        self._current_master = self.PRIMARY

        # UUIDs fetched at startup for pool contamination checks
        self._uuids = {self.PRIMARY: None, self.SECONDARY: None}

        # Failure tracking
        self._failure_count = {self.PRIMARY: 0, self.SECONDARY: 0}
        self._max_failures = 3

        # Dead-server cooldown (seconds) — avoids hammering a dead server
        self._dead_until = {}

        # Failover / failback control
        self._failover_lock = threading.Lock()
        self._failover_in_progress = False
        self._failover_triggered = False
        self._failover_trigger_lock = threading.Lock()
        self._last_failover_time = 0
        self._failover_cooldown = 60          # seconds between auto-failovers
        self._last_master_change_time = time.time()
        self._enforcer_cooldown = 30          # seconds enforcer waits after a switch

        # Failback control
        self._failback_in_progress = False
        self._primary_recovery_detected = False

        # Replication restart flag
        self._replication_restart_needed = False

        # Stats and history
        self.stats = {
            'failovers': 0, 'failbacks': 0,
            'auto_recoveries': 0, 'errors': 0,
        }
        self.history = []

        # Fetch UUIDs before starting monitor
        self._fetch_uuids()
        for attempt in range(3):
            if self._uuids[self.PRIMARY] and self._uuids[self.SECONDARY]:
                break
            logger.warning(f"UUID fetch attempt {attempt+1}/3 incomplete, retrying in 2s...")
            time.sleep(2)
            self._fetch_uuids()

        # Start background monitor
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="GTID-Monitor"
        )
        self._monitor_thread.start()

        logger.info(f"Active master: {self._current_master}")

    # --------------------------------------------------------
    # CONFIG HELPER
    # --------------------------------------------------------

    def _config(self, server):
        """Return config dict for a physical server name."""
        if server == self.PRIMARY:
            return self.primary_config
        return self.secondary_config

    # --------------------------------------------------------
    # LOW-LEVEL CONNECTIONS
    # --------------------------------------------------------

    def _raw_connect(self, server, timeout=5):
        """
        Open a raw TCP connection to a physical server.
        No UUID check — used only during bootstrap and failover steps.
        """
        cfg = self._config(server)
        try:
            conn = mysql.connector.connect(
                host=cfg['host'],
                port=cfg['port'],
                user=cfg['user'],
                password=cfg['password'],
                database=cfg['database'],
                connection_timeout=timeout,
                autocommit=True
            )
            return conn
        except Exception as e:
            logger.debug(f"Raw connect to {server} failed: {e}")
            return None

    def _verified_connect(self, server, timeout=5):
        """
        Open a connection to a physical server and verify its UUID.
        Returns None if UUID mismatches or connection fails.
        """
        cfg = self._config(server)
        expected = self._uuids.get(server)

        try:
            conn = mysql.connector.connect(
                host=cfg['host'],
                port=cfg['port'],
                user=cfg['user'],
                password=cfg['password'],
                database=cfg['database'],
                connection_timeout=timeout,
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("SELECT @@server_uuid")
            actual = cursor.fetchone()[0]
            cursor.close()

            if expected and actual != expected:
                logger.error(
                    f"UUID mismatch on {server}! "
                    f"Expected {expected[:8]}... got {actual[:8]}..."
                )
                conn.close()
                return None

            if not expected:
                # Store UUID if we didn't have it yet
                self._uuids[server] = actual
                logger.info(f"Stored UUID for {server}: {actual}")

            return conn
        except Exception as e:
            logger.debug(f"Verified connect to {server} failed: {e}")
            return None

    def _fetch_uuids(self):
        """Fetch and store UUIDs for both servers at startup."""
        logger.info("Fetching server UUIDs...")
        for server in (self.PRIMARY, self.SECONDARY):
            try:
                conn = self._raw_connect(server, timeout=5)
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT @@GLOBAL.server_uuid")
                    self._uuids[server] = cursor.fetchone()[0]
                    cursor.close()
                    conn.close()
                    logger.info(f"  {server}: {self._uuids[server]}")
            except Exception as e:
                logger.warning(f"  Could not fetch UUID for {server}: {e}")

    # --------------------------------------------------------
    # POOLED CONNECTIONS (PUBLIC INTERFACE USES THESE)
    # --------------------------------------------------------

    def _get_pooled_connection(self, server):
        """
        Get a UUID-verified connection from the pool for a physical server.
        Creates a new one if the pool is empty or all connections are stale.
        """
        # Respect dead-server cooldown
        dead_until = self._dead_until.get(server, 0)
        if time.time() < dead_until:
            remaining = int(dead_until - time.time())
            logger.debug(f"{server} in dead cooldown ({remaining}s remaining)")
            return None

        pool = self._pools[server]
        expected_uuid = self._uuids.get(server)
        conn = None

        while pool and not conn:
            candidate = pool.pop()
            try:
                candidate.ping(reconnect=False, attempts=1, delay=0)
                cursor = candidate.cursor()
                cursor.execute("SELECT @@server_uuid")
                actual = cursor.fetchone()[0]
                cursor.close()
                if actual == expected_uuid:
                    conn = candidate
                else:
                    logger.warning(f"Contaminated connection removed from {server} pool")
                    candidate.close()
            except Exception:
                try:
                    candidate.close()
                except Exception:
                    pass

        if not conn:
            conn = self._verified_connect(server)
            if not conn:
                self._failure_count[server] += 1
                if self._failure_count[server] >= self._max_failures:
                    self._dead_until[server] = time.time() + 30
                    logger.warning(
                        f"{server} marked dead for 30s "
                        f"({self._failure_count[server]} consecutive failures)"
                    )
                return None

        # Good connection — reset failure counter
        if self._failure_count[server] > 0:
            logger.info(f"{server} connection restored — resetting failure counter")
            self._failure_count[server] = 0

        return conn

    def _return_to_pool(self, server, conn):
        """Return a connection to the physical server pool."""
        if not conn:
            return
        try:
            if not conn.is_connected():
                conn.close()
                return
            cursor = conn.cursor()
            cursor.execute("SELECT @@server_uuid")
            actual = cursor.fetchone()[0]
            cursor.close()

            # Route to correct pool by UUID
            target_server = None
            for s, uuid in self._uuids.items():
                if uuid and uuid == actual:
                    target_server = s
                    break

            if target_server and len(self._pools[target_server]) < self._max_pool_size:
                self._pools[target_server].append(conn)
            else:
                conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    # --------------------------------------------------------
    # HEALTH CHECK
    # --------------------------------------------------------

    def _check_server(self, server):
        """
        Check health of a physical server.
        Returns a dict with: healthy, writable, gtid_ok, replicating, problem, lag
        """
        result = {
            'server': server,
            'healthy': False,
            'writable': False,
            'gtid_ok': False,
            'replicating': False,
            'lag': None,
            'problem': None,
        }

        conn = self._get_pooled_connection(server)
        if not conn:
            result['problem'] = 'cannot_connect'
            return result

        try:
            cursor = conn.cursor()

            # Connectivity
            cursor.execute("SELECT 1")
            cursor.fetchone()
            result['healthy'] = True

            # Writable?
            cursor.execute("SELECT @@read_only, @@super_read_only")
            ro, sro = cursor.fetchone()
            result['writable'] = (ro == 0 and sro == 0)

            # GTID on?
            cursor.execute("SELECT @@GLOBAL.gtid_mode")
            gtid_mode = cursor.fetchone()[0]
            result['gtid_ok'] = (gtid_mode == 'ON')
            if not result['gtid_ok']:
                result['problem'] = 'gtid_off'

            # Replication status (only meaningful on secondary)
            cursor.execute("SHOW REPLICA STATUS")
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                col = dict(zip(cols, row))
                io_ok  = col.get('Replica_IO_Running', 'No') == 'Yes'
                sql_ok = col.get('Replica_SQL_Running', 'No') == 'Yes'
                result['replicating'] = io_ok and sql_ok
                result['lag'] = col.get('Seconds_Behind_Source')
                if not result['replicating']:
                    result['problem'] = result['problem'] or 'replication_broken'

            cursor.close()

        except Exception as e:
            result['healthy'] = False
            result['problem'] = f'exception: {type(e).__name__}'
            logger.debug(f"Health check error on {server}: {e}")
        finally:
            self._return_to_pool(server, conn)

        return result

    # --------------------------------------------------------
    # FENCING (SPLIT-BRAIN PREVENTION)
    # --------------------------------------------------------

    def _fence(self, server):
        """
        Set a physical server to read-only and kill active user connections.
        Called on the old master before promoting the new master.
        """
        logger.info(f"Fencing {server} (setting read-only, killing connections)...")
        conn = self._raw_connect(server, timeout=5)
        if not conn:
            logger.warning(f"Cannot connect to {server} to fence — assuming dead, proceeding")
            return

        try:
            cursor = conn.cursor()
            cursor.execute("SET GLOBAL read_only = ON")
            cursor.execute("SET GLOBAL super_read_only = ON")

            # Verify
            cursor.execute("SELECT @@read_only, @@super_read_only")
            ro, sro = cursor.fetchone()
            if ro != 1 or sro != 1:
                logger.error(f"Fence verify failed on {server} — retrying")
                cursor.execute("SET GLOBAL read_only = ON")
                cursor.execute("SET GLOBAL super_read_only = ON")

            # Kill user connections
            cursor.execute("""
                SELECT id FROM information_schema.processlist
                WHERE USER NOT IN ('system user', 'event_scheduler', 'rdsadmin')
                AND COMMAND NOT IN ('Sleep', 'Binlog Dump')
                AND ID != CONNECTION_ID()
            """)
            for (cid,) in cursor.fetchall():
                try:
                    cursor.execute(f"KILL {cid}")
                except Exception:
                    pass

            cursor.close()
            logger.info(f"  {server} successfully fenced")
        except Exception as e:
            logger.warning(f"Partial fence on {server}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --------------------------------------------------------
    # FAILOVER: PRIMARY → SECONDARY
    # --------------------------------------------------------

    def _do_failover(self):
        """
        Promote secondary (Docker) to master when primary (RDS) is down.
        Runs in a background thread.
        """
        if not self._failover_lock.acquire(timeout=10):
            logger.warning("Could not acquire failover lock — skipping")
            return

        try:
            if self._failover_in_progress:
                logger.warning("Failover already in progress — skipping")
                return
            self._failover_in_progress = True

            logger.info("=" * 60)
            logger.info("FAILOVER STARTING: primary_db (RDS) → secondary_db (Docker)")
            logger.info("=" * 60)

            # Step 1 — Verify secondary is healthy
            secondary_health = self._check_server(self.SECONDARY)
            if not secondary_health['healthy'] or not secondary_health['gtid_ok']:
                logger.error(
                    f"Failover aborted — secondary is not ready: "
                    f"{secondary_health.get('problem')}"
                )
                return

            # Step 2 — Fence primary (best-effort; it may be dead)
            self._fence(self.PRIMARY)

            # Step 3 — Stop replication on secondary and make it writable
            conn = self._raw_connect(self.SECONDARY)
            if not conn:
                logger.error("Cannot connect to secondary — failover aborted")
                return
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute("STOP REPLICA")
                    cursor.execute("RESET REPLICA ALL")
                except Exception as e:
                    logger.debug(f"STOP/RESET REPLICA: {e}")
                cursor.execute("SET GLOBAL read_only = OFF")
                cursor.execute("SET GLOBAL super_read_only = OFF")
                cursor.close()
            finally:
                conn.close()

            # Step 4 — Switch logical routing
            self._current_master = self.SECONDARY
            self._last_master_change_time = time.time()
            self.stats['failovers'] += 1

            self.history.append({
                'time': time.time(),
                'event': 'failover',
                'from': self.PRIMARY,
                'to': self.SECONDARY,
            })

            logger.info("=" * 60)
            logger.info("FAILOVER COMPLETE — secondary_db (Docker) is now master")
            logger.info("Writes are being served from Docker MySQL")
            logger.info("Monitoring for RDS recovery to trigger auto-failback")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Failover failed: {e}", exc_info=True)
        finally:
            self._failover_in_progress = False
            with self._failover_trigger_lock:
                self._failover_triggered = False
            self._failover_lock.release()

    # --------------------------------------------------------
    # FAILBACK: SECONDARY → PRIMARY (auto, when RDS recovers)
    # --------------------------------------------------------

    def _do_failback(self):
        """
        Demote secondary (Docker) back to replica when primary (RDS) recovers.
        1. Fence secondary (make read-only).
        2. Wait for secondary to finish applying any in-flight transactions.
        3. Make primary writable.
        4. Point secondary at primary as replica (CHANGE REPLICATION SOURCE).
        5. Switch logical routing back to primary.
        Runs in a background thread.
        """
        if not self._failover_lock.acquire(timeout=10):
            logger.warning("Could not acquire lock for failback — skipping")
            return

        try:
            if self._failback_in_progress or self._failover_in_progress:
                return
            self._failback_in_progress = True

            logger.info("=" * 60)
            logger.info("FAILBACK STARTING: secondary_db (Docker) → primary_db (RDS)")
            logger.info("=" * 60)

            # Step 1 — Verify primary is really back and healthy
            primary_health = self._check_server(self.PRIMARY)
            if not primary_health['healthy'] or not primary_health['gtid_ok']:
                logger.warning(
                    f"Failback aborted — primary not ready yet: "
                    f"{primary_health.get('problem')}"
                )
                self._primary_recovery_detected = False
                return

            # Step 2 — Fence secondary (stop new writes)
            self._fence(self.SECONDARY)

            # Step 3 — Wait for secondary to apply all pending transactions (max 30s)
            logger.info("Waiting for secondary to apply all pending transactions...")
            deadline = time.time() + 30
            while time.time() < deadline:
                conn = self._raw_connect(self.SECONDARY)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SHOW REPLICA STATUS")
                        row = cursor.fetchone()
                        cursor.close()
                        if not row:
                            break  # No replication configured — safe to proceed
                        cols = [d[0] for d in cursor.description]
                        col = dict(zip(cols, row))
                        lag = col.get('Seconds_Behind_Source', 0)
                        if lag is not None and int(lag) == 0:
                            logger.info("Secondary has caught up (lag = 0)")
                            break
                        logger.debug(f"Secondary still has lag: {lag}s")
                    except Exception:
                        break
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                time.sleep(2)

            # Step 4 — Make primary writable
            logger.info("Making primary (RDS) writable...")
            conn = self._raw_connect(self.PRIMARY)
            if not conn:
                logger.error("Cannot connect to primary — failback aborted")
                self._primary_recovery_detected = False
                return
            try:
                cursor = conn.cursor()
                cursor.execute("SET GLOBAL read_only = OFF")
                cursor.execute("SET GLOBAL super_read_only = OFF")
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                cursor.close()
                if ro != 0 or sro != 0:
                    logger.error(f"Primary still read-only after attempt (ro={ro}, sro={sro})")
                    return
                logger.info("Primary (RDS) is now writable")
            finally:
                conn.close()

            # Step 5 — Switch routing back to primary BEFORE re-enabling replication
            # (so new app writes go to RDS immediately)
            self._current_master = self.PRIMARY
            self._last_master_change_time = time.time()
            self.stats['failbacks'] += 1

            self.history.append({
                'time': time.time(),
                'event': 'failback',
                'from': self.SECONDARY,
                'to': self.PRIMARY,
            })

            logger.info("Routing switched back to primary (RDS)")

            # Step 6 — Re-configure secondary as replica of primary (background)
            logger.info("Re-configuring Docker secondary as replica of RDS (background)...")
            threading.Thread(
                target=self._configure_secondary_as_replica,
                daemon=True,
                name="Failback-Reconfig"
            ).start()

            logger.info("=" * 60)
            logger.info("FAILBACK COMPLETE — primary_db (RDS) is master again")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Failback failed: {e}", exc_info=True)
            # If failback failed mid-way, try to stay on secondary safely
        finally:
            self._failback_in_progress = False
            self._primary_recovery_detected = False
            self._failover_lock.release()

    # --------------------------------------------------------
    # REPLICATION SETUP
    # --------------------------------------------------------

    def _configure_secondary_as_replica(self):
        """
        Point secondary (Docker) at primary (RDS) and start replication.
        Called after failback, and also during initial health-fix.
        """
        logger.info("Configuring secondary as replica of primary...")
        time.sleep(2)

        conn = self._raw_connect(self.SECONDARY)
        if not conn:
            logger.error("Cannot connect to secondary to configure replication")
            self._replication_restart_needed = True
            return

        try:
            cursor = conn.cursor()
            cfg = self.primary_config

            try:
                cursor.execute("STOP REPLICA")
            except Exception:
                pass
            try:
                cursor.execute("RESET REPLICA ALL")
            except Exception:
                pass

            # Ensure secondary is read-only
            cursor.execute("SET GLOBAL read_only = ON")
            cursor.execute("SET GLOBAL super_read_only = ON")

            # Verify replica_user can reach primary before configuring
            try:
                test = mysql.connector.connect(
                    host=cfg['replication_host'],
                    port=cfg['port'],
                    user=cfg['replica_user'],
                    password=cfg['replica_password'],
                    connection_timeout=5
                )
                test.close()
                logger.info(f"replica_user can reach primary at {cfg['replication_host']}:{cfg['port']}")
            except Exception as e:
                logger.error(f"replica_user CANNOT reach primary: {e}")
                logger.error("Replication will not start — fix replica_user credentials on RDS")
                self._replication_restart_needed = True
                cursor.close()
                return

            cursor.execute("""
                CHANGE REPLICATION SOURCE TO
                SOURCE_HOST=%s,
                SOURCE_PORT=%s,
                SOURCE_USER=%s,
                SOURCE_PASSWORD=%s,
                SOURCE_AUTO_POSITION=1,
                SOURCE_SSL=0,
                GET_SOURCE_PUBLIC_KEY=1
            """, (
                cfg['replication_host'], cfg['port'],
                cfg['replica_user'], cfg['replica_password']
            ))

            cursor.execute("START REPLICA")
            time.sleep(3)

            # Verify
            cursor.execute("SHOW REPLICA STATUS")
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                col = dict(zip(cols, row))
                io_ok  = col.get('Replica_IO_Running', 'No') == 'Yes'
                sql_ok = col.get('Replica_SQL_Running', 'No') == 'Yes'
                if io_ok and sql_ok:
                    logger.info("Replication is running — secondary is syncing from primary")
                    self._replication_restart_needed = False
                else:
                    err = col.get('Last_IO_Error') or col.get('Last_SQL_Error') or 'unknown'
                    logger.error(f"Replication NOT running after configuration: {err}")
                    self._replication_restart_needed = True
            else:
                logger.warning("No SHOW REPLICA STATUS output — replication may not be configured")

            cursor.close()

        except Exception as e:
            logger.error(f"Error configuring replication: {e}", exc_info=True)
            self._replication_restart_needed = True
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --------------------------------------------------------
    # ROLE ENFORCEMENT
    # --------------------------------------------------------

    def _enforce_roles(self):
        """
        Ensure the current master is writable and the current replica is read-only.
        Runs every monitor cycle. Skips during failover cooldown.
        """
        if self._failover_in_progress or self._failback_in_progress:
            return

        since_change = time.time() - self._last_master_change_time
        if since_change < self._enforcer_cooldown:
            return

        # Any failover/reconfig threads still running?
        for t in threading.enumerate():
            if t.name and any(k in t.name for k in ('Failover', 'Failback', 'Reconfig')):
                if t.is_alive():
                    return

        master_server  = self._current_master
        replica_server = self.SECONDARY if master_server == self.PRIMARY else self.PRIMARY

        # Master must be writable
        conn = self._raw_connect(master_server)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                if ro == 1 or sro == 1:
                    logger.warning(f"Enforcer: {master_server} is read-only — fixing")
                    cursor.execute("SET GLOBAL read_only = OFF")
                    cursor.execute("SET GLOBAL super_read_only = OFF")
                    logger.info(f"Enforcer: {master_server} made writable")
                cursor.close()
            except Exception as e:
                logger.debug(f"Enforcer master check error: {e}")
            finally:
                conn.close()

        # Replica must be read-only
        conn = self._raw_connect(replica_server)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                if ro == 0 or sro == 0:
                    logger.critical(
                        f"SPLIT-BRAIN RISK: {replica_server} is writable but should be replica! Fixing..."
                    )
                    cursor.execute("SET GLOBAL read_only = ON")
                    cursor.execute("SET GLOBAL super_read_only = ON")
                    logger.info(f"Enforcer: {replica_server} forced to read-only")
                cursor.close()
            except Exception as e:
                logger.debug(f"Enforcer replica check error: {e}")
            finally:
                conn.close()

    # --------------------------------------------------------
    # MONITOR LOOP
    # --------------------------------------------------------

    def _monitor_loop(self):
        """
        Background thread. Every 10 seconds:
          1. Health-check both servers.
          2. If master is down → trigger failover.
          3. If master is secondary AND primary has recovered → trigger failback.
          4. Fix broken replication.
          5. Enforce roles.
        """
        logger.info("Monitor started")
        check_num = 0

        while self._monitor_running:
            try:
                check_num += 1
                primary_health   = self._check_server(self.PRIMARY)
                secondary_health = self._check_server(self.SECONDARY)

                master_is_primary = (self._current_master == self.PRIMARY)

                # ── FAILOVER TRIGGER ──────────────────────────────
                if master_is_primary:
                    primary_down = (
                        not primary_health['healthy'] or
                        self._failure_count[self.PRIMARY] >= self._max_failures
                    )
                    if (primary_down and
                            secondary_health['healthy'] and
                            secondary_health['gtid_ok'] and
                            not self._failover_in_progress and
                            not self._failback_in_progress):

                        now = time.time()
                        if now - self._last_failover_time < self._failover_cooldown:
                            logger.debug("Failover on cooldown — skipping trigger")
                        else:
                            with self._failover_trigger_lock:
                                if not self._failover_triggered:
                                    self._failover_triggered = True
                                    self._last_failover_time = now
                                    logger.warning(
                                        f"Primary down "
                                        f"({self._failure_count[self.PRIMARY]} failures) "
                                        f"— triggering failover"
                                    )
                                    threading.Thread(
                                        target=self._do_failover,
                                        daemon=True,
                                        name="Auto-Failover"
                                    ).start()

                # ── FAILBACK TRIGGER ──────────────────────────────
                # When we are running on secondary and primary has recovered
                if (not master_is_primary and
                        primary_health['healthy'] and
                        primary_health['gtid_ok'] and
                        not self._failover_in_progress and
                        not self._failback_in_progress and
                        not self._primary_recovery_detected):

                    # Confirm with a second connection attempt
                    confirm = self._raw_connect(self.PRIMARY, timeout=5)
                    if confirm:
                        confirm.close()
                        logger.info(
                            "Primary (RDS) has recovered — scheduling auto-failback"
                        )
                        self._primary_recovery_detected = True
                        threading.Thread(
                            target=self._do_failback,
                            daemon=True,
                            name="Auto-Failback"
                        ).start()

                # ── REPLICATION FIX ───────────────────────────────
                # When routing is on primary, secondary should be replicating
                if (master_is_primary and
                        secondary_health['healthy'] and
                        not secondary_health['replicating'] and
                        not self._failover_in_progress and
                        not self._failback_in_progress):

                    logger.warning("Secondary replication is broken — attempting restart")
                    threading.Thread(
                        target=self._configure_secondary_as_replica,
                        daemon=True,
                        name="Repl-Restart"
                    ).start()

                # ── LOG SUMMARY ───────────────────────────────────
                p_icon = "✅" if primary_health['healthy'] else "❌"
                s_icon = "✅" if secondary_health['healthy'] else "❌"
                master_label = "RDS" if master_is_primary else "DOCKER"
                logger.info(
                    f"Check #{check_num} | Master: {master_label} | "
                    f"Primary {p_icon} | Secondary {s_icon} | "
                    f"Failovers: {self.stats['failovers']} | "
                    f"Failbacks: {self.stats['failbacks']}"
                )

                # ── ENFORCE ROLES ─────────────────────────────────
                self._enforce_roles()

            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)

            # Sleep 10 seconds (interruptible)
            for _ in range(10):
                if not self._monitor_running:
                    break
                time.sleep(1)

        logger.info("Monitor stopped")

    # --------------------------------------------------------
    # PUBLIC INTERFACE
    # --------------------------------------------------------

    def get_write_connection(self):
        """
        Return a connection to the current master (writable).
        If the master is unavailable and the other server is healthy,
        a failover is triggered in the background and a connection to
        the new master is returned immediately.
        """
        master = self._current_master
        conn = self._get_pooled_connection(master)
        if conn:
            return conn

        # Master is down — attempt emergency failover
        other = self.SECONDARY if master == self.PRIMARY else self.PRIMARY
        logger.warning(f"Master {master} unavailable — attempting emergency failover to {other}")

        other_conn = self._raw_connect(other)
        if other_conn:
            try:
                cursor = other_conn.cursor()
                cursor.execute("SET GLOBAL read_only = OFF")
                cursor.execute("SET GLOBAL super_read_only = OFF")
                cursor.close()
            finally:
                other_conn.close()

            threading.Thread(
                target=self._do_failover,
                daemon=True,
                name="Emergency-Failover"
            ).start()

            conn = self._get_pooled_connection(other)
            if conn:
                return conn

        raise Exception(
            f"No writable database available. "
            f"Master ({master}) is down and failover target ({other}) is unreachable."
        )

    def get_read_connection(self):
        """
        Return a connection for reading.
        Prefers the current replica (to avoid load on master).
        Falls back to master if replica is unavailable.
        """
        replica = self.SECONDARY if self._current_master == self.PRIMARY else self.PRIMARY
        conn = self._get_pooled_connection(replica)
        if conn:
            return conn

        logger.warning("Replica unavailable for reads — falling back to master")
        conn = self._get_pooled_connection(self._current_master)
        if conn:
            return conn

        raise Exception("No database available for reading.")

    def return_connection(self, conn):
        """Return a connection back to its pool (call after every get_*_connection use)."""
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT @@server_uuid")
            actual = cursor.fetchone()[0]
            cursor.close()
            server = None
            for s, uuid in self._uuids.items():
                if uuid == actual:
                    server = s
                    break
            if server:
                self._return_to_pool(server, conn)
            else:
                conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def get_status(self):
        """Return a dict describing the full system status."""
        primary_health   = self._check_server(self.PRIMARY)
        secondary_health = self._check_server(self.SECONDARY)
        return {
            'current_master': self._current_master,
            'running_on_rds': self._current_master == self.PRIMARY,
            'failover_in_progress': self._failover_in_progress,
            'failback_in_progress': self._failback_in_progress,
            'primary': {
                'host': PRIMARY_DB_HOST,
                'port': PRIMARY_DB_PORT,
                **primary_health,
            },
            'secondary': {
                'host': SECONDARY_DB_HOST,
                'port': SECONDARY_DB_PORT,
                **secondary_health,
            },
            'stats': self.stats,
            'recent_history': self.history[-5:],
            'timestamp': time.time(),
        }

    def manual_failover(self):
        """Manually trigger failover to secondary (Docker)."""
        if self._current_master == self.SECONDARY:
            return {'success': False, 'message': 'Already running on secondary'}
        threading.Thread(target=self._do_failover, daemon=True, name="Manual-Failover").start()
        return {'success': True, 'message': 'Manual failover to secondary triggered'}

    def manual_failback(self):
        """Manually trigger failback to primary (RDS)."""
        if self._current_master == self.PRIMARY:
            return {'success': False, 'message': 'Already running on primary'}
        threading.Thread(target=self._do_failback, daemon=True, name="Manual-Failback").start()
        return {'success': True, 'message': 'Manual failback to primary triggered'}

    def stop_monitor(self):
        """Stop the background monitor thread."""
        self._monitor_running = False
        logger.info("Monitor stop requested")


# ============================================================
# GLOBAL INSTANCE + SIMPLE FUNCTION INTERFACE
# (used by user_routes.py and auth_routes.py)
# ============================================================

gtid_manager = GTIDFailoverManager()


def get_write_connection():
    return gtid_manager.get_write_connection()


def get_read_connection():
    return gtid_manager.get_read_connection()


def return_connection(conn):
    """
    Return a connection to the pool.
    Routes.py should call this in a finally block after every DB operation.
    """
    gtid_manager.return_connection(conn)


def get_status():
    return gtid_manager.get_status()


def manual_failover():
    return gtid_manager.manual_failover()


def manual_failback():
    return gtid_manager.manual_failback()


def stop_monitor():
    return gtid_manager.stop_monitor()


if __name__ == "__main__":
    print("DB-Squared GTID Failover Manager")
    print(f"Primary  : {PRIMARY_DB_HOST}:{PRIMARY_DB_PORT} (AWS RDS)")
    print(f"Secondary: {SECONDARY_DB_HOST}:{SECONDARY_DB_PORT} (Docker MySQL)")
    print("\nAvailable functions:")
    print("  get_write_connection() — writable connection, auto-failover")
    print("  get_read_connection()  — read connection (prefers replica)")
    print("  return_connection(conn)— return conn to pool when done")
    print("  get_status()           — full system status dict")
    print("  manual_failover()      — force failover to Docker")
    print("  manual_failback()      — force failback to RDS")
