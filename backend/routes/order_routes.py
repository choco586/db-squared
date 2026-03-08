# backend/routes/order_routes.py

from flask import jsonify, request, g
from db import get_write_connection, get_read_connection, return_connection, ValidationError, Validator


def get_orders():
    """
    Returns all orders for the logged-in user's business.
    Includes customer name for display — joined on (business_unit_id, customer_id)
    to ensure we only ever join within the same business.

    Admin sees all orders.
    Employee sees all orders too — they need to see the full order list to
    pick up and process orders. Log filtering (own vs all) is separate in log_routes.
    """
    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT  o.order_id,
                    o.customer_id,
                    c.name        AS customer_name,
                    o.order_date,
                    o.total_amount,
                    o.status
            FROM    orders o
            JOIN    customers c
                ON  o.business_unit_id = c.business_unit_id
                AND o.customer_id      = c.customer_id
            WHERE   o.business_unit_id = %s
            ORDER   BY o.order_id DESC
            """,
            (g.business_unit_id,)
        )
        orders = cursor.fetchall()
        cursor.close()
        return jsonify({'success': True, 'orders': orders})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load orders'}), 500
    finally:
        return_connection(conn)


def get_order_detail(order_id):
    """
    Returns a single order with its full item list.
    Each item includes the product name and unit price at time of order.

    JOIN conditions always include business_unit_id on both sides —
    this guarantees we never accidentally join across businesses.
    """
    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Get order header
        cursor.execute(
            """
            SELECT  o.order_id,
                    o.customer_id,
                    c.name        AS customer_name,
                    c.email       AS customer_email,
                    o.order_date,
                    o.total_amount,
                    o.status
            FROM    orders o
            JOIN    customers c
                ON  o.business_unit_id = c.business_unit_id
                AND o.customer_id      = c.customer_id
            WHERE   o.business_unit_id = %s
            AND     o.order_id         = %s
            """,
            (g.business_unit_id, order_id)
        )
        order = cursor.fetchone()
        if not order:
            cursor.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        # Get order items with product name
        cursor.execute(
            """
            SELECT  oi.product_id,
                    p.name       AS product_name,
                    oi.quantity,
                    oi.unit_price,
                    (oi.quantity * oi.unit_price) AS line_total
            FROM    order_items oi
            JOIN    products p
                ON  oi.business_unit_id = p.business_unit_id
                AND oi.product_id       = p.product_id
            WHERE   oi.business_unit_id = %s
            AND     oi.order_id         = %s
            """,
            (g.business_unit_id, order_id)
        )
        items = cursor.fetchall()
        cursor.close()

        order['items'] = items
        return jsonify({'success': True, 'order': order})

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load order'}), 500
    finally:
        return_connection(conn)


def create_order():
    """
    Create a new order with one or more items.

    Body: {
        customer_id: int,
        items: [
            { product_id: int, quantity: int },
            ...
        ]
    }

    All steps run in a single transaction — either everything succeeds or
    nothing is written:

      1. Validate customer belongs to this business.
      2. Validate every product belongs to this business and has enough stock.
      3. Get next order_id for this business (MAX + 1 pattern).
      4. Insert orders row.
      5. Insert order_items rows, setting unit_price from the product's
         current price (price at time of order, not a live reference).
      6. Deduct stock_quantity for each product.
      7. Commit.

    total_amount is calculated server-side from actual product prices —
    never trusted from the frontend.
    """
    data = request.json or {}

    # Validate customer_id and items list
    try:
        customer_id = Validator.require_positive_int(data.get('customer_id'), 'customer_id')
        items       = Validator.validate_order_items(data.get('items'))
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # ── Step 1: Validate customer belongs to this business ─────────────────
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

        # ── Step 2: Validate every product and check stock ─────────────────────
        validated_items = []
        for item in items:
            cursor.execute(
                """
                SELECT product_id, name, price, stock_quantity
                FROM   products
                WHERE  business_unit_id = %s AND product_id = %s
                """,
                (g.business_unit_id, item['product_id'])
            )
            product = cursor.fetchone()
            if not product:
                cursor.close()
                return jsonify({
                    'success': False,
                    'error':   f"Product ID {item['product_id']} not found"
                }), 404

            if product['stock_quantity'] < item['quantity']:
                cursor.close()
                return jsonify({
                    'success': False,
                    'error':   f"Not enough stock for '{product['name']}'. "
                               f"Available: {product['stock_quantity']}, requested: {item['quantity']}"
                }), 400

            validated_items.append({
                'product_id':  item['product_id'],
                'quantity':    item['quantity'],
                'unit_price':  float(product['price']),
                'name':        product['name'],
            })

        # ── Step 3: Calculate total_amount server-side ─────────────────────────
        total_amount = sum(i['unit_price'] * i['quantity'] for i in validated_items)

        # ── Step 4: Get next order_id for this business ────────────────────────
        cursor.execute(
            "SELECT COALESCE(MAX(order_id), 0) + 1 AS next_id FROM orders WHERE business_unit_id = %s",
            (g.business_unit_id,)
        )
        order_id = cursor.fetchone()['next_id']

        # ── Step 5: Insert order header ────────────────────────────────────────
        cursor.execute(
            """
            INSERT INTO orders (business_unit_id, order_id, customer_id, total_amount, status)
            VALUES (%s, %s, %s, %s, 'placed')
            """,
            (g.business_unit_id, order_id, customer_id, total_amount)
        )

        # ── Step 6: Insert order items + deduct stock ──────────────────────────
        for item in validated_items:
            cursor.execute(
                """
                INSERT INTO order_items (business_unit_id, order_id, product_id, quantity, unit_price)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (g.business_unit_id, order_id, item['product_id'], item['quantity'], item['unit_price'])
            )
            cursor.execute(
                """
                UPDATE products
                SET    stock_quantity = stock_quantity - %s
                WHERE  business_unit_id = %s AND product_id = %s
                """,
                (item['quantity'], g.business_unit_id, item['product_id'])
            )

        conn.commit()
        cursor.close()

        return jsonify({
            'success':      True,
            'order_id':     order_id,
            'total_amount': total_amount,
        }), 201

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to create order'}), 500
    finally:
        return_connection(conn)


