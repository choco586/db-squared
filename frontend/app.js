// ============================================================
// DB² — app.js
// All frontend logic: auth, API calls, DOM rendering
// ============================================================

const API = '/api';   // Nginx proxies /api → Flask:5000

// In-memory session — NOT localStorage.
// Frontend Dockerfile's sed command rewrites "http://localhost:5000/api" → "/api"
// if you open the file directly; the session object lives only while the tab is open.
let session = null;

// Products cache used when building the New Order modal
let _products = [];
let _superadminPw = null;   // cached after health unlock
let _healthInterval = null;


// ════════════════════════════════════════════════════════════
//  UTILITIES
// ════════════════════════════════════════════════════════════

/** Build fetch options including auth headers from session */
function apiOpts(method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (session) {
    headers['X-User-Id']    = session.id;
    headers['X-Business-Id'] = session.business_unit_id;
    headers['X-Role']       = session.role;
  }
  const opts = { method, headers };
  if (body !== null) opts.body = JSON.stringify(body);
  return opts;
}

/** Wrapper around fetch — returns parsed JSON or throws */
async function api(path, method = 'GET', body = null) {
  const res = await fetch(`${API}${path}`, apiOpts(method, body));
  const data = await res.json();
  return { ok: res.ok, status: res.status, data };
}

/** Show a brief toast notification */
function toast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => { el.className = 'toast'; }, 3200);
}

/** Format a date string to locale short format */
function fmtDate(str) {
  if (!str) return '—';
  return new Date(str).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Format a number as currency */
function fmtMoney(n) {
  return '$' + parseFloat(n || 0).toFixed(2);
}

/** Show an inline alert in a container */
function showAlert(id, msg, type = 'error') {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.style.display = 'block';
}

function hideAlert(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

/** Status badge HTML */
function badge(status, type = 'status') {
  const map = {
    placed: 'badge-placed', processing: 'badge-processing',
    completed: 'badge-completed', cancelled: 'badge-cancelled',
    active: 'badge-active', disabled: 'badge-disabled',
    admin: 'badge-admin', employee: 'badge-employee',
  };
  return `<span class="badge ${map[status] || ''}">${status}</span>`;
}


// ════════════════════════════════════════════════════════════
//  PAGE NAVIGATION
// ════════════════════════════════════════════════════════════

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const el = document.getElementById(`page-${name}`);
  if (el) el.classList.add('active');
}

function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

  const panel = document.getElementById(`tab-${name}`);
  if (panel) panel.classList.add('active');

  const navEl = document.querySelector(`[data-tab="${name}"]`);
  if (navEl) navEl.classList.add('active');

  // Load data for the tab that just became visible
  switch (name) {
    case 'customers':  loadCustomers(); break;
    case 'products':   loadProducts();  break;
    case 'orders':     loadOrders();    break;
    case 'employees':  loadEmployees(); break;
    case 'logs':       loadLogs();      break;
    case 'health':     /* unlocked separately */ break;
  }
}


// ════════════════════════════════════════════════════════════
//  MODALS
// ════════════════════════════════════════════════════════════

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

function closeModalOutside(event, id) {
  if (event.target.id === id) closeModal(id);
}


// ════════════════════════════════════════════════════════════
//  AUTH
// ════════════════════════════════════════════════════════════

async function handleLogin() {
  hideAlert('login-error');
  const business_name = document.getElementById('login-business').value.trim();
  const username      = document.getElementById('login-username').value.trim();
  const password      = document.getElementById('login-password').value;

  if (!business_name || !username || !password) {
    showAlert('login-error', 'Please fill in all fields');
    return;
  }

  const { ok, data } = await api('/auth/login', 'POST', { business_name, username, password });

  if (ok && data.success) {
    session = data.user;
    initDashboard();
    showPage('dashboard');
    switchTab('customers');
  } else {
    showAlert('login-error', data.error || 'Login failed');
  }
}

