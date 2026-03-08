# backend/routes/user_routes.py

from flask import jsonify, request, g
from db import get_write_connection, get_read_connection, return_connection, ValidationError, Validator


def get_users():
    """
    Admin only (enforced in app.py).
    Returns all users belonging to the logged-in admin's business.
    Never returns password_hash — only safe fields.
    """
    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, username, role, active, created_at
            FROM   users
            WHERE  business_unit_id = %s
            ORDER  BY role DESC, username ASC
            """,
            (g.business_unit_id,)
        )
        users = cursor.fetchall()
        cursor.close()
        return jsonify({'success': True, 'users': users})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load users'}), 500
    finally:
        return_connection(conn)


def add_user():
    """
    Admin only (enforced in app.py).
    Creates a new employee account within the admin's business.

    Body: { username, password, role }

    Rules:
    - username must be unique within this business (not globally)
    - role must be 'admin' or 'employee'
    - admin can create another admin (e.g. backup admin) — no restriction here
    - business_unit_id always comes from g, never from the request body
    """
    data = request.json or {}

    try:
        username = Validator.validate_username(data.get('username'))
        password = Validator.validate_password(data.get('password'))
        role     = Validator.validate_role(data.get('role', 'employee'))
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    password_hash = Validator.hash_password(password)

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Username must be unique within this business
        cursor.execute(
            """
            SELECT id FROM users
            WHERE  business_unit_id = %s AND username = %s
            """,
            (g.business_unit_id, username)
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'Username already exists in this business'}), 400

        cursor.execute(
            """
            INSERT INTO users (business_unit_id, username, password_hash, role)
            VALUES (%s, %s, %s, %s)
            """,
            (g.business_unit_id, username, password_hash, role)
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()

        return jsonify({
            'success': True,
            'user': {
                'id':       new_id,
                'username': username,
                'role':     role,
                'active':   True,
            }
        }), 201

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to create user'}), 500
    finally:
        return_connection(conn)


def update_user(user_id):
    """
    Admin: can update any user in their business.
    Employee: can only update themselves (enforced in app.py).

    Body: { username?, password? }
    At least one field must be provided.

    Rules:
    - Can only update users belonging to the same business (WHERE clause includes
      business_unit_id — prevents updating users from other businesses even if
      somehow the wrong user_id is sent).
    - If changing username, check it doesn't clash with another user in the business.
    - Password is optional — if not sent, it stays unchanged.
    """
    data = request.json or {}

    new_username = data.get('username')
    new_password = data.get('password')

    if not new_username and not new_password:
        return jsonify({'success': False, 'error': 'Nothing to update — provide username or password'}), 400

    try:
        if new_username:
            new_username = Validator.validate_username(new_username)
        if new_password:
            new_password = Validator.validate_password(new_password)
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Confirm user exists in this business
        cursor.execute(
            """
            SELECT id, username FROM users
            WHERE  id = %s AND business_unit_id = %s
            """,
            (user_id, g.business_unit_id)
        )
        existing = cursor.fetchone()
        if not existing:
            cursor.close()
            return jsonify({'success': False, 'error': 'User not found'}), 404

        # If changing username, check for clash within the same business
        if new_username and new_username != existing['username']:
            cursor.execute(
                """
                SELECT id FROM users
                WHERE  business_unit_id = %s AND username = %s AND id != %s
                """,
                (g.business_unit_id, new_username, user_id)
            )
            if cursor.fetchone():
                cursor.close()
                return jsonify({'success': False, 'error': 'Username already taken in this business'}), 400

        # Build update dynamically — only update fields that were provided
        fields = []
        values = []

        if new_username:
            fields.append('username = %s')
            values.append(new_username)

        if new_password:
            fields.append('password_hash = %s')
            values.append(Validator.hash_password(new_password))

        values.append(user_id)
        values.append(g.business_unit_id)

        cursor.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE id = %s AND business_unit_id = %s",
            tuple(values)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to update user'}), 500
    finally:
        return_connection(conn)


def delete_user(user_id):
    """
    Admin only (enforced in app.py).
    Deletes a user from the admin's business.

    The self-delete guard (admin cannot delete themselves) is enforced in app.py
    before this function is called, so we don't repeat it here.

    WHERE clause includes business_unit_id — ensures an admin can only delete
    users from their own business, even if a wrong user_id is passed.
    """
    conn = get_write_connection()
    try:
        cursor = conn.cursor()

        # Confirm the user exists in this business before deleting
        cursor.execute(
            "SELECT id FROM users WHERE id = %s AND business_unit_id = %s",
            (user_id, g.business_unit_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'User not found'}), 404

        cursor.execute(
            "DELETE FROM users WHERE id = %s AND business_unit_id = %s",
            (user_id, g.business_unit_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to delete user'}), 500
    finally:
        return_connection(conn)


def toggle_user(user_id):
    """
    Admin only (enforced in app.py).
    Enable or disable a user account within the admin's business.

    Body: { active: true | false }

    Sets users.active = 1 or 0.
    A disabled user will be blocked at login by the active check in auth_routes.
    The self-toggle guard is enforced in app.py before this function is called.
    """
    data = request.json or {}

    if 'active' not in data:
        return jsonify({'success': False, 'error': "'active' field is required (true or false)"}), 400

    active = bool(data['active'])

    conn = get_write_connection()
    try:
        cursor = conn.cursor()

        # Confirm user exists in this business
        cursor.execute(
            "SELECT id FROM users WHERE id = %s AND business_unit_id = %s",
            (user_id, g.business_unit_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'User not found'}), 404

        cursor.execute(
            "UPDATE users SET active = %s WHERE id = %s AND business_unit_id = %s",
            (1 if active else 0, user_id, g.business_unit_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({
            'success': True,
            'message': f"User {'enabled' if active else 'disabled'} successfully",
        })

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to update user status'}), 500
    finally:
        return_connection(conn)