def update_order_status(order_id):
    """
    Update the status of an order.

    Body: { status: 'placed' | 'processing' | 'completed' | 'cancelled' }

    Rules enforced here:
    - Admin can set any valid status.
    - Employee can only move orders forward:
        placed → processing → completed
      Employee cannot cancel orders (that's an admin action).

    WHERE clause includes business_unit_id — cross-business status change impossible.
    """
    data = request.json or {}

    try:
        new_status = Validator.validate_order_status(data.get('status'))
    except ValidationError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    # Employee restriction — cannot cancel
    if g.role == 'employee' and new_status == 'cancelled':
        return jsonify({'success': False, 'error': 'Only admins can cancel orders'}), 403

    conn = get_write_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Confirm order exists in this business and get current status
        cursor.execute(
            """
            SELECT order_id, status FROM orders
            WHERE  business_unit_id = %s AND order_id = %s
            """,
            (g.business_unit_id, order_id)
        )
        order = cursor.fetchone()
        if not order:
            cursor.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        current_status = order['status']

        # Employee can only move forward in sequence
        if g.role == 'employee':
            allowed_transitions = {
                'placed':     'processing',
                'processing': 'completed',
            }
            allowed_next = allowed_transitions.get(current_status)
            if new_status != allowed_next:
                cursor.close()
                return jsonify({
                    'success': False,
                    'error':   f"Cannot move order from '{current_status}' to '{new_status}'. "
                               f"Expected next status: '{allowed_next}'"
                }), 400

        cursor.execute(
            """
            UPDATE orders
            SET    status = %s
            WHERE  business_unit_id = %s AND order_id = %s
            """,
            (new_status, g.business_unit_id, order_id)
        )
        conn.commit()
        cursor.close()

        return jsonify({'success': True, 'status': new_status})

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': 'Failed to update order status'}), 500
    finally:
        return_connection(conn)