async function handleRegister() {
  hideAlert('register-error');
  hideAlert('register-success');

  const business_name = document.getElementById('reg-business').value.trim();
  const username      = document.getElementById('reg-username').value.trim();
  const password      = document.getElementById('reg-password').value;

  if (!business_name || !username || !password) {
    showAlert('register-error', 'Please fill in all fields');
    return;
  }

  const { ok, data } = await api('/auth/register', 'POST', { business_name, username, password });

  if (ok && data.success) {
    showAlert('register-success', data.message || 'Business registered! You can now log in.', 'success');
    document.getElementById('reg-business').value = '';
    document.getElementById('reg-username').value = '';
    document.getElementById('reg-password').value = '';
  } else {
    showAlert('register-error', data.error || 'Registration failed');
  }
}

function logout() {
  session = null;
  _superadminPw = null;
  clearInterval(_healthInterval);
  _healthInterval = null;
  showPage('landing');
}


// ════════════════════════════════════════════════════════════
//  DASHBOARD INIT
// ════════════════════════════════════════════════════════════

function initDashboard() {
  document.getElementById('sidebar-business-name').textContent = session.business_name;
  document.getElementById('sidebar-username').textContent      = session.username;
  document.getElementById('sidebar-role').textContent          = session.role;

  // Show admin-only nav items
  document.querySelectorAll('.admin-only').forEach(el => {
    el.style.display = session.role === 'admin' ? 'flex' : 'none';
  });

  // Logs subtitle
  document.getElementById('logs-subtitle').textContent =
    session.role === 'admin' ? 'All business transactions' : 'Your order history';

  // Admin logs show "Placed By" column
  const logHead = document.getElementById('log-placedby-head');
  if (logHead) logHead.textContent = session.role === 'admin' ? 'Placed By' : '';
}


// ════════════════════════════════════════════════════════════
//  CUSTOMERS
// ════════════════════════════════════════════════════════════

let _editCustomerId = null;

async function loadCustomers() {
  const tbody = document.getElementById('customers-body');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Loading…</td></tr>`;
  hideAlert('customers-alert');

  const { ok, data } = await api('/customers');
  if (!ok) { showAlert('customers-alert', data.error || 'Failed to load customers'); return; }

  if (!data.customers.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-row">No customers yet — add your first one</td></tr>`;
    return;
  }

  tbody.innerHTML = data.customers.map(c => `
    <tr>
      <td><span style="font-family:var(--font-mono);color:var(--text-muted)">${c.customer_id}</span></td>
      <td><strong>${esc(c.name)}</strong></td>
      <td>${esc(c.email)}</td>
      <td>${esc(c.phone || '—')}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td>
        <div class="row-actions">
          <button class="btn-edit"   onclick="editCustomer(${c.customer_id},'${esc(c.name)}','${esc(c.email)}','${esc(c.phone||'')}')">Edit</button>
          ${session.role === 'admin' ? `<button class="btn-delete" onclick="deleteCustomer(${c.customer_id})">Delete</button>` : ''}
        </div>
      </td>
    </tr>
  `).join('');
}

function openCustomerModal(edit = false) {
  document.getElementById('customer-modal-title').textContent = edit ? 'Edit Customer' : 'Add Customer';
  hideAlert('customer-modal-error');
  openModal('customer-modal');
}

function editCustomer(id, name, email, phone) {
  _editCustomerId = id;
  document.getElementById('c-name').value  = name;
  document.getElementById('c-email').value = email;
  document.getElementById('c-phone').value = phone;
  openCustomerModal(true);
}

// Override the Add Customer button to also clear the form
document.addEventListener('DOMContentLoaded', () => {
  const addBtn = document.querySelector('[onclick="openModal(\'customer-modal\')"]');
  if (addBtn) addBtn.onclick = () => {
    _editCustomerId = null;
    document.getElementById('c-name').value  = '';
    document.getElementById('c-email').value = '';
    document.getElementById('c-phone').value = '';
    openCustomerModal(false);
  };
});

