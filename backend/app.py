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
from routes.backup_routes   import download_report, list_backups, start_backup_loop

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# Superadmin password — only for /api/admin/* endpoints (you, the system owner).
# Separate from business admin accounts stored in the database.
SUPERADMIN_PASSWORD = 'superadmin123'


# ── Session helper ─────────────────────────────────────────────────────────────
@app.before_request
def load_session():
    try:
        g.user_id          = int(request.headers.get('X-User-Id', 0)) or None
        g.business_unit_id = int(request.headers.get('X-Business-Id', 0)) or None
        g.role             = request.headers.get('X-Role', None)
    except (ValueError, TypeError):
        g.user_id          = None
        g.business_unit_id = None
        g.role             = None


def require_auth():
    if not g.user_id or not g.business_unit_id or not g.role:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    return None


def require_admin():
    if g.role != 'admin':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    return None


def require_superadmin():
    auth = request.headers.get('Authorization', '')
    if auth != SUPERADMIN_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401
    return None


# ==============================================================================
# AUTH ROUTES
# ==============================================================================

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    return register()


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    return login()


# ==============================================================================
# BUSINESS ROUTES
# ==============================================================================

@app.route('/api/business/info', methods=['GET'])
def api_business_info():
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
    except Exception:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    finally:
        db.return_connection(conn)


# ==============================================================================
# USER / EMPLOYEE ROUTES
# ==============================================================================

@app.route('/api/users', methods=['GET'])
def api_get_users():
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    return get_users()


@app.route('/api/users', methods=['POST'])
def api_add_user():
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    return add_user()


@app.route('/api/users/<int:user_id>', methods=['PUT'])
def api_update_user(user_id):
    err = require_auth()
    if err: return err
    if g.role == 'employee' and g.user_id != user_id:
        return jsonify({'success': False, 'error': 'You can only edit your own account'}), 403
    return update_user(user_id)


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    if g.user_id == user_id:
        return jsonify({'success': False, 'error': 'Cannot delete your own account'}), 400
    return delete_user(user_id)


@app.route('/api/users/<int:user_id>/toggle', methods=['PUT'])
def api_toggle_user(user_id):
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    if g.user_id == user_id:
        return jsonify({'success': False, 'error': 'Cannot toggle your own account'}), 400
    return toggle_user(user_id)


# ==============================================================================
# CUSTOMER ROUTES
# ==============================================================================

@app.route('/api/customers', methods=['GET'])
def api_get_customers():
    err = require_auth()
    if err: return err
    return get_customers()


@app.route('/api/customers', methods=['POST'])
def api_add_customer():
    err = require_auth()
    if err: return err
    return add_customer()


@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
def api_update_customer(customer_id):
    err = require_auth()
    if err: return err
    return update_customer(customer_id)


@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
def api_delete_customer(customer_id):
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    return delete_customer(customer_id)


# ==============================================================================
# PRODUCT ROUTES
# ==============================================================================

@app.route('/api/products', methods=['GET'])
def api_get_products():
    err = require_auth()
    if err: return err
    return get_products()


@app.route('/api/products', methods=['POST'])
def api_add_product():
    err = require_auth()
    if err: return err
    return add_product()


@app.route('/api/products/<int:product_id>', methods=['PUT'])
def api_update_product(product_id):
    err = require_auth()
    if err: return err
    return update_product(product_id)


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def api_delete_product(product_id):
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    return delete_product(product_id)


# ==============================================================================
# ORDER ROUTES
# ==============================================================================

@app.route('/api/orders', methods=['GET'])
def api_get_orders():
    err = require_auth()
    if err: return err
    return get_orders()


@app.route('/api/orders', methods=['POST'])
def api_create_order():
    err = require_auth()
    if err: return err
    return create_order()


@app.route('/api/orders/<int:order_id>', methods=['GET'])
def api_get_order_detail(order_id):
    err = require_auth()
    if err: return err
    return get_order_detail(order_id)


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
def api_update_order_status(order_id):
    err = require_auth()
    if err: return err
    return update_order_status(order_id)


# ==============================================================================
# TRANSACTION LOG ROUTES
# ==============================================================================

@app.route('/api/logs', methods=['GET'])
def api_get_logs():
    err = require_auth()
    if err: return err
    return get_logs()


# ==============================================================================
# REPORT ROUTES — business admin only, CSV download of their own data
# ==============================================================================

@app.route('/api/reports/<string:report_type>', methods=['GET'])
def api_download_report(report_type):
    err = require_auth()
    if err: return err
    err = require_admin()
    if err: return err
    return download_report(report_type)


# ==============================================================================
# BACKUP STATUS — superadmin only, read-only listing
# No trigger endpoint — backups run automatically in the background
# ==============================================================================

@app.route('/api/backups', methods=['GET'])
def api_list_backups():
    """Superadmin only: list all local backup files and their S3 status."""
    err = require_superadmin()
    if err: return err
    return list_backups()


# ==============================================================================
# ADMIN — DATABASE HEALTH (superadmin only)
# ==============================================================================

@app.route('/api/admin/health', methods=['GET'])
def api_admin_health():
    err = require_superadmin()
    if err: return err

    status  = db.get_status()
    primary = status['primary']
    secondary = status['secondary']

    return jsonify({
        'timestamp':            datetime.now().isoformat(),
        'current_master':       status['current_master'],
        'running_on_rds':       status['running_on_rds'],
        'failover_in_progress': status['failover_in_progress'],
        'failback_in_progress': status['failback_in_progress'],
        'databases': {
            'master': {
                'status':   'UP' if primary['healthy'] else 'DOWN',
                'hostname': primary['host'],
                'port':     primary['port'],
                'writable': primary['writable'],
                'gtid_ok':  primary['gtid_ok'],
                'role':     'PRIMARY (RDS)',
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
    err = require_superadmin()
    if err: return err
    return jsonify(db.manual_failover())


@app.route('/api/admin/failback', methods=['POST'])
def api_manual_failback():
    err = require_superadmin()
    if err: return err
    return jsonify(db.manual_failback())


# ==============================================================================
# ROOT
# ==============================================================================

@app.route('/')
def home():
    return jsonify({'message': 'DB-Squared backend running'})


# ==============================================================================
# STARTUP
# ==============================================================================

if __name__ == '__main__':
    # Start the silent background backup loop before serving requests
    start_backup_loop()
    app.run(debug=True, host='0.0.0.0', port=5000)