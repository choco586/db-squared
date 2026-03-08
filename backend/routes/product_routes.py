# backend/routes/product_routes.py

from flask import jsonify, request, g
from db import get_write_connection, get_read_connection, return_connection, ValidationError, Validator


def get_products():
    """
    Returns all products belonging to the logged-in user's business.
    Ordered by product_id so the list is stable and predictable.
    """
    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT product_id, name, supplier, price,
                   expiry_date, stock_quantity, created_at
            FROM   products
            WHERE  business_unit_id = %s
            ORDER  BY product_id ASC
            """,
            (g.business_unit_id,)
        )
        products = cursor.fetchall()
        cursor.close()
        return jsonify({'success': True, 'products': products})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load products'}), 500
    finally:
        return_connection(conn)


def add_product():
    """
    Add a new product to the logged-in user's business.

    Body: { name, supplier, price, stock_quantity, expiry_date (optional) }

    product_id is auto-incremented per business using MAX(product_id) + 1,
    same pattern as customer_id — each business has its own numbering from 1.
    """
    data = request.json or {}

    try:
        validated = Validator.validate_product(data)
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Get next product_id for this business
        cursor.execute(
            "SELECT COALESCE(MAX(product_id), 0) + 1 AS next_id FROM products WHERE business_unit_id = %s",
            (g.business_unit_id,)
        )
        next_id = cursor.fetchone()['next_id']

        cursor.execute(
            """
            INSERT INTO products
                (business_unit_id, product_id, name, supplier, price, expiry_date, stock_quantity)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.business_unit_id, next_id,
                validated['name'],     validated['supplier'],
                validated['price'],    validated['expiry_date'],
                validated['stock_quantity'],
            )
        )
        conn.commit()
        cursor.close()

        return jsonify({
            'success':    True,
            'product_id': next_id,
        }), 201

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to add product'}), 500
    finally:
        return_connection(conn)


def update_product(product_id):
    """
    Update a product within the logged-in user's business.

    Body: { name, supplier, price, stock_quantity, expiry_date (optional) }

    All fields are required on update — this is a full replace, not a patch.
    WHERE clause always includes business_unit_id for isolation.
    """
    data = request.json or {}

    try:
        validated = Validator.validate_product(data)
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor()

        # Confirm product exists in this business
        cursor.execute(
            """
            SELECT product_id FROM products
            WHERE  business_unit_id = %s AND product_id = %s
            """,
            (g.business_unit_id, product_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'Product not found'}), 404

        cursor.execute(
            """
            UPDATE products
            SET    name = %s, supplier = %s, price = %s,
                   expiry_date = %s, stock_quantity = %s
            WHERE  business_unit_id = %s AND product_id = %s
            """,
            (
                validated['name'],     validated['supplier'],
                validated['price'],    validated['expiry_date'],
                validated['stock_quantity'],
                g.business_unit_id,    product_id,
            )
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to update product'}), 500
    finally:
        return_connection(conn)


def delete_product(product_id):
    """
    Admin only (enforced in app.py).
    Delete a product from the logged-in user's business.

    If the product is referenced in any order_items row, MySQL will reject
    the delete with a FK constraint error — caught and returned as a clear 400.
    """
    conn = get_write_connection()
    try:
        cursor = conn.cursor()

        # Confirm product exists in this business
        cursor.execute(
            """
            SELECT product_id FROM products
            WHERE  business_unit_id = %s AND product_id = %s
            """,
            (g.business_unit_id, product_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'success': False, 'error': 'Product not found'}), 404

        cursor.execute(
            """
            DELETE FROM products
            WHERE  business_unit_id = %s AND product_id = %s
            """,
            (g.business_unit_id, product_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True})

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        if 'foreign key constraint' in str(e).lower():
            return jsonify({
                'success': False,
                'error':   'Cannot delete product — it appears in existing orders. '
                           'Remove it from all orders first.'
            }), 400
        return jsonify({'success': False, 'error': 'Failed to delete product'}), 500
    finally:
        return_connection(conn)
