from flask import Flask, jsonify, request
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from flask_cors import CORS
from routes.user_routes import get_users, add_user, update_user, delete_user
from routes.auth_routes import login, signup
from config import (
    MASTER_DB_HOST, MASTER_DB_PORT, MASTER_DB_USER, 
    MASTER_DB_PASSWORD, MASTER_DB_NAME,
    SLAVE_DB_HOST, SLAVE_DB_PORT, SLAVE_DB_USER, 
    SLAVE_DB_PASSWORD, SLAVE_DB_NAME
)

app = Flask(__name__)
CORS(app)  # Allow React on port 3000 to connect

# API Routes
@app.route('/api/users', methods=['GET'])
def api_get_users():
    return get_users()

@app.route('/api/users', methods=['POST'])
def api_add_user():
    return add_user()

@app.route('/api/users/<int:user_id>', methods=['PUT'])
def api_update_user(user_id):
    return update_user(user_id)

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    return delete_user(user_id)

@app.route('/api/login', methods=['POST'])
def api_login():
    return login()

@app.route('/api/signup', methods=['POST'])
def api_signup():
    return signup()

@app.route('/api/admin/health', methods=['GET'])
def api_admin_health():
    """Admin-only health check for databases"""
    # Simple admin authentication
    auth = request.headers.get('Authorization')
    if not auth or auth != 'admin123':
        return jsonify({"error": "Unauthorized"}), 401
    
    health_report = {
        "timestamp": datetime.now().isoformat(),
        "databases": {}
    }
    
    # Check master
    try:
        master_conn = mysql.connector.connect(
            host=MASTER_DB_HOST,
            port=MASTER_DB_PORT,
            user=MASTER_DB_USER,
            password=MASTER_DB_PASSWORD,
            database=MASTER_DB_NAME
        )
        master_cursor = master_conn.cursor()
        master_cursor.execute("SELECT 1 as status, @@hostname as hostname")
        master_result = master_cursor.fetchone()
        master_conn.close()
        health_report["databases"]["master"] = {
            "status": "UP",
            "hostname": master_result[1],
            "port": MASTER_DB_PORT
        }
    except Error as e:
        health_report["databases"]["master"] = {
            "status": "DOWN",
            "error": str(e),
            "port": MASTER_DB_PORT
        }
    
    # Check slave
    try:
        slave_conn = mysql.connector.connect(
            host=SLAVE_DB_HOST,
            port=SLAVE_DB_PORT,
            user=SLAVE_DB_USER,
            password=SLAVE_DB_PASSWORD,
            database=SLAVE_DB_NAME
        )
        slave_cursor = slave_conn.cursor()
        slave_cursor.execute("SELECT 1 as status, @@hostname as hostname")
        slave_result = slave_cursor.fetchone()
        slave_conn.close()
        health_report["databases"]["slave"] = {
            "status": "UP",
            "hostname": slave_result[1],
            "port": SLAVE_DB_PORT
        }
    except Error as e:
        health_report["databases"]["slave"] = {
            "status": "DOWN",
            "error": str(e),
            "port": SLAVE_DB_PORT
        }
    
    # Overall status
    master_up = health_report["databases"]["master"]["status"] == "UP"
    slave_up = health_report["databases"]["slave"]["status"] == "UP"
    
    if master_up and slave_up:
        health_report["overall"] = "HEALTHY"
    elif master_up or slave_up:
        health_report["overall"] = "DEGRADED"
    else:
        health_report["overall"] = "DOWN"
    
    return jsonify(health_report)

@app.route('/')
def home():
    return jsonify({"message": "Backend API running on port 5000"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)  # Backend on port 5000