async function saveCustomer() {
  hideAlert('customer-modal-error');
  const name  = document.getElementById('c-name').value.trim();
  const email = document.getElementById('c-email').value.trim();
  const phone = document.getElementById('c-phone').value.trim();

  if (!name || !email) { showAlert('customer-modal-error', 'Name and email are required'); return; }

  const body = { name, email, phone };
  const { ok, data } = _editCustomerId
    ? await api(`/customers/${_editCustomerId}`, 'PUT', body)
    : await api('/customers', 'POST', body);

  if (ok && data.success) {
    closeModal('customer-modal');
    toast(_editCustomerId ? 'Customer updated' : 'Customer added');
    _editCustomerId = null;
    loadCustomers();
  } else {
    showAlert('customer-modal-error', data.error || 'Failed to save customer');
  }
}

async function deleteCustomer(id) {
  if (!confirm('Delete this customer? This cannot be undone.')) return;
  const { ok, data } = await api(`/customers/${id}`, 'DELETE');
  if (ok && data.success) { toast('Customer deleted'); loadCustomers(); }
  else showAlert('customers-alert', data.error || 'Failed to delete');
}


// ════════════════════════════════════════════════════════════
//  PRODUCTS
// ════════════════════════════════════════════════════════════

let _editProductId = null;

