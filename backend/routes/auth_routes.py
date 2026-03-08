# backend/routes/auth_routes.py

from flask import jsonify, request
from db import get_write_connection, get_read_connection, return_connection, ValidationError, Validator


def register():
    """
    Register a new business + first admin user.

    Body: { business_name, username, password }

    Logic:
      1. Validate all inputs.
      2. Check business_name does NOT already exist — if it does, that business
         already has an admin and new users must be added by that admin, not here.
      3. Insert into businesses.
      4. Insert into users with role='admin' and the new business_unit_id.

    Two queries need to succeed together, so we use a single write connection
    and commit only after both inserts succeed. If the second insert fails we
    roll back so we don't leave an empty business row with no admin.
    """
    data = request.json or {}

    # ── Validate inputs ────────────────────────────────────────────────────────
    try:
        business_name = Validator.validate_business_name(data.get('business_name'))
        username      = Validator.validate_username(data.get('username'))
        password      = Validator.validate_password(data.get('password'))
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    password_hash = Validator.hash_password(password)

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # ── Step 1: Check business does not already exist ──────────────────────
        cursor.execute(
            "SELECT id FROM businesses WHERE name = %s",
            (business_name,)
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({
                'success': False,
                'error':   'This business is already registered. '
                           'Ask your admin to create an account for you.'
            }), 400

        # ── Step 2: Insert business ────────────────────────────────────────────
        cursor.execute(
            "INSERT INTO businesses (name) VALUES (%s)",
            (business_name,)
        )
        business_unit_id = cursor.lastrowid

        # ── Step 3: Insert admin user ──────────────────────────────────────────
        # username uniqueness is per-business (business_unit_id + username)
        cursor.execute(
            """
            INSERT INTO users (business_unit_id, username, password_hash, role)
            VALUES (%s, %s, %s, 'admin')
            """,
            (business_unit_id, username, password_hash)
        )

        conn.commit()
        cursor.close()

        return jsonify({
            'success':  True,
            'message':  f"Business '{business_name}' registered. You can now log in.",
        }), 201

    except Exception as e:
        # Roll back both inserts if anything failed
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Registration failed. Please try again.'}), 500
    finally:
        return_connection(conn)


def login():
    """
    Login for all users (admin and employee).

    Body: { business_name, username, password }

    Logic:
      1. Validate inputs.
      2. Find business by name — if not found, vague error (don't confirm
         whether the business or the user is wrong, prevents enumeration).
      3. Find user by (business_unit_id, username) — composite lookup because
         usernames are unique per business, not globally.
      4. Compare SHA-256 hash of submitted password against stored hash.
      5. Check user is active (not disabled by admin).
      6. Return user info — frontend stores this and sends it as headers
         on every subsequent request.

    We use get_read_connection() because login only reads, never writes.
    """
    data = request.json or {}

    # ── Validate inputs ────────────────────────────────────────────────────────
    try:
        business_name = Validator.validate_business_name(data.get('business_name'))
        username      = Validator.validate_username(data.get('username'))
        password      = Validator.validate_password(data.get('password'))
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    password_hash = Validator.hash_password(password)

    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # ── Step 1: Find business ──────────────────────────────────────────────
        cursor.execute(
            "SELECT id FROM businesses WHERE name = %s",
            (business_name,)
        )
        business = cursor.fetchone()
        if not business:
            cursor.close()
            # Vague error — don't tell them whether business or password is wrong
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

        business_unit_id = business['id']

        # ── Step 2: Find user within that business ─────────────────────────────
        cursor.execute(
            """
            SELECT id, username, role, business_unit_id, active
            FROM   users
            WHERE  business_unit_id = %s
            AND    username         = %s
            AND    password_hash    = %s
            """,
            (business_unit_id, username, password_hash)
        )
        user = cursor.fetchone()
        cursor.close()

        if not user:
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

        # ── Step 3: Check account is active ───────────────────────────────────
        if not user['active']:
            return jsonify({
                'success': False,
                'error':   'Your account has been disabled. Contact your admin.'
            }), 403

        # ── Step 4: Return user info + business name ───────────────────────────
        # Frontend will store these and send them as:
        #   X-User-Id, X-Business-Id, X-Role headers on every request.
        return jsonify({
            'success': True,
            'user': {
                'id':               user['id'],
                'username':         user['username'],
                'role':             user['role'],
                'business_unit_id': user['business_unit_id'],
                'business_name':    business_name,
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': 'Login failed. Please try again.'}), 500
    finally:
        return_connection(conn)
