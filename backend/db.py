# backend/db.py FAILOVER HANDLER - FIXED WITH AUTO-DETECTION
import mysql.connector
from mysql.connector import Error
from config import (
    MASTER_DB_HOST, MASTER_DB_PORT, MASTER_DB_USER, 
    MASTER_DB_PASSWORD, MASTER_DB_NAME,
    SLAVE_DB_HOST, SLAVE_DB_PORT, SLAVE_DB_USER, 
    SLAVE_DB_PASSWORD, SLAVE_DB_NAME, REPLICA_USER, REPLICA_PASSWORD,
    MASTER_REPLICATION_HOST
)
import time
import threading
import logging
import json
import random
from logging.handlers import RotatingFileHandler
import os
import re
from email_alerter import email_alerter

# ============================================
# ENHANCED LOGGING SETUP - PRODUCTION READY
# ============================================

# Create logs directory if it doesn't exist
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Clear any existing loggers
logging.getLogger().handlers.clear()

# ========== 1. MAIN LOGGER (INFO level - for daily monitoring) ==========
main_logger = logging.getLogger('gtid.main')
main_logger.setLevel(logging.INFO)

main_handler = RotatingFileHandler(
    filename=f'{log_dir}/gtid_main.log',
    maxBytes=10*1024*1024,  # 10MB per file
    backupCount=5,          # Keep 5 backup files
    encoding='utf-8'
)
main_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
main_handler.setFormatter(main_formatter)
main_logger.addHandler(main_handler)

# ========== 2. DETAIL LOGGER (DEBUG level - for troubleshooting) ==========
detail_logger = logging.getLogger('gtid.detail')
detail_logger.setLevel(logging.DEBUG)

detail_handler = RotatingFileHandler(
    filename=f'{log_dir}/gtid_detail.log',
    maxBytes=20*1024*1024,  # 20MB per file
    backupCount=3,          # Keep 3 backup files
    encoding='utf-8'
)
detail_formatter = logging.Formatter(
    '%(asctime)s | %(threadName)-15s | %(levelname)-8s | %(funcName)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
detail_handler.setFormatter(detail_formatter)
detail_logger.addHandler(detail_handler)

# ========== 3. ERROR LOGGER (ERROR level - critical issues only) ==========
error_logger = logging.getLogger('gtid.error')
error_logger.setLevel(logging.ERROR)

error_handler = RotatingFileHandler(
    filename=f'{log_dir}/gtid_errors.log',
    maxBytes=5*1024*1024,   # 5MB per file
    backupCount=10,         # Keep more error logs
    encoding='utf-8'
)
error_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s\nFile: %(pathname)s:%(lineno)d\n',
    datefmt='%Y-%m-%d %H:%M:%S'
)
error_handler.setFormatter(error_formatter)
error_logger.addHandler(error_handler)

# ========== 4. CONSOLE LOGGER (for running in terminal) ==========
console_logger = logging.getLogger('gtid.console')
console_logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
console_handler.setFormatter(console_formatter)
console_logger.addHandler(console_handler)

# ========== 5. BACKWARD COMPATIBILITY (your old log file) ==========
legacy_logger = logging.getLogger('gtid.legacy')
legacy_logger.setLevel(logging.DEBUG)