async function loadProducts() {
  const tbody = document.getElementById('products-body');
  tbody.innerHTML = `<tr><td colspan="7" class="loading-row">Loading…</td></tr>`;
  hideAlert('products-alert');

  const { ok, data } = await api('/products');
  if (!ok) { showAlert('products-alert', data.error || 'Failed to load products'); return; }

  _products = data.products;   // cache for order modal

  if (!data.products.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="loading-row">No products yet</td></tr>`;
    return;
  }

  tbody.innerHTML = data.products.map(p => `
    <tr>
      <td><span style="font-family:var(--font-mono);color:var(--text-muted)">${p.product_id}</span></td>
      <td><strong>${esc(p.name)}</strong></td>
      <td>${esc(p.supplier)}</td>
      <td style="font-family:var(--font-mono)">${fmtMoney(p.price)}</td>
      <td>${p.stock_quantity}</td>
      <td>${p.expiry_date ? fmtDate(p.expiry_date) : '—'}</td>
      <td>
        <div class="row-actions">
          <button class="btn-edit" onclick="editProduct(${p.product_id},'${esc(p.name)}','${esc(p.supplier)}',${p.price},${p.stock_quantity},'${p.expiry_date||''}')">Edit</button>
          ${session.role === 'admin' ? `<button class="btn-delete" onclick="deleteProduct(${p.product_id})">Delete</button>` : ''}
        </div>
      </td>
    </tr>
  `).join('');
}

function editProduct(id, name, supplier, price, stock, expiry) {
  _editProductId = id;
  document.getElementById('p-name').value     = name;
  document.getElementById('p-supplier').value = supplier;
  document.getElementById('p-price').value    = price;
  document.getElementById('p-stock').value    = stock;
  document.getElementById('p-expiry').value   = expiry;
  document.getElementById('product-modal-title').textContent = 'Edit Product';
  hideAlert('product-modal-error');
  openModal('product-modal');
}

// Override Add Product button to clear form
document.addEventListener('DOMContentLoaded', () => {
  const addBtn = document.querySelector('[onclick="openModal(\'product-modal\')"]');
  if (addBtn) addBtn.onclick = () => {
    _editProductId = null;
    ['p-name','p-supplier','p-price','p-stock','p-expiry'].forEach(id => {
      document.getElementById(id).value = '';
    });
    document.getElementById('product-modal-title').textContent = 'Add Product';
    hideAlert('product-modal-error');
    openModal('product-modal');
  };
});

async function saveProduct() {
  hideAlert('product-modal-error');
  const name     = document.getElementById('p-name').value.trim();
  const supplier = document.getElementById('p-supplier').value.trim();
  const price    = document.getElementById('p-price').value;
  const stock    = document.getElementById('p-stock').value;
  const expiry   = document.getElementById('p-expiry').value;

  if (!name || !supplier || !price || stock === '') {
    showAlert('product-modal-error', 'Name, supplier, price, and stock are required'); return;
  }

  const body = { name, supplier, price: parseFloat(price), stock_quantity: parseInt(stock), expiry_date: expiry || null };
  const { ok, data } = _editProductId
    ? await api(`/products/${_editProductId}`, 'PUT', body)
    : await api('/products', 'POST', body);

  if (ok && data.success) {
    closeModal('product-modal');
    toast(_editProductId ? 'Product updated' : 'Product added');
    _editProductId = null;
    loadProducts();
  } else {
    showAlert('product-modal-error', data.error || 'Failed to save product');
  }
}

async function deleteProduct(id) {
  if (!confirm('Delete this product?')) return;
  const { ok, data } = await api(`/products/${id}`, 'DELETE');
  if (ok && data.success) { toast('Product deleted'); loadProducts(); }
  else showAlert('products-alert', data.error || 'Failed to delete');
}


// ════════════════════════════════════════════════════════════
//  ORDERS
// ════════════════════════════════════════════════════════════

async function loadOrders() {
  const tbody = document.getElementById('orders-body');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Loading…</td></tr>`;
  hideAlert('orders-alert');

  const { ok, data } = await api('/orders');
  if (!ok) { showAlert('orders-alert', data.error || 'Failed to load orders'); return; }

  if (!data.orders.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-row">No orders yet</td></tr>`;
    return;
  }

  tbody.innerHTML = data.orders.map(o => `
    <tr>
      <td><span style="font-family:var(--font-mono);color:var(--text-muted)">${o.order_id}</span></td>
      <td>${esc(o.customer_name)}</td>
      <td>${fmtDate(o.order_date)}</td>
      <td style="font-family:var(--font-mono)">${fmtMoney(o.total_amount)}</td>
      <td>${badge(o.status)}</td>
      <td>
        <div class="row-actions">
          <button class="btn-view" onclick="viewOrder(${o.order_id})">View</button>
          ${buildStatusSelect(o)}
        </div>
      </td>
    </tr>
  `).join('');
}

function buildStatusSelect(order) {
  const statuses = ['placed', 'processing', 'completed', 'cancelled'];
  // Employees can't cancel
  const available = session.role === 'admin'
    ? statuses
    : ['placed', 'processing', 'completed'];

  const opts = available.map(s =>
    `<option value="${s}" ${s === order.status ? 'selected' : ''}>${s}</option>`
  ).join('');

  return `<select class="status-select" onchange="updateOrderStatus(${order.order_id}, this.value)">${opts}</select>`;
}

async function updateOrderStatus(orderId, newStatus) {
  const { ok, data } = await api(`/orders/${orderId}/status`, 'PUT', { status: newStatus });
  if (ok && data.success) { toast(`Order #${orderId} → ${newStatus}`); loadOrders(); }
  else { toast(data.error || 'Failed to update status', 'error'); loadOrders(); }
}

async function viewOrder(orderId) {
  const body = document.getElementById('order-detail-body');
  body.innerHTML = 'Loading…';
  document.getElementById('order-detail-title').textContent = `Order #${orderId}`;
  openModal('order-detail-modal');

  const { ok, data } = await api(`/orders/${orderId}`);
  if (!ok) { body.innerHTML = `<p style="color:var(--red)">${data.error}</p>`; return; }

  const o = data.order;
  const itemRows = (o.items || []).map(i => `
    <div class="order-detail-row">
      <span>${esc(i.product_name)}</span>
      <span>${i.quantity} × ${fmtMoney(i.unit_price)} = <strong>${fmtMoney(i.line_total)}</strong></span>
    </div>
  `).join('');

  body.innerHTML = `
    <div class="order-detail-section">
      <h4>Order Info</h4>
      <div class="order-detail-row"><span>Customer</span><span>${esc(o.customer_name)}</span></div>
      <div class="order-detail-row"><span>Date</span><span>${fmtDate(o.order_date)}</span></div>
      <div class="order-detail-row"><span>Status</span><span>${badge(o.status)}</span></div>
    </div>
    <div class="order-detail-section">
      <h4>Items</h4>
      ${itemRows}
      <div class="order-detail-row" style="padding-top:.5rem;margin-top:.25rem;border-top:2px solid var(--border)">
        <span><strong>Total</strong></span>
        <span class="order-detail-total">${fmtMoney(o.total_amount)}</span>
      </div>
    </div>
  `;
}

// ── New Order Modal ─────────────────────────────────────────

async function openOrderModal() {
  document.getElementById('order-modal-error') && hideAlert('order-modal-error');
  document.getElementById('order-items-list').innerHTML = '';
  document.getElementById('order-total-preview').textContent = '$0.00';

  // Populate customer dropdown
  const custSel = document.getElementById('o-customer');
  custSel.innerHTML = '<option value="">Loading customers…</option>';

  const { ok, data } = await api('/customers');
  if (ok) {
    custSel.innerHTML = '<option value="">Select customer…</option>' +
      data.customers.map(c => `<option value="${c.customer_id}">${esc(c.name)}</option>`).join('');
  }

  // Ensure products cache is fresh
  if (!_products.length) {
    const pr = await api('/products');
    if (pr.ok) _products = pr.data.products;
  }

  openModal('order-modal');
  addOrderItemRow();
}

// Override the New Order button
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.querySelector('[onclick="openModal(\'order-modal\')"]');
  if (btn) btn.onclick = openOrderModal;
});

