# backend/routes/log_routes.py

from flask import jsonify, request, g
from db import get_read_connection, return_connection


def get_logs():
    """
    Return transaction logs for the logged-in user's business.

    Admin  → sees ALL orders (with who created them) for the entire business.
    Employee → sees only orders they are associated with via user_id.

    We use the orders table as the transaction log — every order is a
    transaction. We join users to show which employee placed or last
    updated the order.

    Optional query params:
      ?status=placed|processing|completed|cancelled   filter by status
      ?limit=50                                        max rows (default 100)
    """
    # Optional filters from query string
    status_filter = request.args.get('status')
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
    except (ValueError, TypeError):
        limit = 100

    valid_statuses = {'placed', 'processing', 'completed', 'cancelled'}
    if status_filter and status_filter not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status filter'}), 400

    conn = get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        if g.role == 'admin':
            # Admin: full log for the entire business
            # Join customers for customer name, join users for who placed the order
            base_query = """
                SELECT  o.order_id,
                        o.order_date,
                        o.status,
                        o.total_amount,
                        c.name          AS customer_name,
                        c.customer_id,
                        o.created_by    AS placed_by_user_id,
                        u.username      AS placed_by_username
                FROM    orders o
                JOIN    customers c
                    ON  o.business_unit_id = c.business_unit_id
                    AND o.customer_id      = c.customer_id
                LEFT JOIN users u
                    ON  u.id               = o.created_by
                    AND u.business_unit_id = o.business_unit_id
                WHERE   o.business_unit_id = %s
            """
            params = [g.business_unit_id]

            if status_filter:
                base_query += " AND o.status = %s"
                params.append(status_filter)

            base_query += " ORDER BY o.order_date DESC LIMIT %s"
            params.append(limit)

            cursor.execute(base_query, tuple(params))

        else:
            # Employee: only orders they placed (created_by = their user_id)
            base_query = """
                SELECT  o.order_id,
                        o.order_date,
                        o.status,
                        o.total_amount,
                        c.name  AS customer_name,
                        c.customer_id
                FROM    orders o
                JOIN    customers c
                    ON  o.business_unit_id = c.business_unit_id
                    AND o.customer_id      = c.customer_id
                WHERE   o.business_unit_id = %s
                AND     o.created_by       = %s
            """
            params = [g.business_unit_id, g.user_id]

            if status_filter:
                base_query += " AND o.status = %s"
                params.append(status_filter)

            base_query += " ORDER BY o.order_date DESC LIMIT %s"
            params.append(limit)

            cursor.execute(base_query, tuple(params))

        logs = cursor.fetchall()
        cursor.close()

        return jsonify({
            'success': True,
            'role':    g.role,
            'count':   len(logs),
            'logs':    logs,
        })

    except Exception:
        return jsonify({'success': False, 'error': 'Failed to load logs'}), 500
    finally:
        return_connection(conn)
