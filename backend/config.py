import os
from dotenv import load_dotenv

load_dotenv()

# Replica User for Replication (same on both databases)
REPLICA_USER = os.getenv('REPLICA_USER', 'replica_user')
REPLICA_PASSWORD = os.getenv('REPLICA_PASSWORD', 'replica_user123')

# Master Database Config (for writes)
MASTER_DB_HOST = os.getenv('MASTER_HOST', 'localhost')
MASTER_DB_PORT = int(os.getenv('MASTER_PORT', 3306))
MASTER_DB_USER = os.getenv('MASTER_USER')
MASTER_DB_PASSWORD = os.getenv('MASTER_PASSWORD')
MASTER_DB_NAME = os.getenv('MASTER_DB', 'testdb')

# Slave Database Config (for reads)
SLAVE_DB_HOST = os.getenv('SLAVE_HOST', '127.0.0.1')
SLAVE_DB_PORT = int(os.getenv('SLAVE_PORT', 3307))
SLAVE_DB_USER = os.getenv('SLAVE_USER')
SLAVE_DB_PASSWORD = os.getenv('SLAVE_PASSWORD')
SLAVE_DB_NAME = os.getenv('SLAVE_DB', 'testdb')

# Host used inside CHANGE REPLICATION SOURCE TO.
# When the slave container runs CHANGE REPLICATION SOURCE, it needs an address
# that reaches the host machine's MySQL from inside Docker — not 'localhost'
# which would resolve to the container itself.
# Mac/Windows: host.docker.internal  |  Linux: your docker bridge IP (e.g. 172.17.0.1)
MASTER_REPLICATION_HOST = os.getenv('MASTER_REPLICATION_HOST', 'host.docker.internal')