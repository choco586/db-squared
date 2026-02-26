from flask import jsonify, request
from db import get_write_connection, get_read_connection

def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    
    conn = get_read_connection()  
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email FROM users WHERE name=%s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if user:
        return jsonify({"success": True, "user": user})
    return jsonify({"success": False, "error": "User not found"}), 404

def signup():
    data = request.json
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    
    if not name or not email or not password:
        return jsonify({"success": False, "error": "All fields required"}), 400
    
    if '@' not in email or '.' not in email:
        return jsonify({"success": False, "error": "Invalid email"}), 400
    
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password too short"}), 400
    
    conn = get_write_connection()  
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({"success": False, "error": "Email already exists"}), 400
    
    cursor.execute("INSERT INTO users (name, email) VALUES (%s, %s)", (name, email))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({"success": True})