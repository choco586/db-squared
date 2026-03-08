# backend/app.py

from flask import Flask, jsonify, request, g
from flask_cors import CORS
from datetime import datetime
import db

# ── Route imports ──────────────────────────────────────────────────────────────
from routes.auth_routes     import register, login
from routes.user_routes     import get_users, add_user, update_user, delete_user, toggle_user
from routes.customer_routes import get_customers, add_customer, update_customer, delete_customer
from routes.product_routes  import get_products, add_product, update_product, delete_product
from routes.order_routes    import get_orders, get_order_detail, create_order, update_order_status
from routes.log_routes      import get_logs
from routes.backup_routes   import run_backup, list_backups, system_backup

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# Superadmin password — only for /api/admin/* endpoints (you, the system owner).
# This is separate from business admin accounts stored in the database.
SUPERADMIN_PASSWORD = 'superadmin123'


# ── Session helper ─────────────────────────────────────────────────────────────
# We don't use JWT yet. Each request that needs auth must send these headers:
#
#   X-User-Id:           <user id from login response>
#   X-Business-Id:       <business_unit_id from login response>
#   X-Role:              <role from login response>
#
# Routes read from flask.g (set in before_request below).
# business_unit_id is NEVER accepted from the request body — only from headers.
# This prevents one business from crafting requests that touch another's data.

@app.before_request
def load_session():
    """
    Parse auth headers into flask.g so every route can read:
        g.user_id           int | None
        g.business_unit_id  int | None
        g.role              str | None  ('admin' | 'employee')
    """
    try:
        g.user_id          = int(request.headers.get('X-User-Id', 0)) or None
        g.business_unit_id = int(request.headers.get('X-Business-Id', 0)) or None
        g.role             = request.headers.get('X-Role', None)
    except (ValueError, TypeError):
        g.user_id          = None
        g.business_unit_id = None
        g.role             = None


def require_auth():
    """
    Return a 401 response if the request has no valid session headers.
    Usage:  err = require_auth(); if err: return err
    """
    if not g.user_id or not g.business_unit_id or not g.role:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    return None


def require_admin():
    """
    Return a 403 response if the logged-in user is not an admin.
    Usage:  err = require_admin(); if err: return err
    Always call require_auth() first.
    """
    if g.role != 'admin':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    return None


def require_superadmin():
    """
    Return a 401 response if the Authorization header doesn't match
    the hardcoded superadmin password.
    Used only for /api/admin/* endpoints.
    """
    auth = request.headers.get('Authorization', '')
    if auth != SUPERADMIN_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401
    return None


# ==============================================================================
# AUTH ROUTES
# No session required — these are the routes that create the session.
# ==============================================================================

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    """
    Register a new business + first admin user.
    Body: { business_name, username, password }
    - If business_name already exists → error.
    - Creates businesses row + users row with role='admin'.
    """
    return register()


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """
    Login for all users (admin and employee).
    Body: { business_name, username, password }
    Returns: { success, user: { id, username, role, business_unit_id, business_name } }
    """
    return login()


# ==============================================================================
# BUSINESS ROUTES
# ==============================================================================

@app.route('/api/business/info', methods=['GET'])
def api_business_info():
    """
    Return info about the logged-in user's own business.
    No cross-business access possible — business_unit_id comes from session.
    """
    err = require_auth()
    if err:
        return err

    conn = db.get_read_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, created_at FROM businesses WHERE id = %s",
            (g.business_unit_id,)
        )
        business = cursor.fetchone()
        cursor.close()
        if not business:
            return jsonify({'success': False, 'error': 'Business not found'}), 404
        return jsonify({'success': True, 'business': business})
    except Exception as e:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    finally:
        db.return_connection(conn)


# ==============================================================================
# USER / EMPLOYEE ROUTES
# ==============================================================================

@app.route('/api/users', methods=['GET'])
def api_get_users():
    """
    Admin: returns all users in their business.
    Employees cannot list all users.
    """
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    return get_users()


@app.route('/api/users', methods=['POST'])
def api_add_user():
    """
    Admin only: create a new employee account within their business.
    Body: { username, password, role }
    business_unit_id is taken from session — never from body.
    """
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    return add_user()


@app.route('/api/users/<int:user_id>', methods=['PUT'])
def api_update_user(user_id):
    """
    Admin: can update any user in their business.
    Employee: can only update their own credentials.
    Body: { username?, password? }
    """
    err = require_auth()
    if err:
        return err
    # Employees can only edit themselves
    if g.role == 'employee' and g.user_id != user_id:
        return jsonify({'success': False, 'error': 'You can only edit your own account'}), 403
    return update_user(user_id)


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    """Admin only: delete a user from their business."""
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    # Admin cannot delete themselves
    if g.user_id == user_id:
        return jsonify({'success': False, 'error': 'Cannot delete your own account'}), 400
    return delete_user(user_id)


@app.route('/api/users/<int:user_id>/toggle', methods=['PUT'])
def api_toggle_user(user_id):
    """
    Admin only: enable or disable a user account.
    Body: { active: true|false }
    Cannot toggle your own account.
    """
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    if g.user_id == user_id:
        return jsonify({'success': False, 'error': 'Cannot toggle your own account'}), 400
    return toggle_user(user_id)


# ==============================================================================
# CUSTOMER ROUTES
# ==============================================================================

