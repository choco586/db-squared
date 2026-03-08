# backend/routes/customer_routes.py

from flask import jsonify, request, g
from db import get_write_connection, get_read_connection, return_connection, ValidationError, Validator


def get_customers():
    """
    Returns all customers belonging to the logged-in user's business.
    Ordered by customer_id so the list is stable and predictable.
    """
    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT customer_id, name, email, phone, created_at
            FROM   customers
            WHERE  business_unit_id = %s
            ORDER  BY customer_id ASC
            """,
            (g.business_unit_id,)
        )
        customers = cursor.fetchall()
        cursor.close()
        return jsonify({'success': True, 'customers': customers})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load customers'}), 500
    finally:
        return_connection(conn)


def add_customer():
    """
    Add a new customer to the logged-in user's business.

    Body: { name, email, phone (optional) }

    customer_id is auto-incremented PER BUSINESS using MAX(customer_id) + 1.
    This gives each business its own numbering starting at 1, which matches
    the composite PRIMARY KEY (business_unit_id, customer_id) in the schema.

    Email uniqueness is checked within the business only.
    """
    data = request.json or {}

    try:
        validated = Validator.validate_customer(data)
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Check email doesn't already exist within this business
        cursor.execute(
            """
            SELECT customer_id FROM customers
            WHERE  business_unit_id = %s AND email = %s
            """,
            (g.business_unit_id, validated['email'])
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'A customer with this email already exists'}), 400

        # Get next customer_id for this business
        # COALESCE handles the case where this is the first customer (returns 0, so +1 = 1)
        cursor.execute(
            "SELECT COALESCE(MAX(customer_id), 0) + 1 AS next_id FROM customers WHERE business_unit_id = %s",
            (g.business_unit_id,)
        )
        next_id = cursor.fetchone()['next_id']

        cursor.execute(
            """
            INSERT INTO customers (business_unit_id, customer_id, name, email, phone)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (g.business_unit_id, next_id, validated['name'], validated['email'], validated['phone'])
        )
        conn.commit()
        cursor.close()

        return jsonify({
            'success':     True,
            'customer_id': next_id,
        }), 201

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to add customer'}), 500
    finally:
        return_connection(conn)


def update_customer(customer_id):
    """
    Update a customer's details within the logged-in user's business.

    Body: { name?, email?, phone? }

    WHERE clause always includes business_unit_id — a user from business 1
    cannot update customer_id=5 from business 2 even if they craft the URL.
    """
    data = request.json or {}

    try:
        validated = Validator.validate_customer(data)
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Confirm customer exists in this business
        cursor.execute(
            """
            SELECT customer_id FROM customers
            WHERE  business_unit_id = %s AND customer_id = %s
            """,
            (g.business_unit_id, customer_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        # If changing email, check it doesn't clash with another customer in this business
        cursor.execute(
            """
            SELECT customer_id FROM customers
            WHERE  business_unit_id = %s AND email = %s AND customer_id != %s
            """,
            (g.business_unit_id, validated['email'], customer_id)
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'A customer with this email already exists'}), 400

        cursor.execute(
            """
            UPDATE customers
            SET    name = %s, email = %s, phone = %s
            WHERE  business_unit_id = %s AND customer_id = %s
            """,
            (validated['name'], validated['email'], validated['phone'],
             g.business_unit_id, customer_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to update customer'}), 500
    finally:
        return_connection(conn)


def delete_customer(customer_id):
    """
    Admin only (enforced in app.py).
    Delete a customer from the logged-in user's business.

    Important: the schema has a FK from orders → customers including
    business_unit_id. If this customer has orders, MySQL will reject the
    delete with a FK constraint error. We catch that specifically and return
    a clear message instead of a generic 500.
    """
    conn = get_write_connection()
    try:
        cursor = conn.cursor()

        # Confirm customer exists in this business
        cursor.execute(
            """
            SELECT customer_id FROM customers
            WHERE  business_unit_id = %s AND customer_id = %s
            """,
            (g.business_unit_id, customer_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        cursor.execute(
            """
            DELETE FROM customers
            WHERE  business_unit_id = %s AND customer_id = %s
            """,
            (g.business_unit_id, customer_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        # FK constraint violation — customer has existing orders
        if 'foreign key constraint' in str(e).lower():
            return jsonify({
                'success': False,
                'error':   'Cannot delete customer — they have existing orders. '
                           'Cancel or complete their orders first.'
            }), 400
        return jsonify({'success': False, 'error': 'Failed to delete customer'}), 500
    finally:
        return_connection(conn)