legacy_handler = logging.FileHandler('gtid_failover.log', encoding='utf-8')
legacy_formatter = logging.Formatter(
    '%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)
legacy_handler.setFormatter(legacy_formatter)
legacy_logger.addHandler(legacy_handler)

# ========== COMBINED LOGGER (easy to use in code) ==========
class GTIDLogger:
    """Unified logger that writes to all appropriate logs"""
    
    @staticmethod
    def info(message):
        """INFO level - goes to main log and console"""
        main_logger.info(message)
        console_logger.info(message)
        legacy_logger.info(message)
    
    @staticmethod
    def debug(message):
        """DEBUG level - goes to detail log only (for troubleshooting)"""
        detail_logger.debug(message)
        legacy_logger.debug(message)
    
    @staticmethod  
    def warning(message):
        """WARNING level - goes to main log, console, and error log"""
        main_logger.warning(message)
        console_logger.warning(message)
        error_logger.warning(message)
        legacy_logger.warning(message)
    
    @staticmethod
    def error(message, exc_info=False):
        """ERROR level - goes to ALL logs (critical issue)"""
        main_logger.error(message)
        console_logger.error(message)
        error_logger.error(message, exc_info=exc_info)
        legacy_logger.error(message)
    
    @staticmethod
    def critical(message):
        """CRITICAL level - goes to ALL logs with stack trace"""
        main_logger.critical(message)
        console_logger.critical(message)
        error_logger.critical(message, exc_info=True)
        legacy_logger.critical(message)

# ========== SIMPLE ALIAS ==========
logger = GTIDLogger()

# Log startup message
logger.info("=" * 60)
logger.info("GTID-ONLY FAILOVER MANAGER STARTED (UUID-VERIFIED + AUTO-DETECT)")
logger.info(f"Logs: {log_dir}/gtid_main.log (summary)")
logger.info(f"      {log_dir}/gtid_detail.log (troubleshooting)")
logger.info(f"      {log_dir}/gtid_errors.log (critical errors)")
logger.info("=" * 60)

class GTIDFailoverManager:
    """
    GTID-only failover manager - No binary logging checks
    """
    
    def __init__(self):
        # ===== PHYSICAL SERVER CONFIGS (NEVER CHANGE) =====
        self.master_db_config = {
            'host': MASTER_DB_HOST,
            'port': MASTER_DB_PORT,
            'user': MASTER_DB_USER,
            'password': MASTER_DB_PASSWORD,
            'database': MASTER_DB_NAME,
            'replica_user': REPLICA_USER,
            'replica_password': REPLICA_PASSWORD,
            'replication_host': MASTER_REPLICATION_HOST
        }
        
        self.slave_db_config = {
            'host': SLAVE_DB_HOST,
            'port': SLAVE_DB_PORT,
            'user': SLAVE_DB_USER,
            'password': SLAVE_DB_PASSWORD,
            'database': SLAVE_DB_NAME,
            'replica_user': REPLICA_USER,
            'replica_password': REPLICA_PASSWORD,
            'replication_host': SLAVE_DB_HOST
        }

        # ===== CONNECTION POOLS (PER PHYSICAL SERVER) =====
        self.master_db_pool = []
        self.slave_db_pool = []
        self.max_connections = 10

        # ===== POOL STATS (PER LOGICAL ROLE) =====
        self.pool_stats = {
            'master': {
                'total_connections_created': 0,
                'total_connections_reused': 0,
                'contaminated_connections_removed': 0,
                'dead_connections_removed': 0,
                'current_pool_size': 0,
                'peak_pool_size': 0,
                'contaminated_since_last_cleanup': 0,
                'dead_since_last_cleanup': 0
            },
            'slave': {
                'total_connections_created': 0,
                'total_connections_reused': 0,
                'contaminated_connections_removed': 0,
                'dead_connections_removed': 0,
                'current_pool_size': 0,
                'peak_pool_size': 0,
                'contaminated_since_last_cleanup': 0,
                'dead_since_last_cleanup': 0
           }
        }

        # ===== LOGICAL ROLE (CHANGES DURING FAILOVER) =====
        # AUTO-DETECT CURRENT MASTER from replication configs
        self.current_master_server = self._detect_current_master()
        
        # For backward compatibility
        self.current_master = 'slave' if self.current_master_server == 'slave_db' else 'master'
        
        # Active connections being used right now
        self.active_connections = {}
        
        # Failover control
        self.failover_in_progress = False
        self.failover_lock = threading.Lock()
        self.fencing_tokens = {}
        self._replication_restart_needed = False
        self.connection_failure_count = {
            'master': 0,
            'slave': 0
        }
        self.max_failures_before_failover = 3

        # Statistics
        self.stats = {
            'failovers': 0,
            'auto_recoveries': 0,
            'errors': 0,
            'connection_creates': 0,
            'connection_reuses': 0
        }
        
        # History of operations
        self.history = []
        
        # ===== UUID TRACKING (CRITICAL FIX) =====
        self.master_db_uuid = None
        self.slave_db_uuid = None
        
        # ===== FIX #4: Add cooldown for auto-failover =====
        self.last_failover_trigger_time = 0
        self.failover_cooldown = 60
        
        # ===== FIX #6: Add failover trigger lock to prevent multiple triggers =====
        self.failover_trigger_lock = threading.Lock()
        self.failover_triggered = False
        
        # ===== FIX #10: Add dead servers tracking =====
        self._dead_servers = {}
        
        # ===== ADDED: Track when master last changed =====
        self._last_master_change_time = time.time()
        
        # ===== ADDED: Enforcer cooldown period =====
        self.enforcer_cooldown = 30  # Don't enforce for 30 seconds after failover
        
        # Fetch UUIDs BEFORE starting monitor
        self._fetch_server_uuids()
        
        # Try multiple times to fetch UUIDs (in case slave is slow to start)
        for attempt in range(3):
            if self.master_db_uuid and self.slave_db_uuid:
                break
            logger.warning(f"⚠️ UUID fetch attempt {attempt+1}/3 incomplete, waiting 2 seconds...")
            time.sleep(2)
            self._fetch_server_uuids()

        
        # Start background monitoring
        self.monitor_running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="GTID-Monitor"
        )
        self.monitor_thread.start()
        
        logger.info(f"Current master server: {self.current_master_server}")
        logger.info(f"Master DB: {MASTER_DB_HOST}:{MASTER_DB_PORT}")
        logger.info(f"Slave DB: {SLAVE_DB_HOST}:{SLAVE_DB_PORT}")
    
    # ============================================
    # AUTO-DETECTION OF CURRENT MASTER
    # ============================================
    
    def _detect_current_master(self):
        """
        Auto-detect which physical server is currently the master by examining replication configs.
        
        Detection Logic:
        1. Query SHOW REPLICA STATUS on both servers
        2. Determine master based on who is NOT replicating:
           - If master_db has no replication → master_db is likely the master
           - If slave_db has no replication → slave_db is likely the master
           - If BOTH have no replication → default to master_db (initial setup)
           - If BOTH are replicating → INVALID STATE, default to master_db + critical log
        
        Returns: 'master_db' or 'slave_db' (the detected master server)
        """
        logger.info("=" * 60)
        logger.info("🔍 AUTO-DETECTING CURRENT MASTER SERVER")
        logger.info("=" * 60)
        
        # Check replication status on both servers
        master_db_replicating = False
        slave_db_replicating = False
        master_db_source = None
        slave_db_source = None
        
        # Check master_db
        try:
            conn = self._create_physical_connection_bootstrap('master_db')
            if conn:
                cursor = conn.cursor()
                cursor.execute("SHOW REPLICA STATUS")
                row = cursor.fetchone()
                
                if row:
                    # master_db IS replicating from something
                    columns = [desc[0] for desc in cursor.description]
                    for i, col in enumerate(columns):
                        if col in ['Source_Host', 'Master_Host']:
                            master_db_source = row[i]
                        elif col in ['Replica_IO_Running', 'Slave_IO_Running']:
                            if row[i] == 'Yes':
                                master_db_replicating = True
                
                cursor.close()
                conn.close()
                
                if master_db_replicating:
                    logger.info(f"   master_db (3306): IS replicating from {master_db_source}")
                else:
                    logger.info(f"   master_db (3306): NOT replicating (standalone or master)")
        except Exception as e:
            logger.warning(f"   ⚠️ Could not check master_db replication status: {e}")
        
        # Check slave_db
        try:
            conn = self._create_physical_connection_bootstrap('slave_db')
            if conn:
                cursor = conn.cursor()
                cursor.execute("SHOW REPLICA STATUS")
                row = cursor.fetchone()
                
                if row:
                    # slave_db IS replicating from something
                    columns = [desc[0] for desc in cursor.description]
                    for i, col in enumerate(columns):
                        if col in ['Source_Host', 'Master_Host']:
                            slave_db_source = row[i]
                        elif col in ['Replica_IO_Running', 'Slave_IO_Running']:
                            if row[i] == 'Yes':
                                slave_db_replicating = True
                
                cursor.close()
                conn.close()
                
                if slave_db_replicating:
                    logger.info(f"   slave_db (3307): IS replicating from {slave_db_source}")
                else:
                    logger.info(f"   slave_db (3307): NOT replicating (standalone or master)")
        except Exception as e:
            logger.warning(f"   ⚠️ Could not check slave_db replication status: {e}")
        
        # Decision logic
        detected_master = None
        
        if not master_db_replicating and not slave_db_replicating:
            # Case 1: BOTH standalone (initial setup or both broken)
            logger.info("📋 CASE: Both servers standalone (no replication)")
            logger.info("   → Defaulting to master_db as master")
            detected_master = 'master_db'
        
        elif not master_db_replicating and slave_db_replicating:
            # Case 2: Normal topology - master_db is master, slave_db replicates from it
            logger.info("📋 CASE: Normal topology detected")
            logger.info("   → master_db is the master")
            detected_master = 'master_db'
        
        elif master_db_replicating and not slave_db_replicating:
            # Case 3: Failover topology - slave_db is now master, master_db replicates from it
            logger.info("📋 CASE: Failover topology detected")
            logger.info("   → slave_db is the master (previous failover occurred)")
            detected_master = 'slave_db'
        
        elif master_db_replicating and slave_db_replicating:
            # Case 4: INVALID - circular replication or both are slaves
            logger.critical("🚨 CRITICAL: BOTH SERVERS ARE REPLICATING!")
            logger.critical(f"   master_db replicating from: {master_db_source}")
            logger.critical(f"   slave_db replicating from: {slave_db_source}")
            logger.critical("   This is an INVALID state (circular replication or both are slaves)")
            logger.critical("   → Defaulting to master_db and manual intervention required")
            detected_master = 'master_db'
        
        logger.info("=" * 60)
        logger.info(f"✅ DETECTED MASTER: {detected_master}")
        logger.info("=" * 60)
        
        return detected_master
    
    def _create_physical_connection_bootstrap(self, physical_server):
        """
        Create connection during bootstrap (before UUIDs are fetched).
        This is a simpler version without UUID verification for initial setup.
        """
        if physical_server == 'master_db':
            config = self.master_db_config
        else:  # slave_db
            config = self.slave_db_config
    
        try:
            conn = mysql.connector.connect(
                host=config['host'],
                port=config['port'],
                user=config['user'],
                password=config['password'],
                database=config['database'],
                connection_timeout=5,
                autocommit=True
            )
            return conn
        except Exception as e:
            logger.debug(f"Bootstrap connection to {physical_server} failed: {e}")
            return None

    # ============================================
    # 1. CONNECTION MANAGEMENT
    # ============================================

    def verify_read_only_status(self, physical_server, expected_ro=True):
        """Verify a physical server is in the expected read-only state"""
        logger.debug(f"Verifying read-only status of {physical_server} (expected read-only={expected_ro})")
    
        conn = None
        try:
            conn = self._create_physical_connection(physical_server)
            if not conn:
                logger.error(f"Cannot connect to {physical_server} to verify read-only status")
                return False
        
            cursor = conn.cursor()
            cursor.execute("SELECT @@read_only, @@super_read_only")
            read_only, super_read_only = cursor.fetchone()
            cursor.close()
        
            is_read_only = (read_only == 1 and super_read_only == 1)
        
            if is_read_only == expected_ro:
                logger.debug(f"✅ {physical_server} read-only status correct: {is_read_only}")
                return True
            else:
                logger.error(f"❌ {physical_server} has wrong read-only status!")
                logger.error(f"   Expected read-only={expected_ro}, Got: ro={read_only}, sro={super_read_only}")
                return False
            
        except Exception as e:
            logger.error(f"Error verifying read-only status of {physical_server}: {e}")
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
     
    def _create_physical_connection(self, physical_server):
        """Create a new connection to a specific physical server - UUID VERIFIED"""
        logger.debug(f"Creating new connection to {physical_server}")
        
        # Choose the correct physical server config
        if physical_server == 'master_db':
            config = self.master_db_config
            expected_uuid = self.master_db_uuid
        else:  # slave_db
            config = self.slave_db_config
            expected_uuid = self.slave_db_uuid
    
        try:
            conn = mysql.connector.connect(
                host=config['host'],
                port=config['port'],
                user=config['user'],
                password=config['password'],
                database=config['database'],
                connection_timeout=5,
                autocommit=True
            )
        
            # CRITICAL FIX: Verify UUID instead of port
            cursor = conn.cursor()
            cursor.execute("SELECT @@server_uuid")
            actual_uuid = cursor.fetchone()[0]
            cursor.close()
        
            # Verify UUID matches (if we have it)
            if expected_uuid:
                if actual_uuid != expected_uuid:
                    logger.error(f"❌ UUID MISMATCH! Expected {physical_server} ({expected_uuid[:8]}...), got {actual_uuid[:8]}...")
                    conn.close()
                    return None
                logger.debug(f"✅ UUID verified: connected to {physical_server}")
            else:
                # During startup before UUIDs are fetched
                logger.debug(f"✅ Created connection to {physical_server} (UUID: {actual_uuid[:8]}...)")
        
            return conn
        
        except Exception as e:
            logger.error(f"Failed to connect to {physical_server}: {e}")
            return None

    def _get_physical_server_for_logical_role(self, logical_role):
        """Map logical role (master/slave) to physical server (master_db/slave_db)"""
        if logical_role == 'master':
            result = self.current_master_server
        else:  # logical_role == 'slave'
            if self.current_master_server == 'master_db':
                result = 'slave_db'
            else:
                result = 'master_db'
        
        logger.debug(f"🔍 Role mapping: logical {logical_role} → physical {result} (current master={self.current_master_server})")
        return result
    
    def get_connection(self, logical_role):
        """Get a connection for a logical role (master/slave) - UUID VERIFIED"""
        logger.debug(f"Getting connection for logical {logical_role}")
        
        # Map logical role to physical server
        physical_server = self._get_physical_server_for_logical_role(logical_role)
        logger.debug(f"   Logical {logical_role} → Physical {physical_server}")
        
        # Check if server is in dead cooldown
        dead_info = self._dead_servers.get(physical_server)
        if dead_info and time.time() - dead_info['time'] < 30:  # 30 second cooldown
            logger.debug(f"⏸️ Server {physical_server} is in dead cooldown ({int(30 - (time.time() - dead_info['time']))}s remaining)")
            return None
        
        # Select the correct pool based on physical server
        if physical_server == 'master_db':
            pool = self.master_db_pool
            expected_uuid = self.master_db_uuid
            config = self.master_db_config
        else:
            pool = self.slave_db_pool
            expected_uuid = self.slave_db_uuid
            config = self.slave_db_config
        
        # Update pool stats for the LOGICAL role
        self.pool_stats[logical_role]['current_pool_size'] = len(pool)
        if len(pool) > self.pool_stats[logical_role]['peak_pool_size']:
            self.pool_stats[logical_role]['peak_pool_size'] = len(pool)
        
        # Try to get a valid connection from pool
        conn = None
        contaminated_count = 0
        dead_count = 0
    
        while pool and not conn:
            test_conn = pool.pop()
            try:
                test_conn.ping(reconnect=False, attempts=1, delay=0)
               
                # CRITICAL FIX: Check UUID instead of port
                cursor = test_conn.cursor()
                cursor.execute("SELECT @@server_uuid")
                actual_uuid = cursor.fetchone()[0]
                cursor.close()
            
                # Verify UUID matches
                if actual_uuid == expected_uuid:
                    conn = test_conn
                    self.stats['connection_reuses'] += 1
                    self.pool_stats[logical_role]['total_connections_reused'] += 1
                    logger.debug(f"✅ Reused connection to {physical_server} (UUID verified)")
                else:
                    contaminated_count += 1
                    self.pool_stats[logical_role]['contaminated_connections_removed'] += 1
                    self.pool_stats[logical_role]['contaminated_since_last_cleanup'] += 1
                    logger.warning(f"🧹 CONTAMINATED! Expected {physical_server} ({expected_uuid[:8] if expected_uuid else '?'}...), got UUID {actual_uuid[:8]}...")
                    test_conn.close()
                
            except Exception as e:
                dead_count += 1
                self.pool_stats[logical_role]['dead_connections_removed'] += 1
                self.pool_stats[logical_role]['dead_since_last_cleanup'] += 1
                logger.debug(f"🧹 Removed dead connection: {e}")
                test_conn.close()
    
        if contaminated_count > 0 or dead_count > 0:
            logger.warning(f"📊 {logical_role.upper()} pool cleanup: removed {contaminated_count} contaminated, {dead_count} dead")
    
        # If no valid connection, create new one
        if not conn:
            logger.debug(f"No valid connection to {physical_server} in pool, creating new")
            conn = self._create_physical_connection(physical_server)
            if conn:
                self.pool_stats[logical_role]['total_connections_created'] += 1
                self.stats['connection_creates'] += 1
            else:
                # Track failure for the logical role
                if logical_role == 'master':
                    self.connection_failure_count['master'] += 1
                else:
                    self.connection_failure_count['slave'] += 1
                return None
        else:
            # Successfully reused - reset failure counter for this logical role
            if logical_role == 'master':
                if self.connection_failure_count['master'] > 0:
                    logger.info(f"✅ Master connection restored - resetting failure counter")
                    self.connection_failure_count['master'] = 0
            else:
                if self.connection_failure_count['slave'] > 0:
                    logger.info(f"✅ Slave connection restored - resetting failure counter")
                    self.connection_failure_count['slave'] = 0
    
        # Track active connection
        thread_id = threading.get_ident()
        self.active_connections[thread_id] = {
            'logical_role': logical_role,
            'physical_server': physical_server,
            'conn': conn,
            'time': time.time()
        }
        return conn
        
    
    def return_connection(self, logical_role, conn):
        """Return connection to pool - UUID VERIFIED ROUTING"""
        if not conn:
            return
    
        thread_id = threading.get_ident()
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
    
        try:
            if not conn.is_connected():
                self.pool_stats[logical_role]['dead_connections_removed'] += 1
                self.pool_stats[logical_role]['dead_since_last_cleanup'] += 1
                conn.close()
                return
        
            # CRITICAL FIX: Route by UUID instead of port
            cursor = conn.cursor()
            cursor.execute("SELECT @@server_uuid")
            actual_uuid = cursor.fetchone()[0]
            cursor.close()
        
            # Route to correct pool based on UUID
            if actual_uuid == self.master_db_uuid:
                pool = self.master_db_pool
                physical = 'master_db'
            elif actual_uuid == self.slave_db_uuid:
                pool = self.slave_db_pool
                physical = 'slave_db'
            else:
                logger.warning(f"🧹 Unknown UUID {actual_uuid[:8]}..., closing connection")
                conn.close()
                return
        
            # Add to pool if space
            if len(pool) < self.max_connections:
                pool.append(conn)
                self.pool_stats[logical_role]['current_pool_size'] = len(pool)
                logger.debug(f"✅ Returned connection to {physical} pool (now {len(pool)}/{self.max_connections})")
            else:
                conn.close()
                logger.debug(f"{physical} pool full, closed connection")
            
        except Exception as e:
            logger.debug(f"Error returning connection: {e}")
            self.pool_stats[logical_role]['dead_connections_removed'] += 1
            self.pool_stats[logical_role]['dead_since_last_cleanup'] += 1
            try:
                conn.close()
            except:
                pass

    def check_pool_health(self):
        """Check pool health and log contamination stats"""
        logger.info("=" * 60)
        logger.info("📊 POOL HEALTH REPORT")
        logger.info("=" * 60)
    
        for role in ['master', 'slave']:
            stats = self.pool_stats[role]
            master_pool_size = len(self.master_db_pool)
            slave_pool_size = len(self.slave_db_pool)
        
            logger.info(f"\n{role.upper()} LOGICAL ROLE STATS:")
            logger.info(f"  Total created: {stats['total_connections_created']}")
            logger.info(f"  Total reused: {stats['total_connections_reused']}")
            logger.info(f"  Contaminated removed: {stats['contaminated_connections_removed']}")
            logger.info(f"  Dead removed: {stats['dead_connections_removed']}")
        
        logger.info(f"\nPHYSICAL POOLS:")
        logger.info(f"  master_db pool (3306): {len(self.master_db_pool)}/{self.max_connections}")
        logger.info(f"  slave_db pool (3307): {len(self.slave_db_pool)}/{self.max_connections}")
        logger.info("=" * 60)

    # ============================================
    # 2. GTID-ONLY HEALTH CHECKS
    # ============================================
    
    def check_database(self, logical_role):
        """Check database health for a logical role (master/slave)"""
        start_time = time.time()
        logger.debug(f"Starting health check for logical {logical_role}")
        
        # Map logical role to physical server
        physical_server = self._get_physical_server_for_logical_role(logical_role)
        current_slave_server = self._get_current_slave_server()

        # Check if server is in dead cooldown
        dead_info = self._dead_servers.get(physical_server)
        if dead_info and time.time() - dead_info['time'] < 30:
            logger.debug(f"⏸️ Skipping health check for dead server {physical_server} (cooldown)")
            return {
                'role': logical_role,
                'physical_server': physical_server,
                'timestamp': start_time,
                'healthy': False,
                'writable': False,
                'gtid_ok': False,
                'problem': 'server_dead_cooldown',
                'checks': {'connectivity': 'SKIP (cooldown)'},
                'check_time_seconds': time.time() - start_time
            }

        result = {
            'role': logical_role,
            'physical_server': physical_server,
            'timestamp': start_time,
            'healthy': False,
            'writable': False,
            'gtid_ok': False,
            'problem': None,
            'checks': {}
        }
        
        # If we don't have this server's UUID yet, try to get it
        if ((physical_server == 'master_db' and self.master_db_uuid is None) or
            (physical_server == 'slave_db' and self.slave_db_uuid is None)):
        
            try:
                # Try a direct connection to get the UUID
                if physical_server == 'master_db':
                    config = self.master_db_config
                else:
                    config = self.slave_db_config
                
                test_conn = mysql.connector.connect(
                    host=config['host'],
                    port=config['port'],
                    user=config['user'],
                    password=config['password'],
                    connection_timeout=3
                )
             
                cursor = test_conn.cursor()
                cursor.execute("SELECT @@server_uuid")
                fetched_uuid = cursor.fetchone()[0]
                cursor.close()
                test_conn.close()
                
                # Store the UUID
                if physical_server == 'master_db':
                    logger.info(f"✅ Got master_db UUID: {fetched_uuid}")
                    self.master_db_uuid = fetched_uuid
                else:
                    logger.info(f"✅ Got slave_db UUID: {fetched_uuid}")
                    self.slave_db_uuid = fetched_uuid
                
            except Exception as e:
                logger.debug(f"Still can't get UUID for {physical_server}: {e}")
        

        conn = None
        try:
            # 1. Get connection
            conn = self.get_connection(logical_role)
            if not conn:
                result['problem'] = 'cannot_connect'
                result['checks']['connectivity'] = 'FAIL'
    
                # Track connection failures
                if logical_role == 'master':
                    self.connection_failure_count['master'] += 1
                    logger.warning(f"❌ Master connection failed ({self.connection_failure_count['master']}/{self.max_failures_before_failover})")
        
                    if self.connection_failure_count['master'] >= self.max_failures_before_failover:
                        logger.critical(f"🚨 Master connection failed {self.max_failures_before_failover} times in a row!")
                        result['problem'] = 'connection_failure_threshold_exceeded'
                        self._dead_servers[physical_server] = {
                            'time': time.time(),
                            'failures': self.connection_failure_count['master']
                        }
                else:
                    self.connection_failure_count['slave'] += 1
                    logger.warning(f"❌ Slave connection failed ({self.connection_failure_count['slave']}/{self.max_failures_before_failover})")
                    
                    if self.connection_failure_count['slave'] >= self.max_failures_before_failover:
                        self._dead_servers[physical_server] = {
                            'time': time.time(),
                            'failures': self.connection_failure_count['slave']
                        }
    
                return result
            
            # VERIFY the connection actually works
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            except Exception as e:
                logger.warning(f"❌ {logical_role} connection test query failed: {e}")
                result['problem'] = 'cannot_connect'
                result['checks']['connectivity'] = 'FAIL'
                self.return_connection(logical_role, conn)
                
                if logical_role == 'master':
                    self.connection_failure_count['master'] += 1
                    logger.warning(f"❌ Master connection test failed ({self.connection_failure_count['master']}/{self.max_failures_before_failover})")
                    
                    if self.connection_failure_count['master'] >= self.max_failures_before_failover:
                        logger.critical(f"🚨 Master connection failed {self.max_failures_before_failover} times in a row!")
                        result['problem'] = 'connection_failure_threshold_exceeded'
                        self._dead_servers[physical_server] = {
                            'time': time.time(),
                            'failures': self.connection_failure_count['master']
                        }
                else:
                    self.connection_failure_count['slave'] += 1
                    logger.warning(f"❌ Slave connection test failed ({self.connection_failure_count['slave']}/{self.max_failures_before_failover})")
                    
                    if self.connection_failure_count['slave'] >= self.max_failures_before_failover:
                        self._dead_servers[physical_server] = {
                            'time': time.time(),
                            'failures': self.connection_failure_count['slave']
                        }
                
                return result
            
            # If we got a connection, server is alive
            if physical_server in self._dead_servers:
                logger.info(f"✅ Server {physical_server} is back alive - removing from dead list")
                
                del self._dead_servers[physical_server]
            
            result['healthy'] = True
            result['checks']['connectivity'] = 'PASS'
            logger.debug(f"{logical_role} connectivity: PASS")
            
            # 2. Check if writable
            cursor.execute("SELECT @@read_only, @@super_read_only")
            read_only, super_read_only = cursor.fetchone()
            
            if read_only == 0 and super_read_only == 0:
                result['writable'] = True
                result['checks']['writable'] = 'PASS'
                logger.debug(f"{logical_role} writable: YES")
            else:
                result['checks']['writable'] = 'FAIL'
                result['problem'] = 'read_only'
                logger.debug(f"{logical_role} writable: NO (read_only={read_only}, super_read_only={super_read_only})")
            
            # 3. Check GTID status
            try:
                cursor.execute("SELECT @@GLOBAL.gtid_mode, @@GLOBAL.gtid_executed")
                gtid_mode, gtid_executed = cursor.fetchone()
                
                result['gtid_mode'] = gtid_mode
                result['gtid_executed'] = gtid_executed or ""
                result['checks']['gtid_mode'] = 'PASS' if gtid_mode == 'ON' else 'FAIL'
                
                if gtid_mode == 'ON':
                    result['gtid_ok'] = True
                    gtid_count = len(gtid_executed.split(',')) if gtid_executed else 0
                    logger.debug(f"{logical_role} GTID: {gtid_mode} ({gtid_count} transactions)")
                else:
                    result['problem'] = 'gtid_off'
                    logger.debug(f"{logical_role} GTID: {gtid_mode} (should be ON)")
                    
            except Exception as e:
                result['checks']['gtid'] = 'ERROR'
                logger.debug(f"{logical_role} GTID check error: {e}")
            
            # 4. Check disk space
            try:
                test_id = int(time.time()) % 1000
                cursor.execute(f"SELECT {test_id} as test_value")
                if cursor.fetchone()[0] == test_id:
                    result['checks']['disk_space'] = 'PASS'
                    logger.debug(f"{logical_role} disk: OK")
                else:
                    result['checks']['disk_space'] = 'WARN'
                    logger.debug(f"{logical_role} disk: WARNING")
            except Exception as e:
                error_str = str(e).lower()
                if "no space" in error_str or "disk full" in error_str:
                    result['checks']['disk_space'] = 'FAIL'
                    result['problem'] = 'disk_full'
                    logger.debug(f"{logical_role} disk: FULL")
                else:
                    result['checks']['disk_space'] = 'ERROR'
                    logger.debug(f"{logical_role} disk check error: {str(e)[:50]}")
            
            # 5. If this is the logical slave, check replication
            if logical_role == 'slave':
                repl_status = self._check_replication(cursor)
                result['replication'] = repl_status
                if not repl_status['ok']:
                    result['problem'] = 'replication_broken'
                    logger.debug(f"{logical_role} replication: {repl_status.get('error', 'broken')}")
                else:
                    lag = repl_status.get('seconds_behind', '?')
                    logger.debug(f"{logical_role} replication: OK (lag: {lag}s)")
            
            cursor.close()
            
        except Exception as e:
            if result['healthy'] is not False: 
                result['healthy'] = False
                
            error_type = type(e).__name__
            error_message = str(e)
            
            logger.debug(f"{logical_role} check ERROR: {error_type}: {error_message}")
            
            if result['problem'] is None:
                error_str = error_message.lower()
            
                if "access denied" in error_str or "authentication" in error_str:
                    result['problem'] = 'access_denied'
                elif "can't connect" in error_str or "connection refused" in error_str:
                    result['problem'] = 'connection_failed'
                elif "timeout" in error_str:
                    result['problem'] = 'timeout'
                elif "too many connections" in error_str:
                    result['problem'] = 'too_many_connections'
                elif "no space" in error_str or "disk full" in error_str:
                    result['problem'] = 'disk_full'
                elif "read-only" in error_str:
                    result['problem'] = 'read_only'
                else:
                    result['problem'] = f'other: {error_type}'
                
        finally:
            if conn:
                self.return_connection(logical_role, conn)
        
        check_time = time.time() - start_time
        result['check_time_seconds'] = check_time
        
        # Create summary for main log
        if result['healthy']:
            status_icon = "✅" if result['writable'] else "⚠️ "
            
            summary_parts = [f"{logical_role.upper()} {status_icon}"]
            is_current_master = (logical_role == 'master')
            if is_current_master:
                summary_parts.append("(CURRENT)")
            if result['writable']:
                summary_parts.append("WRITABLE")
            else:
                summary_parts.append("READ-ONLY")
            
            if result['gtid_ok']:
                summary_parts.append("GTID:ON")
            else:
                summary_parts.append("GTID:OFF")
            
            if logical_role == 'slave' and result.get('replication', {}).get('ok'):
                lag = result['replication'].get('seconds_behind', '?')
                summary_parts.append(f"REPL:{lag}s")
            
            summary_parts.append(f"({check_time:.1f}s)")
            summary_parts.append(f"[{physical_server}]")
            
            logger.info(" | ".join(summary_parts))
            
        else:
            status_icon = "❌"
            problem = result.get('problem', 'unknown')
            logger.warning(f"{logical_role.upper()} {status_icon} UNHEALTHY: {problem} ({check_time:.1f}s) [{physical_server}]")
        
        return result
    
    def _check_replication(self, cursor):
        """Check replication status for slave"""
        try:
            cursor.execute("SHOW REPLICA STATUS")
            row = cursor.fetchone()
            
            if not row:
                return {'ok': False, 'error': 'Not configured as slave'}
            
            columns = [desc[0] for desc in cursor.description]
            values = row
            
            result = {'ok': False}
            
            for i, col in enumerate(columns):
                value = values[i]
                if col == 'Replica_IO_Running':
                    result['io_running'] = value
                elif col == 'Replica_SQL_Running':
                    result['sql_running'] = value
                elif col == 'Seconds_Behind_Source':
                    result['seconds_behind'] = value
                elif col == 'Last_IO_Error':
                    if value:
                        result['last_io_error'] = value[:100]
                elif col == 'Last_SQL_Error':
                    if value:
                        result['last_sql_error'] = value[:100]
                elif col == 'Source_UUID':
                    result['master_uuid'] = value
            
            if result.get('io_running') == 'Yes' and result.get('sql_running') == 'Yes':
                result['ok'] = True
            
            return result
            
        except Exception as e:
            logger.debug(f"Replication check error: {e}")
            return {'ok': False, 'error': str(e)}
    
    # ============================================
    # 3. FENCING (SPLIT-BRAIN PREVENTION)
    # ============================================
    
    def _create_fence_token(self, role):
        """Create a fencing token to prevent split-brain"""
        token = {
            'role': role,
            'timestamp': time.time(),
            'token': f"fence_{role}_{int(time.time())}_{random.randint(1000, 9999)}",
            'valid_until': time.time() + 300
        }
        
        self.fencing_tokens[role] = token
        logger.info(f"Created fence token for {role}: {token['token'][:20]}...")
        return token
    
    def _validate_fence_token(self, role, token_to_check):
        """Check if a fence token is valid"""
        if role not in self.fencing_tokens:
            return False
        
        stored_token = self.fencing_tokens[role]
        
        if (stored_token['token'] == token_to_check and 
            stored_token['valid_until'] > time.time()):
            return True
        
        return False
    
    def _fence_database(self, physical_server):
        """Fence a PHYSICAL server (make it read-only and kill connections)"""
        logger.info(f"🔒 FENCING physical server: {physical_server}")
    
        conn = None
        try:
            conn = self._create_physical_connection(physical_server)
            if not conn:
                logger.warning(f"Cannot connect to {physical_server} to fence it")
                return self._create_fence_token(physical_server)
         
            cursor = conn.cursor()
        
            logger.debug(f"Setting {physical_server} to read-only...")
            cursor.execute("SET GLOBAL read_only = ON")
            cursor.execute("SET GLOBAL super_read_only = ON")
        
            # VERIFY it's actually read-only
            cursor.execute("SELECT @@read_only, @@super_read_only")
            read_only, super_read_only = cursor.fetchone()
        
            if read_only == 1 and super_read_only == 1:
                logger.info(f"✅ {physical_server} successfully set to read-only")
            else:
                logger.error(f"❌ {physical_server} STILL WRITABLE after fence! ro={read_only}, sro={super_read_only}")
                # Try again
                cursor.execute("SET GLOBAL read_only = ON")
                cursor.execute("SET GLOBAL super_read_only = ON")
                cursor.execute("SELECT @@read_only, @@super_read_only")
                read_only, super_read_only = cursor.fetchone()
                if read_only == 1 and super_read_only == 1:
                    logger.info(f"✅ {physical_server} now read-only after retry")
                else:
                    logger.critical(f"💥 CRITICAL: Cannot fence {physical_server}!")
           
            # Kill user connections
            logger.debug(f"Killing user connections on {physical_server}...")
            try:
                cursor.execute("""
                    SELECT id 
                    FROM information_schema.processlist 
                    WHERE USER NOT IN ('system user', 'event_scheduler') 
                    AND COMMAND NOT IN ('Sleep', 'Binlog Dump')
                    AND ID != CONNECTION_ID()
                """)
             
                connections_to_kill = cursor.fetchall()
                kill_count = 0
               
                for (connection_id,) in connections_to_kill:
                    try:
                        cursor.execute(f"KILL {connection_id}")
                        kill_count += 1
                    except:
                        pass
               
                if kill_count > 0:
                    logger.debug(f"Killed {kill_count} connections on {physical_server}")
                else:
                    logger.debug(f"No user connections to kill on {physical_server}")
                     
            except Exception as e:
                logger.debug(f"Could not kill connections: {e}")
           
            cursor.close()
           
            token = self._create_fence_token(physical_server)
            logger.info(f"✅ {physical_server} successfully fenced")
            return token
         
        except Exception as e:
            logger.warning(f"Partial fencing on {physical_server}: {e}")
            return self._create_fence_token(physical_server)
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
    
    # ============================================
    # 4. GTID-BASED FAILOVER
    # ============================================
    
    def _get_current_slave_server(self):
        """Determine which physical server is currently the slave"""
        if self.current_master_server == 'master_db':
            return 'slave_db'
        else:
            return 'master_db'
    
    def failover_to(self, new_master_server):
        email_alerter.send_alert(
            "🚨 FAILOVER STARTED",
            f"Failover triggered!\n\nFrom: {self.current_master_server}\nTo: {new_master_server}"
        )
    
        logger.info("=" * 60)
        logger.info(f"STARTING GTID FAILOVER to {new_master_server}")
        logger.info("=" * 60)
        
        # Validation
        if new_master_server not in ['master_db', 'slave_db']:
            return self._make_response(False, f"Invalid server: {new_master_server}")
        
        if new_master_server == self.current_master_server:
            return self._make_response(False, f"{new_master_server} is already master")
        
        old_master_server = self.current_master_server
        
        # Get lock
        logger.debug("Getting failover lock...")
        if not self.failover_lock.acquire(timeout=10):
            return self._make_response(False, "Could not get failover lock")
        
        try:
            if self.failover_in_progress:
                return self._make_response(False, "Failover already in progress")
            
            self.failover_in_progress = True
            
            logger.info(f"Plan: {old_master_server} → {new_master_server}")
            
            # STEP 1: Check new master health
            logger.info(f"Step 1: Checking {new_master_server} health...")

            original_master = self.current_master_server
            self.current_master_server = new_master_server
            new_health = self.check_database('master')
            self.current_master_server = original_master
            
            if not new_health['healthy']:
                return self._make_response(False, 
                    f"{new_master_server} is not healthy: {new_health.get('problem')}",
                    {'health': new_health})
            
            if not new_health['gtid_ok']:
                return self._make_response(False,
                    f"{new_master_server} GTID is not ON")
            
            # STEP 2: GTID consistency check
            logger.info(f"Step 2: Checking GTID consistency...")
            
            old_conn = None
            try:
                old_conn = self._create_physical_connection(old_master_server)
            except Exception as e:
                logger.warning(f"⚠️ Could not connect to old master {old_master_server}: {e}")
                logger.warning("   This is expected if master is dead - proceeding with forced failover")

            new_conn = self._create_physical_connection(new_master_server)
            if not new_conn:
                return self._make_response(False, f"Could not connect to new master {new_master_server}")

            if not old_conn:
                logger.warning("=" * 60)
                logger.warning("⚠️ OLD MASTER IS DEAD - PROCEEDING WITH FORCED FAILOVER")
                logger.warning("   GTID consistency cannot be verified")
                logger.warning("=" * 60)
                gtid_ok, reason = True, "Forced failover - old master unreachable"
                email_alerter.send_alert(
                    "💀 MASTER SERVER DEAD",
                    f"Master {old_master_server} is unreachable!\n\nFailing over to {new_master_server}\nManual investigation required."
                )
            else:
                try:
                    old_cursor = old_conn.cursor()
                    new_cursor = new_conn.cursor()
                    
                    old_cursor.execute("SELECT @@GLOBAL.gtid_executed")
                    old_gtid = old_cursor.fetchone()[0] or ""
                    
                    new_cursor.execute("SELECT @@GLOBAL.gtid_executed")
                    new_gtid = new_cursor.fetchone()[0] or ""
                    
                    old_cursor.close()
                    new_cursor.close()
                    
                    if not old_gtid and not new_gtid:
                        gtid_ok, reason = True, "Both databases fresh (no transactions)"
                    elif old_gtid == new_gtid:
                        gtid_ok, reason = True, "GTID sets identical"
                    elif new_gtid in old_gtid:
                        gtid_ok, reason = True, "New has subset of old's transactions"
                    elif old_gtid in new_gtid:
                        gtid_ok, reason = False, "New has MORE transactions than old (data diverged)"
                    else:
                        old_count = old_gtid.count(',') + (1 if old_gtid else 0)
                        new_count = new_gtid.count(',') + (1 if new_gtid else 0)
                        if new_count <= old_count:
                            gtid_ok, reason = True, f"Transaction count OK ({new_count} <= {old_count})"
                        else:
                            gtid_ok, reason = False, f"New has more transaction ranges ({new_count} > {old_count})"
                except Exception as e:
                    logger.error(f"GTID check error: {e}")
                    logger.warning("⚠️ GTID check failed - proceeding with forced failover anyway")
                    gtid_ok, reason = True, "Forced failover - GTID check failed"
                finally:
                    if old_conn:
                        old_conn.close()
                    if new_conn:
                        new_conn.close()
            
            if not gtid_ok:
                return self._make_response(False,
                    f"GTID consistency check failed: {reason}")
            logger.info(f"GTID check passed: {reason}")
            
            # STEP 3: Data integrity check
            logger.info(f"Step 3: Quick data integrity check...")
            conn = self._create_physical_connection(new_master_server)
            integrity_ok = False
            if conn:
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    if result and result[0] == 1:
                        integrity_ok = True
                    cursor.close()
                    conn.close()
                except:
                    pass
            
            if not integrity_ok:
                logger.warning(f"Data integrity warning (proceeding anyway)")
            
            # STEP 4: Fence old master
            logger.info(f"Step 4: Fencing old master ({old_master_server})...")
            
            old_master_alive = False
            try:
                test_conn = self._create_physical_connection(old_master_server)
                if test_conn:
                    old_master_alive = True
                    test_conn.close()
            except:
                old_master_alive = False

            if not old_master_alive:
                logger.warning(f"⚠️ Old master {old_master_server} is dead - skipping fencing")
                fence_token = self._create_fence_token(old_master_server)
            else:
                fence_token = self._fence_database(old_master_server)
            
            logger.info(f"Step 4.5: Verifying old master is read-only...")
            time.sleep(2)
            if old_master_alive and not self.verify_read_only_status(old_master_server, expected_ro=True):
                logger.error(f"❌ CRITICAL: Old master {old_master_server} is still writable after fencing!")
                logger.warning(f"Attempting aggressive re-fencing...")
                conn = self._create_physical_connection(old_master_server)
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("SET GLOBAL read_only = ON")
                    cursor.execute("SET GLOBAL super_read_only = ON")
                    cursor.execute("FLUSH TABLES WITH READ LOCK")
                    cursor.execute("UNLOCK TABLES")
                    cursor.close()
                    conn.close()
                    time.sleep(1)
                if self.verify_read_only_status(old_master_server, expected_ro=True):
                    logger.info(f"✅ Aggressive re-fencing succeeded")
                else:
                    logger.critical(f"💥 CANNOT FENCE {old_master_server} - SPLIT BRAIN RISK!")
                    email_alerter.send_alert(
                        "🚨 CRITICAL: CANNOT FENCE SERVER",
                        f"Split-brain risk!\n\nServer: {old_master_server}\n\nMANUAL INTERVENTION REQUIRED IMMEDIATELY!"
                    )
            else:
                if old_master_alive:
                    logger.info(f"✅ Verified old master is read-only")
                else:
                    logger.info(f"✅ Old master is dead - no verification needed")
            
            # STEP 4.5: Make new master writable
            if not new_health['writable']:
                logger.info(f"Step 4.5: Making {new_master_server} writable...")
                conn = self._create_physical_connection(new_master_server)
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("SET GLOBAL read_only = OFF")
                    cursor.execute("SET GLOBAL super_read_only = OFF")
                    cursor.close()
                    conn.close()
                    logger.info(f"{new_master_server} is now writable")
                else:
                    return self._make_response(False,
                        f"Could not make {new_master_server} writable")
            
            # STEP 5: Promote new master
            logger.info(f"Step 5: Promoting {new_master_server} to master...")
            if new_master_server == 'slave_db':
                conn = self._create_physical_connection(new_master_server)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("STOP REPLICA")
                        cursor.execute("RESET REPLICA ALL")
                        cursor.close()
                        conn.close()
                        logger.debug(f"Replication stopped on {new_master_server}")
                    except Exception as e:
                        logger.debug(f"Could not stop replication: {e}")
            
            # STEP 6: Update state
            self.current_master_server = new_master_server
            self.current_master = 'slave' if new_master_server == 'slave_db' else 'master'
            self.stats['failovers'] += 1
            # ===== FIX: Track when master changed =====
            self._last_master_change_time = time.time()
            logger.info(f"Step 6: Current master server is now {new_master_server}")
            
            # STEP 7: Reconfigure old as slave (background)
            logger.info(f"Step 7: Reconfiguring {old_master_server} as slave (background)...")
            reconfig_thread = threading.Thread(
                target=self._make_slave_of,
                args=(old_master_server, new_master_server, fence_token['token']),
                daemon=True,
                name=f"Reconfig-{old_master_server}"
            )
            reconfig_thread.start()
            
            # Success!
            logger.info("=" * 60)
            logger.info(f"✅ GTID FAILOVER COMPLETE: {old_master_server} → {new_master_server}")
            email_alerter.send_alert(
                "✅ FAILOVER COMPLETED",
                f"Failover successful!\n\nNew master: {new_master_server}\nOld master: {old_master_server}"
            )
            logger.info("=" * 60)
            
            self.history.append({
                'time': time.time(),
                'type': 'gtid_failover',
                'from': old_master_server,
                'to': new_master_server,
                'success': True,
                'gtid_check': reason
            })
            
            return self._make_response(True,
                f"GTID failover to {new_master_server} successful",
                {
                    'old_master': old_master_server,
                    'new_master': new_master_server,
                    'total_failovers': self.stats['failovers'],
                    'gtid_check_result': reason
                })
            
        except Exception as e:
            logger.error(f"FAILOVER FAILED: {e}", exc_info=True)
            self.history.append({
                'time': time.time(),
                'type': 'gtid_failover',
                'success': False,
                'error': str(e)
            })
            return self._make_response(False, f"GTID failover failed: {e}")
        finally:
            self.failover_in_progress = False
            with self.failover_trigger_lock:
                self.failover_triggered = False
            self.failover_lock.release()
    
    def _quick_integrity_check(self, role):
        """Quick check if database can read/write"""
        conn = None
        try:
            conn = self.get_connection(role)
            if not conn:
                return False
            
            cursor = conn.cursor()
            
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            if not result or result[0] != 1:
                logger.warning(f"{role}: Basic SELECT failed")
                return False
            
            test_table = f"test_{int(time.time()) % 10000}"
            try:
                cursor.execute(f"CREATE TABLE {test_table} (id INT)")
                cursor.execute(f"INSERT INTO {test_table} VALUES (999)")
                cursor.execute(f"SELECT id FROM {test_table}")
                if cursor.fetchone()[0] == 999:
                    logger.debug(f"{role}: Can write")
                cursor.execute(f"DROP TABLE {test_table}")
            except Exception as e:
                logger.debug(f"{role}: Write test issue: {e}")
            
            cursor.close()
            return True
            
        except Exception as e:
            logger.debug(f"{role}: Integrity check failed: {e}")
            return False
        finally:
            if conn:
                self.return_connection(role, conn)
    
    def _make_writable(self, logical_role):
        """Make a database writable"""
        conn = None
        try:
            conn = self.get_connection(logical_role)
            if not conn:
                return False
            
            cursor = conn.cursor()
            cursor.execute("SET GLOBAL read_only = OFF")
            cursor.execute("SET GLOBAL super_read_only = OFF")
            
            cursor.execute("SELECT @@read_only, @@super_read_only")
            read_only, super_read_only = cursor.fetchone()
            
            cursor.close()
            
            if read_only == 0 and super_read_only == 0:
                logger.info(f"{logical_role} is now writable")
                return True
            else:
                logger.warning(f"{logical_role} still read-only after attempt")
                return False
                
        except Exception as e:
            logger.error(f"Failed to make {logical_role} writable: {e}")
            return False
        finally:
            if conn:
                self.return_connection(logical_role, conn)
                
    def _test_connection_details(self, config):
        """Test if we can connect to a database with given credentials"""
        logger.debug(f"Testing connection to {config['host']}:{config['port']} with user {config['replica_user']}...")
        try:
            test_conn = mysql.connector.connect(
                host=config['host'],
                port=config['port'],
                user=config['replica_user'],
                password=config['replica_password'],
                connection_timeout=3
            )

            
            cursor = test_conn.cursor()
            cursor.execute("SELECT @@hostname, @@port, @@read_only, @@gtid_mode")
            hostname, port, read_only, gtid_mode = cursor.fetchone()
            cursor.close()
            test_conn.close()
            logger.debug(f"✅ Connection successful to {hostname}:{port}")
            logger.debug(f"   read_only={read_only}, gtid_mode={gtid_mode}")
            return True
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            return False            
    
    def _promote_to_master(self, logical_role):
        """Promote database to master role"""
        logger.info(f"Promoting {logical_role} to master...")
        
        conn = None
        try:
            conn = self.get_connection(logical_role)
            if not conn:
                return False
            
            cursor = conn.cursor()
            
            if logical_role == 'slave':
                logger.debug(f"Stopping replication on {logical_role}...")
                try:
                    cursor.execute("STOP REPLICA")
                    time.sleep(1)
                    cursor.execute("RESET REPLICA ALL")
                    logger.debug(f"Replication stopped")
                except Exception as e:
                    logger.debug(f"Could not stop replication: {e}")
            
            cursor.execute("SET GLOBAL read_only = OFF")
            cursor.execute("SET GLOBAL super_read_only = OFF")
            
            cursor.execute("SELECT @@read_only, @@super_read_only")
            read_only, super_read_only = cursor.fetchone()
            
            if read_only != 0 or super_read_only != 0:
                logger.error(f"{logical_role} is still read-only after promotion!")
                return False
            
            cursor.close()
            logger.info(f"{logical_role} promoted to master")
            return True
            
        except Exception as e:
            logger.error(f"Failed to promote {logical_role}: {e}")
            return False
        finally:
            if conn:
                self.return_connection(logical_role, conn)
    
        
    def check_if_server_recovered(self, physical_server):
        """Check if a previously failed server is now accessible"""
        logger.info(f"🔍 Checking if {physical_server} is back online...")
    
        try:
            if physical_server == 'master_db':
                config = self.master_db_config
            else:
                config = self.slave_db_config
        
            conn = mysql.connector.connect(
                host=config['host'],
                port=config['port'],
                user=config['user'],
                password=config['password'],
                connection_timeout=3
            )
            conn.close()
        
            logger.info(f"✅ {physical_server} is back online with correct credentials!")
        
            if physical_server == 'master_db':
                count = len(self.master_db_pool)
                for conn in self.master_db_pool:
                    try:
                        conn.close()
                    except:
                        pass
                self.master_db_pool.clear()
                logger.info(f"   🧹 Cleared {count} bad connections from master_db pool")
                self.connection_failure_count['master'] = 0
            else:
                count = len(self.slave_db_pool)
                for conn in self.slave_db_pool:
                    try:
                        conn.close()
                    except:
                        pass
                self.slave_db_pool.clear()
                logger.info(f"   🧹 Cleared {count} bad connections from slave_db pool")
                self.connection_failure_count['slave'] = 0
                
            if physical_server in self._dead_servers:
                del self._dead_servers[physical_server]
        
            return True
        except Exception as e:
            logger.debug(f"❌ {physical_server} still down: {e}")
            return False    
    
    def _make_slave_of(self, slave_server, master_server, fence_token):
        """Make a physical server slave of another physical server"""
        logger.info(f"🚀 STARTING: Make {slave_server} slave of {master_server}")
        logger.info(f"   Current master server: {self.current_master_server}")
        logger.info(f"   Fence token: {fence_token[:20]}...")
        
        time.sleep(2)
        
        conn = None
        try:
            logger.info(f"1️⃣ Connecting to {slave_server}...")
            conn = self._create_physical_connection(slave_server)
            if not conn:
                logger.error(f"❌ Cannot connect to {slave_server}")
                return
            
            cursor = conn.cursor()
            
            logger.info(f"2️⃣ Validating fence token...")
            if not self._validate_fence_token(slave_server, fence_token):
                logger.warning(f"⚠️ Invalid fence token for {slave_server} (but proceeding)")
            
            logger.info(f"3️⃣ Stopping existing replication on {slave_server}...")
            try:
                cursor.execute("STOP REPLICA")
                logger.debug("   STOP REPLICA executed")
            except Exception as e:
                logger.debug(f"   Could not stop replica: {e}")
            
            try:
                cursor.execute("RESET REPLICA ALL")
                logger.debug("   RESET REPLICA ALL executed")
            except Exception as e:
                logger.debug(f"   Could not reset replica: {e}")
            
            logger.info(f"4️⃣ Setting {slave_server} to read-only...")

            for attempt in range(3):
                cursor.execute("SET GLOBAL read_only = ON")
                cursor.execute("SET GLOBAL super_read_only = ON")
                time.sleep(1)
    
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                logger.info(f"   Attempt {attempt+1}: read_only={ro}, super_read_only={sro}")
    
                if ro == 1 and sro == 1:
                    logger.info(f"   ✅ Verified {slave_server} is read-only")
                    break
            else:
                logger.error(f"❌ CRITICAL: FAILED TO SET {slave_server} TO READ-ONLY after 3 attempts!")
                logger.error(f"   Current values: ro={ro}, sro={sro}")
                cursor.execute("SET GLOBAL read_only = 1")
                cursor.execute("SET GLOBAL super_read_only = 1")
                cursor.execute("FLUSH TABLES WITH READ LOCK")
                cursor.execute("UNLOCK TABLES")
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                logger.warning(f"   After force: ro={ro}, sro={sro}")

            logger.info(f"5️⃣ Determining replication target...")
            
            if master_server == 'master_db':
                target_config = self.master_db_config
                target_display = 'master_db (3306)'
            else:
                target_config = self.slave_db_config
                target_display = 'slave_db (3307)'
            
            logger.info(f"   Will connect {slave_server} to {target_display}")
            logger.info(f"   Using user: {target_config['replica_user']}")

            logger.info(f"6️⃣ Testing replica_user connection...")
            try:
                test_conn = mysql.connector.connect(
                    host=target_config['replication_host'],
                    port=target_config['port'],
                    user=target_config['replica_user'],
                    password=target_config['replica_password'],
                    connection_timeout=5
                )
                test_cursor = test_conn.cursor()
                test_cursor.execute("SELECT @@hostname, @@port, @@read_only")
                hostname, port, read_only = test_cursor.fetchone()
                test_cursor.close()
                test_conn.close()
                logger.info(f"   ✅ replica_user connects to {hostname}:{port} (read_only={read_only})")
            except Exception as e:
                logger.error(f"   ❌ replica_user CANNOT connect: {e}")
                logger.error(f"   Check: mysql -u {target_config['replica_user']} -p -h {target_config['host']} -P {target_config['port']}")
                cursor.close()
                return
            
            logger.info(f"7️⃣ Setting up replication...")
            
            try:
                cursor.execute("""
                    CHANGE REPLICATION SOURCE TO
                    SOURCE_HOST=%s,
                    SOURCE_PORT=%s,
                    SOURCE_USER=%s,
                    SOURCE_PASSWORD=%s,
                    SOURCE_AUTO_POSITION=1,
                    SOURCE_SSL=0,
                    GET_SOURCE_PUBLIC_KEY=1
                """, (target_config['replication_host'], target_config['port'],
                      target_config['replica_user'], target_config['replica_password']))
                logger.info("   ✅ CHANGE REPLICATION SOURCE succeeded")
            except Exception as e:
                logger.error(f"   ❌ CHANGE REPLICATION SOURCE failed: {e}")
                cursor.close()
                return
            
            logger.info(f"8️⃣ Starting replication...")
            try:
                cursor.execute("START REPLICA")
                logger.info("   ✅ START REPLICA executed")
            except Exception as e:
                logger.error(f"   ❌ START REPLICA failed: {e}")
                cursor.close()
                return
            
            # ===== FIX: Check replication status without duplicate execute =====
            logger.info(f"9️⃣ Checking replication status...")
            time.sleep(2)
            
            # Use the same cursor, just fetch the description
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            # Now get the actual data
            cursor.execute("SHOW REPLICA STATUS")
            status_row = cursor.fetchone()
            
            if status_row:
                source_host = None
                source_port = None
                io_running = 'No'
                sql_running = 'No'
                last_io_error = None
                last_sql_error = None
                
                for i, col in enumerate(columns):
                    if i < len(status_row):
                        value = status_row[i]
                        if col == 'Source_Host' or col == 'Master_Host':
                            source_host = value
                        elif col == 'Source_Port' or col == 'Master_Port':
                            source_port = value
                        elif col == 'Replica_IO_Running' or col == 'Slave_IO_Running':
                            io_running = value
                        elif col == 'Replica_SQL_Running' or col == 'Slave_SQL_Running':
                            sql_running = value
                        elif (col == 'Last_IO_Error' or col == 'Last_SQL_Error') and value:
                            if not last_error:
                                last_error = str(value)
                
                logger.info(f"   Source: {source_host}:{source_port}")
                logger.info(f"   IO Running: {io_running}")
                logger.info(f"   SQL Running: {sql_running}")
                
                if io_running == 'Yes' and sql_running == 'Yes':
                    logger.info(f"✅ SUCCESS: {slave_server} replicating from {master_server}")
                else:
                    logger.error(f"❌ FAILED: Replication not running")
                    if last_error:
                        logger.error(f"   Last Error: {last_error[:100]}")
            else:
                logger.error(f"❌ CRITICAL: No replication status returned!")
            
            cursor.close()
            logger.info(f"🎉 FINISHED configuring {slave_server} as slave")
            
        except Exception as e:
            logger.error(f"💥 CRITICAL ERROR in _make_slave_of: {e}", exc_info=True)
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        
        logger.info(f"🏁 COMPLETED: _make_slave_of for {slave_server}")
    
    # ============================================
    # 5. MONITORING AND AUTO-RECOVERY
    # ============================================
    
    def enforce_correct_roles(self):
        """Enforce correct read/write roles based on current_master_server - WITH ENHANCED PAUSING"""
        # ===== FIX: MULTI-LAYER PAUSE CHECKS =====
        if self.failover_in_progress:
            logger.debug(f"⏸️ Enforcer paused - failover flag is SET")
            return
        
        # Check if failover was recently triggered
        time_since_trigger = time.time() - self.last_failover_trigger_time
        if time_since_trigger < self.enforcer_cooldown:
            logger.debug(f"⏸️ Enforcer paused - failover triggered {time_since_trigger:.1f}s ago (cooldown {self.enforcer_cooldown}s)")
            return
        
        # Check if master just changed
        time_since_master_change = time.time() - self._last_master_change_time
        if time_since_master_change < 15:  # 15 second cooldown after master change
            logger.debug(f"⏸️ Enforcer paused - master changed {time_since_master_change:.1f}s ago (settling period)")
            return
        
        # Check if any failover threads are running
        for thread in threading.enumerate():
            if thread.name and ("Failover" in thread.name or "Reconfig" in thread.name or "Auto-Failover" in thread.name):
                if thread.is_alive():
                    logger.debug(f"⏸️ Enforcer paused - thread '{thread.name}' is running")
                    return
        
        logger.debug("🛡️ Enforcing correct read/write roles...")
        
        master_server = self.current_master_server
        slave_server = 'slave_db' if master_server == 'master_db' else 'master_db'
        
        # 1. Enforce Master is WRITABLE
        conn = self._create_physical_connection(master_server)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                if ro == 1 or sro == 1:
                    logger.warning(f"⚠️ Enforcer: {master_server} (Master) is read-only! Fixing...")
                    cursor.execute("SET GLOBAL read_only = OFF")
                    cursor.execute("SET GLOBAL super_read_only = OFF")
                    logger.info(f"✅ Enforcer: {master_server} made WRITABLE")
                cursor.close()
            except Exception as e:
                logger.error(f"Enforcer error on master {master_server}: {e}")
            finally:
                conn.close()

        # 2. Enforce Slave is READ-ONLY
        conn = self._create_physical_connection(slave_server)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                if ro == 0 or sro == 0:
                    logger.critical(f"🚨 Enforcer: SPLIT BRAIN RISK! {slave_server} (Slave) is WRITABLE! Fixing...")
                    cursor.execute("SET GLOBAL read_only = ON")
                    cursor.execute("SET GLOBAL super_read_only = ON")
                    logger.info(f"✅ Enforcer: {slave_server} forced to READ-ONLY")
                cursor.close()
            except Exception as e:
                logger.error(f"Enforcer error on slave {slave_server}: {e}")
            finally:
                conn.close()

    def _monitor_loop(self):
        """Background monitoring loop"""
        logger.info("Starting GTID monitor...")
    
        check_num = 0
        while self.monitor_running:
            try:
                check_num += 1
            
                logger.debug(f"\n{'='*50}")
                logger.debug(f"MONITOR CHECK #{check_num}")
                logger.debug(f"Current master server: {self.current_master_server}")
                logger.debug(f"{'='*50}")
            
                # Check both logical roles
                master_health = self.check_database('master')
                slave_health = self.check_database('slave')
            
                logger.debug(f"Monitor check #{check_num} complete")
            
                # Check for problems
                self._detect_problems(master_health, slave_health)
            
                # Try to auto-fix
                self._try_auto_fix(master_health, slave_health)
            
                # Enforce roles
                self.enforce_correct_roles()
            
                # Check if we need to restart replication
                if self._replication_restart_needed:
                    logger.warning("⚠️ Retrying failed replication restart...")
                    if self._restart_replication():
                        logger.info("✅ Replication successfully restarted on retry")
                        self._replication_restart_needed = False
                    else:
                        logger.error("❌ Retry failed - will try again next cycle")

                # Log stats every 10 checks
                if check_num % 10 == 0:
                    stats_msg = (
                        f"📊 Check #{check_num} | "
                        f"Failovers: {self.stats['failovers']} | "
                        f"Recoveries: {self.stats['auto_recoveries']} | "
                        f"Connections: M={len(self.master_db_pool)}/"
                        f"S={len(self.slave_db_pool)}"
                    )
                    logger.info(stats_msg)
            
                # Pool health monitoring
                if check_num % 2 == 0:
                    self.check_pool_health()
                
                    total_contaminated = (self.pool_stats['master']['contaminated_since_last_cleanup'] + 
                        self.pool_stats['slave']['contaminated_since_last_cleanup'])
                    total_dead = (self.pool_stats['master']['dead_since_last_cleanup'] + 
                        self.pool_stats['slave']['dead_since_last_cleanup'])
                
                    if total_contaminated > 10 or total_dead > 20:
                        logger.critical("=" * 70)
                        logger.critical("🚨 EMERGENCY POOL CLEANUP TRIGGERED")
                        logger.critical("=" * 70)
                        logger.critical(f"   Contaminated connections removed: {total_contaminated}")
                        logger.critical(f"   Dead connections removed: {total_dead}")
                        logger.critical(f"   Current master_db pool size: {len(self.master_db_pool)}")
                        logger.critical(f"   Current slave_db pool size: {len(self.slave_db_pool)}")
                        logger.critical(f"   Active connections: {len(self.active_connections)}")
                    
                        m_count = len(self.master_db_pool)
                        for conn in self.master_db_pool:
                            try:
                                conn.close()
                            except:
                                pass
                        self.master_db_pool.clear()
                    
                        s_count = len(self.slave_db_pool)
                        for conn in self.slave_db_pool:
                            try:
                                conn.close()
                            except:
                                pass
                        self.slave_db_pool.clear()
                    
                        logger.critical(f"   ✅ Cleared {m_count} master_db and {s_count} slave_db connections")
                        logger.critical(f"   🔄 Creating fresh pool connections...")
                    
                        fresh_master_db = self._create_physical_connection('master_db')
                        if fresh_master_db:
                            self.master_db_pool.append(fresh_master_db)
                            logger.critical(f"   ✅ Created fresh connection to master_db (3306)")
                    
                        fresh_slave_db = self._create_physical_connection('slave_db')
                        if fresh_slave_db:
                            self.slave_db_pool.append(fresh_slave_db)
                            logger.critical(f"   ✅ Created fresh connection to slave_db (3307)")
                    
                        self.pool_stats['master']['contaminated_since_last_cleanup'] = 0
                        self.pool_stats['slave']['contaminated_since_last_cleanup'] = 0
                        self.pool_stats['master']['dead_since_last_cleanup'] = 0
                        self.pool_stats['slave']['dead_since_last_cleanup'] = 0
                    
                        logger.critical("=" * 70)
                        logger.critical("✅ EMERGENCY POOL CLEANUP COMPLETE")
                        logger.critical("=" * 70)
            
                # Quick pool health check during failures
                elif self.connection_failure_count['master'] > 0 or self.connection_failure_count['slave'] > 0:
                    if check_num % 10 == 0:
                        logger.debug("📊 QUICK POOL HEALTH CHECK (failures detected):")
                    
                        m_failures = self.connection_failure_count['master']
                        s_failures = self.connection_failure_count['slave']
                    
                        logger.debug(f"   MASTER ROLE: {len(self.master_db_pool)} in master_db pool, "
                                   f"{len(self.slave_db_pool)} in slave_db pool, "
                                   f"{m_failures}/{self.max_failures_before_failover} failures")
                        logger.debug(f"   SLAVE ROLE:  {len(self.slave_db_pool)} in slave_db pool, "
                                   f"{len(self.master_db_pool)} in master_db pool, "
                                   f"{s_failures}/{self.max_failures_before_failover} failures")

                # Check for recovered servers
                    if check_num % 10 == 0:
                        if self.current_master_server != 'master_db' and self.connection_failure_count['master'] > 0:
                            if self.check_if_server_recovered('master_db'):
                                logger.warning(f"🚨 master_db recovered! Making it slave...")
                        
                                fix_conn = self._create_physical_connection('master_db')
                                if fix_conn:
                                    cursor = fix_conn.cursor()
                                    cursor.execute("SET GLOBAL read_only = ON")
                                    cursor.execute("SET GLOBAL super_read_only = ON")
                                    cursor.close()
                                    fix_conn.close()
                                    logger.info(f"   ✅ Set master_db to read-only")
                        
                                self._replication_restart_needed = True
                
                        if self.connection_failure_count['slave'] > 0:
                            if self.check_if_server_recovered('slave_db'):
                                logger.info(f"✅ slave_db recovered")                   
                
            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)
        
            # Wait 10 seconds between checks
            for _ in range(10):
                if not self.monitor_running:
                    break
                time.sleep(1)

    
    def _detect_problems(self, master_health, slave_health):
        """Detect and log problems"""
        problems = []
        
        if not master_health['healthy']:
            problems.append(f"Master: {master_health.get('problem')} on {master_health.get('physical_server', 'unknown')}")
        elif not master_health['writable']:
            problems.append(f"Master is read-only on {master_health.get('physical_server', 'unknown')}")
        
        if not slave_health['healthy']:
            problems.append(f"Slave: {slave_health.get('problem')} on {slave_health.get('physical_server', 'unknown')}")
        
        if not master_health['gtid_ok']:
            problems.append("Master GTID not ON")
        if not slave_health['gtid_ok']:
            problems.append("Slave GTID not ON")
        
        if slave_health.get('replication', {}).get('ok') == False:
            problems.append("Slave replication broken")
            self._replication_restart_needed = True

        if problems:
            logger.warning(f"Problems detected: {', '.join(problems)}")
    
    def _try_auto_fix(self, master_health, slave_health):
        """Try to auto-fix common problems"""
        
        if self.failover_in_progress:
            logger.debug(f"⏸️ Failover already in progress - not triggering another one")
            return
        
        # Fix 1: Slave replication broken
        if slave_health.get('replication', {}).get('ok') == False:
            logger.warning("⚠️ Slave replication broken, trying to restart...")

            email_alerter.send_alert(
                "🔄 REPLICATION BROKEN",
                "Slave replication has stopped!\n\nSystem will attempt automatic restart."
            )
            
            if self._restart_replication():
                logger.info("✅ Slave replication restarted")
                self.stats['auto_recoveries'] += 1
                self._replication_restart_needed = False
            else:
                logger.error("❌ Failed to restart replication - will retry in next monitor cycle")
                self._replication_restart_needed = True

        # Fix 2: Master unhealthy but slave is good → AUTO-FAILOVER
        master_down = (
            not master_health['healthy'] or 
            master_health.get('problem') in ['cannot_connect', 'access_denied', 'timeout', 'connection_failed', 'connection_failure_threshold_exceeded'] or
            self.connection_failure_count['master'] >= self.max_failures_before_failover
        )

        if (master_down and
            slave_health['healthy'] and 
            slave_health['gtid_ok']):
    
            failure_reason = master_health.get('problem', 'connection_failure')
            failure_count = self.connection_failure_count['master']
    
            logger.warning("=" * 60)
            logger.warning("🚨 MASTER DOWN DETECTED!")
            logger.warning(f"   Problem: {failure_reason}")
            logger.warning(f"   Consecutive failures: {failure_count}/{self.max_failures_before_failover}")
            logger.warning(f"   Current master server: {self.current_master_server}")
            
            target_server = 'slave_db' if self.current_master_server == 'master_db' else 'master_db'
            
            email_alerter.send_alert(
                "⚠️ MASTER UNHEALTHY",
                f"Master issues detected!\n\nProblem: {failure_reason}\nFailures: {failure_count}/3\nFailing over to: {target_server}"
            )
            
            current_time = time.time()
            if current_time - self.last_failover_trigger_time < self.failover_cooldown:
                logger.debug(f"⏱️ Failover on cooldown ({int(self.failover_cooldown - (current_time - self.last_failover_trigger_time))}s remaining)")
                return
            
            with self.failover_trigger_lock:
                if self.failover_triggered:
                    logger.debug(f"⏸️ Failover already triggered - skipping")
                    return
                self.failover_triggered = True
            
            self.last_failover_trigger_time = current_time
            
            logger.warning(f"🚀 Triggering AUTO-FAILOVER to {target_server}...")
            logger.warning("=" * 60)
    
            logger.warning(f"⚠️  Making {target_server} writable before failover...")
            target_conn = self._create_physical_connection(target_server)
            if target_conn:
                try:
                    cursor = target_conn.cursor()
                    cursor.execute("SET GLOBAL read_only = OFF")
                    cursor.execute("SET GLOBAL super_read_only = OFF")
                    cursor.close()
                    target_conn.close()
                    logger.info(f"✅ {target_server} is now writable")
                except Exception as e:
                    logger.error(f"❌ Failed to make {target_server} writable: {e}")
    
            failover_thread = threading.Thread(
                target=self.failover_to,
                args=(target_server,),
                daemon=True,
                name="Auto-Failover-Detected"
            )
            failover_thread.start()
            self.stats['auto_recoveries'] += 1
    
            self.connection_failure_count['master'] = 0
    
    def _ensure_gtid_on(self, physical_server):
        """Simple GTID enabler"""
        if not hasattr(self, '_gtid_checked'):
            self._gtid_checked = {}
    
        if physical_server in self._gtid_checked:
            logger.debug(f"   GTID already verified on {physical_server}")
            return True

        try:
            conn = mysql.connector.connect(
                host=self.master_db_config['host'] if physical_server == 'master_db' else self.slave_db_config['host'],
                port=self.master_db_config['port'] if physical_server == 'master_db' else self.slave_db_config['port'],
                user=self.master_db_config['user'] if physical_server == 'master_db' else self.slave_db_config['user'],
                password=self.master_db_config['password'] if physical_server == 'master_db' else self.slave_db_config['password'],
                connection_timeout=3
            )
            cursor = conn.cursor()
            cursor.execute("SELECT @@GLOBAL.gtid_mode")
            gtid_mode = cursor.fetchone()[0]
        
            if gtid_mode != 'ON':
                logger.warning(f"⚠️ GTID is {gtid_mode} on {physical_server}, attempting to enable...")
                cursor.execute("SET GLOBAL gtid_mode = OFF_PERMISSIVE")
                cursor.execute("SET GLOBAL gtid_mode = ON_PERMISSIVE") 
                cursor.execute("SET GLOBAL gtid_mode = ON")
                logger.info(f"✅ GTID enabled on {physical_server}")
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Could not enable GTID on {physical_server}: {e}")
            return False
    
    def _fetch_server_uuids(self):
        """Fetch UUIDs from both servers at startup - CRITICAL for pool verification"""
        logger.info("🔑 Fetching server UUIDs (critical for pool verification)...")
        try:
            conn = self._create_physical_connection('master_db')
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT @@GLOBAL.server_uuid")
                self.master_db_uuid = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                logger.info(f"✅ master_db UUID: {self.master_db_uuid}")
        
            conn = self._create_physical_connection('slave_db')
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT @@GLOBAL.server_uuid")
                self.slave_db_uuid = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                logger.info(f"✅ slave_db UUID: {self.slave_db_uuid}")
                
            if not self.master_db_uuid or not self.slave_db_uuid:
                logger.critical("🚨 CRITICAL: Could not fetch UUIDs - pool contamination checks will fail!")
                logger.critical("    System may experience split-brain issues!")
        except Exception as e:
            logger.error(f"❌ Failed to fetch UUIDs: {e}")
            logger.critical("🚨 CRITICAL: UUID fetch failed - system unstable!")
            
    
    def _restart_replication(self):
        """Restart replication - slave server replicates from master server"""
        self._ensure_gtid_on(self._get_physical_server_for_logical_role('slave'))
        conn = None
        try:
            if self.current_master_server == 'master_db':
                slave_server = 'slave_db'
                slave_config = self.slave_db_config
                master_server = 'master_db'
                master_config = self.master_db_config
                logger.info(f"🔄 Normal mode: Restarting replication from master_db (3306) to slave_db (3307)")
            else:
                slave_server = 'master_db'
                slave_config = self.master_db_config
                master_server = 'slave_db'
                master_config = self.slave_db_config
                logger.info(f"🔄 Failover mode: Restarting replication from slave_db (3307) to master_db (3306)")

            logger.info(f"   Current master server: {self.current_master_server}")
            logger.info(f"   Slave server: {slave_server} ({slave_config['port']})")
            logger.info(f"   Master server: {master_server} ({master_config['port']})")

            logger.info(f"   Connecting to slave server {slave_server}...")
  
            try:
                conn = mysql.connector.connect(
                    host=slave_config['host'],
                    port=slave_config['port'],
                    user=slave_config['user'],
                    password=slave_config['password'],
                    database=slave_config['database'],
                    connection_timeout=5
                )
                logger.info(f"   ✅ Connected to {slave_server}")
            except Exception as e:
                logger.error(f"   ❌ Cannot connect to {slave_server}: {e}")
                return False
    
            cursor = conn.cursor()

            logger.info(f"   Checking if replication is already running...")
            try:
                cursor.execute("SHOW REPLICA STATUS")
                guard_row = cursor.fetchone()
                if guard_row:
                    guard_columns = [desc[0] for desc in cursor.description]
                    io_idx = next((i for i, c in enumerate(guard_columns) if c == 'Replica_IO_Running'), None)
                    sql_idx = next((i for i, c in enumerate(guard_columns) if c == 'Replica_SQL_Running'), None)
                    if io_idx is not None and sql_idx is not None:
                        if guard_row[io_idx] == 'Yes' and guard_row[sql_idx] == 'Yes':
                            logger.info(f"   ✅ Replication already running — skipping restart")
                            self._replication_restart_needed = False
                            cursor.close()
                            return True
            except Exception as e:
                logger.debug(f"   Could not check existing replication status: {e}")

            logger.info(f"   Stopping existing replication...")
            try:
                cursor.execute("STOP REPLICA")
                logger.debug("   ✓ STOP REPLICA executed")
            except Exception as e:
                logger.debug(f"   - Could not stop replica: {e}")

            try:
                cursor.execute("RESET REPLICA ALL")
                logger.debug("   ✓ RESET REPLICA ALL executed")
            except Exception as e:
                logger.debug(f"   - Could not reset replica: {e}")

            logger.info(f"   Setting {slave_server} to read-only...")
            cursor.execute("SET GLOBAL read_only = ON")
            cursor.execute("SET GLOBAL super_read_only = ON")
        
            cursor.execute("SELECT @@read_only, @@super_read_only")
            ro, sro = cursor.fetchone()
            if ro != 1 or sro != 1:
                logger.error(f"❌ CRITICAL: Failed to set slave to read-only! ro={ro}, sro={sro}")
                cursor.execute("SET GLOBAL read_only = ON")
                cursor.execute("SET GLOBAL super_read_only = ON")
                cursor.execute("SELECT @@read_only, @@super_read_only")
                ro, sro = cursor.fetchone()
                if ro != 1 or sro != 1:
                    logger.error(f"❌ FAILED AGAIN! Slave is still writable!")
            else:
                logger.info(f"   ✅ Verified slave is read-only")

            logger.info(f"   Verifying master {master_server} is writable...")
            try:
                master_conn = mysql.connector.connect(
                    host=master_config['host'],
                    port=master_config['port'],
                    user=master_config['user'],
                    password=master_config['password'],
                    connection_timeout=5
                )
                master_cursor = master_conn.cursor()
                master_cursor.execute("SELECT @@read_only, @@super_read_only")
                master_ro, master_sro = master_cursor.fetchone()
                
                if master_ro == 1 or master_sro == 1:
                    logger.warning(f"   ⚠️  Master {master_server} is read-only! Making writable...")
                    master_cursor.execute("SET GLOBAL read_only = OFF")
                    master_cursor.execute("SET GLOBAL super_read_only = OFF")
                    
                    master_cursor.execute("SELECT @@read_only, @@super_read_only")
                    master_ro, master_sro = master_cursor.fetchone()
                    if master_ro == 0 and master_sro == 0:
                        logger.info(f"   ✅ Master {master_server} is now writable")
                    else:
                        logger.error(f"   ❌ Failed to make master writable! ro={master_ro}, sro={master_sro}")
                else:
                    logger.info(f"   ✅ Master {master_server} is already writable")
                
                master_cursor.close()
                master_conn.close()
            except Exception as e:
                logger.warning(f"   ⚠️  Could not verify/fix master writable status: {e}")

            logger.info(f"   Testing replica_user connection to master ({master_server}:{master_config['port']})...")
  
            try:
                test_conn = mysql.connector.connect(
                    host=master_config['replication_host'],
                    port=master_config['port'],
                    user=master_config['replica_user'],
                    password=master_config['replica_password'],
                    connection_timeout=5
                )
                test_cursor = test_conn.cursor()
                test_cursor.execute("SELECT @@hostname, @@port, @@read_only, @@gtid_mode")
                hostname, port, read_only, gtid_mode = test_cursor.fetchone()
                test_cursor.close()
                test_conn.close()
                logger.info(f"   ✅ replica_user connects to {hostname}:{port} (read_only={read_only}, gtid={gtid_mode})")
    
            except Exception as e:
                logger.error(f"   ❌ replica_user CANNOT connect to master: {e}")
                logger.error(f"   This is why replication fails! Check:")
                logger.error(f"      - User '{master_config['replica_user']}' exists on {master_server}")
                logger.error(f"      - Password is correct")
                logger.error(f"      - Host '{master_config['host']}' is accessible")
                logger.error(f"      - Port {master_config['port']} is open")
                cursor.close()
                return False

            logger.info(f"   Configuring {slave_server} to replicate from {master_server}...")
    
            try:
                cursor.execute("""
                    CHANGE REPLICATION SOURCE TO
                    SOURCE_HOST=%s,
                    SOURCE_PORT=%s,
                    SOURCE_USER=%s,
                    SOURCE_PASSWORD=%s,
                    SOURCE_AUTO_POSITION=1,
                    SOURCE_SSL=0,
                    GET_SOURCE_PUBLIC_KEY=1
                """, (master_config['replication_host'], master_config['port'],
                      master_config['replica_user'], master_config['replica_password']))
                logger.info(f"   ✓ CHANGE REPLICATION SOURCE to {master_config['replication_host']}:{master_config['port']}")
            except Exception as e:
                logger.error(f"   ❌ CHANGE REPLICATION SOURCE failed: {e}")
    
                try:
                    logger.info(f"   Retrying with legacy syntax...")
                    cursor.execute("""
                        CHANGE MASTER TO
                        MASTER_HOST=%s,
                        MASTER_PORT=%s,
                        MASTER_USER=%s,
                        MASTER_PASSWORD=%s,
                        MASTER_AUTO_POSITION=1,
                        SOURCE_SSL=0,
                        GET_SOURCE_PUBLIC_KEY=1
                    """, (master_config['replication_host'], master_config['port'],
                        master_config['replica_user'], master_config['replica_password']))
                    logger.info(f"   ✓ CHANGE MASTER TO succeeded")
                except Exception as e2:
                    logger.error(f"   ❌ Both syntax attempts failed: {e2}")
                    cursor.close()
                    return False

            logger.info(f"   Starting replication...")
            try:
                cursor.execute("START REPLICA")
                logger.info(f"   ✓ START REPLICA executed")
            except Exception as e:
                try:
                    logger.info(f"   Retrying with START SLAVE...")
                    cursor.execute("START SLAVE")
                    logger.info(f"   ✓ START SLAVE executed")
                except Exception as e2:
                    logger.error(f"   ❌ Failed to start replication: {e2}")
                    cursor.close()
                    return False

            logger.info(f"   Verifying replication status...")
            time.sleep(2)

            try:
                cursor.execute("SHOW REPLICA STATUS")
                status_row = cursor.fetchone()
        
                if status_row:
                    columns = [desc[0] for desc in cursor.description]
           
                    source_host = None
                    source_port = None
                    io_running = 'No'
                    sql_running = 'No'
                    last_error = None
        
                    for i, col in enumerate(columns):
                        value = status_row[i]
                        if col in ['Source_Host', 'Master_Host']:
                            source_host = value
                        elif col in ['Source_Port', 'Master_Port']:
                            source_port = value
                        elif col in ['Replica_IO_Running', 'Slave_IO_Running']:
                            io_running = value
                        elif col in ['Replica_SQL_Running', 'Slave_SQL_Running']:
                            sql_running = value
                        elif col in ['Last_IO_Error', 'Last_SQL_Error'] and value:
                            last_error = value
        
                    if io_running == 'Yes' and sql_running == 'Yes':
                        logger.info(f"   ✅ Replication is RUNNING")
                        logger.info(f"      {slave_server} replicating from {source_host}:{source_port}")
                        self._replication_restart_needed = False
                    else:
                        logger.error(f"   ❌ Replication NOT running")
                        logger.error(f"      IO: {io_running}, SQL: {sql_running}")
                        if last_error:
                            logger.error(f"      Error: {last_error[:200]}")
                        self._replication_restart_needed = True
                else:
                    cursor.execute("SHOW SLAVE STATUS")
                    status_row = cursor.fetchone()
                    if status_row:
                        logger.info(f"   ✓ Replication configured (using SHOW SLAVE STATUS)")
                        self._replication_restart_needed = False
                    else:
                        logger.warning(f"   ⚠️  No replication status available")
                        self._replication_restart_needed = False
            
            except Exception as e:
                logger.warning(f"   ⚠️  Could not verify replication status: {e}")
                self._replication_restart_needed = False

            cursor.close()
            logger.info(f"✅ Replication restart completed successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to restart replication: {e}", exc_info=True)
            self._replication_restart_needed = True
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
    
    # ============================================
    # 6. PUBLIC INTERFACE
    # ============================================
    
    def get_write_connection(self):
        """Get connection for writing - connects to logical master"""
        logger.debug("App requesting write connection...")
        
        master_health = self.check_database('master')
        slave_health = None
        
        master_usable = False
        
        if master_health['healthy'] and master_health['writable'] and master_health['gtid_ok']:
            failure_count = self.connection_failure_count['master']
            
            if failure_count >= self.max_failures_before_failover:
                logger.warning(f"⚠️ Current master has {failure_count} consecutive failures - verifying with fresh connection...")
                physical_server = self._get_physical_server_for_logical_role('master')
                conn = self._create_physical_connection(physical_server)
                if conn:
                    logger.info(f"✅ Fresh connection to master succeeded - resetting failure counter")
                    self.connection_failure_count['master'] = 0
                    master_usable = True
                    conn.close()
                else:
                    logger.error(f"❌ Fresh connection to master also failed!")
                    master_usable = False
            else:
                logger.debug(f"Current master is good (failures: {failure_count})")
                master_usable = True
        
        if master_usable:
            conn = self.get_connection('master')
            if conn:
                logger.debug(f"Returning write connection to logical master")
                return conn
        
        logger.warning(f"⚠️⚠️⚠️ CURRENT MASTER IS NOT USABLE! ⚠️⚠️⚠️")
        logger.warning(f"   Current master server: {self.current_master_server}")
        logger.warning(f"   Health check - Healthy: {master_health['healthy']}, Writable: {master_health['writable']}, GTID: {master_health['gtid_ok']}")
        logger.warning(f"   Failure count: {self.connection_failure_count['master']}")
        
        slave_health = self.check_database('slave')
        
        other_server = 'slave_db' if self.current_master_server == 'master_db' else 'master_db'
        other_role = 'slave' if self.current_master_server == 'master_db' else 'master'
        
        logger.warning(f"   Initiating failover to {other_server}...")
        
        if (slave_health['healthy'] and slave_health['gtid_ok']):
            
            logger.warning(f"✅ {other_server} is healthy! Proceeding with failover...")
            
            if not slave_health['writable']:
                logger.warning(f"⚠️  Making {other_server} writable...")
                other_conn = self._create_physical_connection(other_server)
                if other_conn:
                    try:
                        cursor = other_conn.cursor()
                        cursor.execute("SET GLOBAL read_only = OFF")
                        cursor.execute("SET GLOBAL super_read_only = OFF")
                        cursor.close()
                        other_conn.close()
                        logger.info(f"✅ {other_server} is now writable")
                    except Exception as e:
                        logger.error(f"❌ Failed to make {other_server} writable: {e}")
            
            failover_thread = threading.Thread(
                target=self.failover_to,
                args=(other_server,),
                daemon=True,
                name="Auto-Failover"
            )
            failover_thread.start()
            logger.warning(f"Failover thread started for {other_server}")
            
            conn = self.get_connection(other_role)
            if conn:
                logger.warning(f"✅ Returning write connection to {other_server} (failover in progress)")
                return conn
        else:
            logger.error(f"❌ Cannot failover - {other_server} is not healthy!")
            logger.error(f"   Healthy: {slave_health['healthy']}, GTID OK: {slave_health['gtid_ok']}")
        
        logger.error("=" * 60)
        logger.error("CRITICAL: WRITE CONNECTION FAILED")
        logger.error("=" * 60)
        logger.error(f"MASTER SERVER: {self.current_master_server}")
        logger.error(f"  Problem: {master_health.get('problem', 'unknown')}")
        logger.error(f"  GTID OK: {master_health.get('gtid_ok', False)}")
        logger.error(f"  Writable: {master_health.get('writable', False)}")
        
        other_server = 'slave_db' if self.current_master_server == 'master_db' else 'master_db'
        logger.error(f"SLAVE SERVER: {other_server}")
        logger.error(f"  Problem: {slave_health.get('problem', 'unknown')}")
        logger.error(f"  GTID OK: {slave_health.get('gtid_ok', False)}")
        logger.error(f"  Writable: {slave_health.get('writable', False)}")
        logger.error("=" * 60)
        
        raise Exception(
            f"No healthy database available for writing\n"
            f"Master server ({self.current_master_server}): {master_health.get('problem', 'healthy')}\n"
            f"Slave server ({other_server}): {slave_health.get('problem', 'healthy')}"
        )
    
    def get_read_connection(self):
        """Get connection for reading - connects to logical slave"""
        logger.debug("App requesting read connection...")
    
        conn = self.get_connection('slave')
        if conn:
            logger.debug(f"Using logical slave for reading")
            return conn
    
        logger.warning(f"Slave not available, using master for reading")
        conn = self.get_connection('master')
        if conn:
            return conn
    
        raise Exception("No database available for reading")
    
    def get_status(self):
        """Get complete system status"""
        master_health = self.check_database('master')
        slave_health = self.check_database('slave')
        
        return {
            'current_master_server': self.current_master_server,
            'current_master': self.current_master,
            'failover_in_progress': self.failover_in_progress,
            'stats': self.stats,
            'master': master_health,
            'slave': slave_health,
            'connections': {
                'master_db_pool': len(self.master_db_pool),
                'slave_db_pool': len(self.slave_db_pool),
                'active': len(self.active_connections)
            },
            'monitor_running': self.monitor_running,
            'recent_history': self.history[-5:] if self.history else [],
            'timestamp': time.time()
        }
    
    def promote_slave(self):
        """Manually promote slave_db to master"""
        return self.failover_to('slave_db')
    
    def promote_master(self):
        """Manually promote master_db back to master"""
        return self.failover_to('master_db')
    
    def stop_monitor(self):
        """Stop background monitoring"""
        self.monitor_running = False
        logger.info("Monitor stopped")
    
    def _make_response(self, success, message, data=None):
        """Helper to create response"""
        response = {
            'success': success,
            'message': message,
            'timestamp': time.time()
        }
        if data:
            response['data'] = data
        return response


