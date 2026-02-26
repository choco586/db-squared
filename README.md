
## ✨ Key Features

### 🔄 **Intelligent Failover**
- **GTID-based replication** with automatic master detection
- **UUID verification** prevents connection pool contamination in Docker
- **Split-brain prevention** with fencing tokens and multi-layer enforcement
- **Auto-detection** of current master from replication configs at startup
- **Connection pooling** with contamination tracking (10 connections max)
- **Dead server detection** with 30-second cooldown periods
- **Emergency pool cleanup** triggered by thresholds
- **Multi-layer enforcer** with failover cooldowns and master change settling 

###  **Comprehensive Monitoring**
- **5 logging levels** with rotation:
  - `gtid_main.log` - Daily monitoring summary
  - `gtid_detail.log` - Troubleshooting details
  - `gtid_errors.log` - Critical issues only
  - Console output - Real-time logs
  - Legacy compatibility - `gtid_failover.log`
- **Email alerts** for failover events (WIP)


##  Technology Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python 3.14+, Flask |
| **Database** | MySQL 8.0 (Master: 3306, Slave: 3307) |
| **Replication** | GTID-based with auto-positioning |
| **Frontend** | HTML/CSS/JavaScript (React-ready) |
| **Infrastructure** | Docker, Kafka/Debezium (WIP) |
| **Backup** | Custom Python + mysqldump |