function productOptions() {
  return _products.map(p =>
    `<option value="${p.product_id}" data-price="${p.price}">${esc(p.name)} — ${fmtMoney(p.price)} (stock: ${p.stock_quantity})</option>`
  ).join('');
}

function addOrderItemRow() {
  const list = document.getElementById('order-items-list');
  const row  = document.createElement('div');
  row.className = 'order-item-row';
  row.innerHTML = `
    <select onchange="recalcOrderTotal()">
      <option value="">Select product…</option>
      ${productOptions()}
    </select>
    <input type="number" value="1" min="1" placeholder="Qty" oninput="recalcOrderTotal()">
    <button class="remove-item-btn" onclick="this.parentElement.remove(); recalcOrderTotal()">✕</button>
  `;
  list.appendChild(row);
}

function recalcOrderTotal() {
  const rows  = document.querySelectorAll('#order-items-list .order-item-row');
  let total   = 0;
  rows.forEach(row => {
    const sel   = row.querySelector('select');
    const qty   = parseFloat(row.querySelector('input').value) || 0;
    const opt   = sel.options[sel.selectedIndex];
    const price = opt ? parseFloat(opt.dataset.price || 0) : 0;
    total      += price * qty;
  });
  document.getElementById('order-total-preview').textContent = fmtMoney(total);
}

async function submitOrder() {
  hideAlert('order-modal-error');
  const customer_id = parseInt(document.getElementById('o-customer').value);
  if (!customer_id) { showAlert('order-modal-error', 'Please select a customer'); return; }

  const rows = document.querySelectorAll('#order-items-list .order-item-row');
  const items = [];
  let valid = true;

  rows.forEach((row, i) => {
    const sel        = row.querySelector('select');
    const product_id = parseInt(sel.value);
    const quantity   = parseInt(row.querySelector('input').value);

    if (!product_id || !quantity || quantity < 1) {
      showAlert('order-modal-error', `Item ${i + 1}: select a product and enter a valid quantity`);
      valid = false;
      return;
    }
    items.push({ product_id, quantity });
  });

  if (!valid || !items.length) {
    if (!items.length) showAlert('order-modal-error', 'Add at least one item');
    return;
  }

  const { ok, data } = await api('/orders', 'POST', { customer_id, items });
  if (ok && data.success) {
    closeModal('order-modal');
    toast(`Order #${data.order_id} placed — ${fmtMoney(data.total_amount)}`);
    loadOrders();
    loadProducts();  // refresh stock
  } else {
    showAlert('order-modal-error', data.error || 'Failed to place order');
  }
}


// ════════════════════════════════════════════════════════════
//  EMPLOYEES (admin only)
// ════════════════════════════════════════════════════════════