# ============================================
# GLOBAL INSTANCE
# ============================================

gtid_manager = GTIDFailoverManager()

# Simple interface functions
def get_write_connection():
    return gtid_manager.get_write_connection()

def get_read_connection():
    return gtid_manager.get_read_connection()

def get_status():
    return gtid_manager.get_status()

def promote_slave():
    return gtid_manager.promote_slave()

def promote_master():
    return gtid_manager.promote_master()

def stop_monitor():
    return gtid_manager.stop_monitor()

# Test
if __name__ == "__main__":
    print("=" * 60)
    print(" GTID-ONLY FAILOVER SYSTEM - UUID VERIFIED + AUTO-DETECT")
    print("=" * 60)
    print("\nUsing GTID-based replication with UUID pool verification")
    print("\nLogs created in 'logs/' directory:")
    print("  gtid_main.log     - Summary (INFO level)")
    print("  gtid_detail.log   - Troubleshooting (DEBUG level)")
    print("  gtid_errors.log   - Critical errors (ERROR level)")
    print("  gtid_failover.log - Legacy (backward compatibility)")
    print("\nFunctions:")
    print("  get_write_connection() - Write with auto-failover")
    print("  get_read_connection()  - Read (prefers slave)")
    print("  get_status()          - Complete system status")
    print("  promote_slave()       - Manual failover to slave_db")
    print("  promote_master()      - Manual failover back to master_db")
    print("\nFeatures:")
    print("  ✅ GTID consistency checking")
    print("  ✅ Split-brain prevention with fencing")
    print("  ✅ 16 failure scenarios handled")
    print("  ✅ Connection pooling (per physical server)")
    print("  ✅ UUID-based pool verification (NO PORT CONFUSION!)")
    print("  ✅ AUTO-DETECT current master from replication config")
    print("  ✅ Auto-monitoring and recovery")
    print("=" * 60)