from flask import jsonify, request
from db import get_write_connection, get_read_connection  

def get_users():
    conn = get_read_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(users)

def add_user():
    data = request.json
    
    # VALIDATION ADDED
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    
    if not name or len(name) < 2:
        return jsonify({"success": False, "error": "Name must be at least 2 characters"}), 400
    
    if not email or '@' not in email or '.' not in email:
        return jsonify({"success": False, "error": "Invalid email address"}), 400
    
    # Also validate email doesn't exist already
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

def update_user(user_id):
    data = request.json
    conn = get_write_connection()  
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET name=%s, email=%s WHERE id=%s",
                   (data['name'], data['email'], user_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})

def delete_user(user_id):
    conn = get_write_connection()  
    cursor = conn.cursor() 
    cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})