async function loadEmployees() {
  const tbody = document.getElementById('employees-body');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Loading…</td></tr>`;
  hideAlert('employees-alert');

  const { ok, data } = await api('/users');
  if (!ok) { showAlert('employees-alert', data.error || 'Failed to load employees'); return; }

  if (!data.users.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-row">No team members found</td></tr>`;
    return;
  }

  tbody.innerHTML = data.users.map(u => `
    <tr>
      <td><span style="font-family:var(--font-mono);color:var(--text-muted)">${u.id}</span></td>
      <td>${esc(u.username)} ${u.id === session.id ? '<span style="font-size:.7rem;color:var(--accent)">(you)</span>' : ''}</td>
      <td>${badge(u.role)}</td>
      <td>${badge(u.active ? 'active' : 'disabled')}</td>
      <td>${fmtDate(u.created_at)}</td>
      <td>
        <div class="row-actions">
          ${u.id !== session.id ? `
            <button class="${u.active ? 'btn-toggle' : 'btn-enable'}" onclick="toggleEmployee(${u.id},${!u.active})">
              ${u.active ? 'Disable' : 'Enable'}
            </button>
            <button class="btn-delete" onclick="deleteEmployee(${u.id})">Delete</button>
          ` : ''}
        </div>
      </td>
    </tr>
  `).join('');
}

async function saveEmployee() {
  hideAlert('employee-modal-error');
  const username = document.getElementById('e-username').value.trim();
  const password = document.getElementById('e-password').value;
  const role     = document.getElementById('e-role').value;

  if (!username || !password) { showAlert('employee-modal-error', 'Username and password are required'); return; }

  const { ok, data } = await api('/users', 'POST', { username, password, role });
  if (ok && data.success) {
    closeModal('employee-modal');
    toast('Employee account created');
    document.getElementById('e-username').value = '';
    document.getElementById('e-password').value = '';
    loadEmployees();
  } else {
    showAlert('employee-modal-error', data.error || 'Failed to create employee');
  }
}

// Override Add Employee button to clear form
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.querySelector('[onclick="openModal(\'employee-modal\')"]');
  if (btn) btn.onclick = () => {
    document.getElementById('e-username').value = '';
    document.getElementById('e-password').value = '';
    document.getElementById('e-role').value     = 'employee';
    hideAlert('employee-modal-error');
    openModal('employee-modal');
  };
});

async function toggleEmployee(id, active) {
  const { ok, data } = await api(`/users/${id}/toggle`, 'PUT', { active });
  if (ok && data.success) { toast(data.message); loadEmployees(); }
  else toast(data.error || 'Failed to update', 'error');
}

async function deleteEmployee(id) {
  if (!confirm('Delete this employee account? This cannot be undone.')) return;
  const { ok, data } = await api(`/users/${id}`, 'DELETE');
  if (ok && data.success) { toast('Employee deleted'); loadEmployees(); }
  else toast(data.error || 'Failed to delete', 'error');
}


// ════════════════════════════════════════════════════════════
//  TRANSACTION LOGS
// ════════════════════════════════════════════════════════════

async function loadLogs() {
  const tbody = document.getElementById('logs-body');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Loading…</td></tr>`;
  hideAlert('logs-alert');

  const status = document.getElementById('log-status-filter').value;
  const qs     = status ? `?status=${status}` : '';

  const { ok, data } = await api(`/logs${qs}`);
  if (!ok) { showAlert('logs-alert', data.error || 'Failed to load logs'); return; }

  if (!data.logs.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-row">No transactions found</td></tr>`;
    return;
  }

  tbody.innerHTML = data.logs.map(l => `
    <tr>
      <td><span style="font-family:var(--font-mono);color:var(--text-muted)">${l.order_id}</span></td>
      <td>${esc(l.customer_name)}</td>
      <td>${fmtDate(l.order_date)}</td>
      <td style="font-family:var(--font-mono)">${fmtMoney(l.total_amount)}</td>
      <td>${badge(l.status)}</td>
      <td>${l.placed_by_username ? esc(l.placed_by_username) : ''}</td>
    </tr>
  `).join('');
}


// ════════════════════════════════════════════════════════════
//  DB HEALTH (superadmin unlock)
// ════════════════════════════════════════════════════════════

function unlockHealth() {
  const pw = document.getElementById('superadmin-pw').value;
  if (!pw) { showAlert('health-lock-error', 'Enter the superadmin password'); return; }
  _superadminPw = pw;
  loadHealth();
}