@app.route('/api/customers', methods=['GET'])
def api_get_customers():
    err = require_auth()
    if err:
        return err
    return get_customers()


@app.route('/api/customers', methods=['POST'])
def api_add_customer():
    err = require_auth()
    if err:
        return err
    return add_customer()


@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
def api_update_customer(customer_id):
    err = require_auth()
    if err:
        return err
    return update_customer(customer_id)


@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
def api_delete_customer(customer_id):
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    return delete_customer(customer_id)


# ==============================================================================
# PRODUCT ROUTES
# ==============================================================================

@app.route('/api/products', methods=['GET'])
def api_get_products():
    err = require_auth()
    if err:
        return err
    return get_products()


@app.route('/api/products', methods=['POST'])
def api_add_product():
    err = require_auth()
    if err:
        return err
    return add_product()


@app.route('/api/products/<int:product_id>', methods=['PUT'])
def api_update_product(product_id):
    err = require_auth()
    if err:
        return err
    return update_product(product_id)


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def api_delete_product(product_id):
    err = require_auth()
    if err:
        return err
    err = require_admin()
    if err:
        return err
    return delete_product(product_id)


# ==============================================================================
# ORDER ROUTES
# ==============================================================================

@app.route('/api/orders', methods=['GET'])
def api_get_orders():
    err = require_auth()
    if err:
        return err
    return get_orders()


@app.route('/api/orders', methods=['POST'])
def api_create_order():
    err = require_auth()
    if err:
        return err
    return create_order()


@app.route('/api/orders/<int:order_id>', methods=['GET'])
def api_get_order_detail(order_id):
    err = require_auth()
    if err:
        return err
    return get_order_detail(order_id)


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
def api_update_order_status(order_id):
    """
    Update order status. Admin can change any status.
    Employee can only move orders they created forward
    (placed → processing → completed). Cannot cancel.
    """
    err = require_auth()
    if err:
        return err
    return update_order_status(order_id)


# ==============================================================================
# TRANSACTION LOG ROUTES
# ==============================================================================

@app.route('/api/logs', methods=['GET'])
def api_get_logs():
    """
    Admin: returns all transaction logs for their business.
    Employee: returns only their own transaction logs.
    Filtering is enforced in log_routes.py using g.role and g.user_id.
    """
    err = require_auth()
    if err:
        return err
    return get_logs()


# ==============================================================================
# BACKUP ROUTES
# ==============================================================================

@app.route('/api/backups/run', methods=['POST'])
def api_run_backup():
    """
    Trigger a mysqldump backup for the logged-in user's business.
    Any authenticated user can trigger a backup of their own business.
    """
    err = require_auth()
    if err:
        return err
    return run_backup()


@app.route('/api/backups', methods=['GET'])
def api_list_backups():
    """List available backup files for the logged-in user's business."""
    err = require_auth()
    if err:
        return err
    return list_backups()


@app.route('/api/backups/system', methods=['GET'])
def api_system_backup():
    """
    Superadmin only: list all system-wide backups across all businesses.
    Requires Authorization header with superadmin password.
    """
    err = require_superadmin()
    if err:
        return err
    return system_backup()


# ==============================================================================
# ADMIN — DATABASE HEALTH (superadmin only)
# ==============================================================================

@app.route('/api/admin/health', methods=['GET'])
def api_admin_health():
    """
    Full database health status.
    Reads from db.get_status() — reflects what the GTID manager actually sees.
    """
    err = require_superadmin()
    if err:
        return err

    status = db.get_status()
    primary   = status['primary']
    secondary = status['secondary']

    return jsonify({
        'timestamp':            datetime.now().isoformat(),
        'current_master':       status['current_master'],
        'running_on_rds':       status['running_on_rds'],
        'failover_in_progress': status['failover_in_progress'],
        'failback_in_progress': status['failback_in_progress'],
        'databases': {
            'master': {
                'status':      'UP' if primary['healthy'] else 'DOWN',
                'hostname':    primary['host'],
                'port':        primary['port'],
                'writable':    primary['writable'],
                'gtid_ok':     primary['gtid_ok'],
                'role':        'PRIMARY (RDS)',
            },
            'slave': {
                'status':      'UP' if secondary['healthy'] else 'DOWN',
                'hostname':    secondary['host'],
                'port':        secondary['port'],
                'writable':    secondary['writable'],
                'gtid_ok':     secondary['gtid_ok'],
                'replicating': secondary['replicating'],
                'lag_seconds': secondary['lag'],
                'role':        'SECONDARY (Docker)',
            },
        },
        'overall': (
            'HEALTHY'  if primary['healthy'] and secondary['healthy'] else
            'DEGRADED' if primary['healthy'] or  secondary['healthy'] else
            'DOWN'
        ),
        'stats':          status['stats'],
        'recent_history': status['recent_history'],
    })


@app.route('/api/admin/failover', methods=['POST'])
def api_manual_failover():
    """Superadmin only: force failover to Docker secondary (for testing)."""
    err = require_superadmin()
    if err:
        return err
    return jsonify(db.manual_failover())


@app.route('/api/admin/failback', methods=['POST'])
def api_manual_failback():
    """Superadmin only: force failback to RDS primary (for testing)."""
    err = require_superadmin()
    if err:
        return err
    return jsonify(db.manual_failback())


# ==============================================================================
# ROOT
# ==============================================================================

@app.route('/')
def home():
    return jsonify({'message': 'DB-Squared backend running'})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