async function loadHealth() {
  if (!_superadminPw) return;

  const res = await fetch(`${API}/admin/health`, {
    headers: {
      'Authorization': _superadminPw,
      'Content-Type':  'application/json',
    }
  });

  if (res.status === 401) {
    showAlert('health-lock-error', 'Invalid superadmin password');
    _superadminPw = null;
    return;
  }

  const data = await res.json();

  // Show the content, hide the lock
  document.getElementById('health-lock-panel').style.display  = 'none';
  document.getElementById('health-content').style.display     = 'block';

  // Primary
  const primary = data.databases.master;
  setHealthCard('primary', primary.status, primary.hostname, [
    `Port: ${primary.port}`,
    `GTID: ${primary.gtid_ok ? '✓' : '✗'}`,
    `Writable: ${primary.writable ? '✓' : '✗'}`,
  ]);

  // Secondary
  const secondary = data.databases.slave;
  setHealthCard('secondary', secondary.status, secondary.hostname, [
    `Port: ${secondary.port}`,
    `GTID: ${secondary.gtid_ok ? '✓' : '✗'}`,
    `Replicating: ${secondary.replicating ? '✓' : '✗'}`,
    `Lag: ${secondary.lag_seconds != null ? secondary.lag_seconds + 's' : '—'}`,
  ]);

  // Overall
  const overallEl   = document.getElementById('overall-status');
  const overallCard = document.getElementById('health-overall-card');
  overallEl.textContent = data.overall;
  overallCard.style.borderColor =
    data.overall === 'HEALTHY'  ? 'var(--green)' :
    data.overall === 'DEGRADED' ? 'var(--amber)' : 'var(--red)';

  document.getElementById('overall-detail').textContent =
    `Active DB: ${data.current_master} · Timestamp: ${new Date(data.timestamp).toLocaleTimeString()}`;

  document.getElementById('health-meta').textContent =
    `Failover in progress: ${data.failover_in_progress} · Failback in progress: ${data.failback_in_progress}`;

  // Auto-refresh every 10s while on health tab
  if (!_healthInterval) {
    _healthInterval = setInterval(() => {
      const healthTab = document.getElementById('tab-health');
      if (healthTab && healthTab.classList.contains('active')) loadHealth();
    }, 10000);
  }
}

function setHealthCard(key, status, hostname, details) {
  const dot    = document.getElementById(`${key}-dot`);
  const stat   = document.getElementById(`${key}-status`);
  const detail = document.getElementById(`${key}-detail`);

  dot.className = 'health-status-dot ' + status.toLowerCase();
  stat.textContent = status;
  stat.style.color = status === 'UP' ? 'var(--green)' : 'var(--red)';
  detail.innerHTML = `<strong>${hostname || 'unknown'}</strong><br>${details.join(' · ')}`;
}

async function triggerFailover() {
  if (!confirm('Force failover to Docker secondary? Only do this for testing.')) return;
  const res  = await fetch(`${API}/admin/failover`, {
    method: 'POST',
    headers: { 'Authorization': _superadminPw, 'Content-Type': 'application/json' }
  });
  const data = await res.json();
  toast(data.message || 'Failover triggered', res.ok ? 'success' : 'error');
  setTimeout(loadHealth, 1500);
}

async function triggerFailback() {
  if (!confirm('Force failback to RDS primary?')) return;
  const res  = await fetch(`${API}/admin/failback`, {
    method: 'POST',
    headers: { 'Authorization': _superadminPw, 'Content-Type': 'application/json' }
  });
  const data = await res.json();
  toast(data.message || 'Failback triggered', res.ok ? 'success' : 'error');
  setTimeout(loadHealth, 1500);
}


// ════════════════════════════════════════════════════════════
//  XSS PROTECTION
// ════════════════════════════════════════════════════════════

/** Escape HTML special chars before inserting user-generated content into innerHTML */
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}


// ════════════════════════════════════════════════════════════
//  INIT
// ════════════════════════════════════════════════════════════

window.addEventListener('load', () => {
  // No localStorage — always start at landing on fresh load
  showPage('landing');

  // Enter key on login fields
  ['login-business', 'login-username', 'login-password'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });
  });

  ['reg-business', 'reg-username', 'reg-password'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') handleRegister(); });
  });
});