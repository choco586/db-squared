// Backend API URL - PORT 5000
const API_URL = 'http://localhost:5000/api';

let editingUserId = null;
const ADMIN_PASSWORD = "admin123";

// ============ PAGE NAVIGATION ============
function showPage(pageName) {
    // Hide all pages
    document.querySelectorAll('.page').forEach(page => {
        page.style.display = 'none';
    });
    
    // Show selected page
    document.getElementById(`${pageName}-page`).style.display = 'block';
    
    // Show/hide navbar
    const navbar = document.getElementById('navbar');
    if (pageName === 'dashboard') {
        navbar.style.display = 'flex';
        const user = JSON.parse(localStorage.getItem('user') || '{}');
        if (user.name) {
            document.getElementById('welcome-msg').textContent = `Welcome, ${user.name}`;
        }
        loadUsers();
        document.getElementById('adminSection').style.display = 'block';
    } else {
        navbar.style.display = 'none';
        document.getElementById('adminSection').style.display = 'none';
        hideAdminPanel();
    }
    
    // Clear errors
    document.querySelectorAll('.error').forEach(error => {
        error.style.display = 'none';
        error.textContent = '';
    });
}

// ============ AUTH FUNCTIONS ============
async function handleLogin() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const errorElement = document.getElementById('login-error');
    
    if (!username || !password) {
        errorElement.textContent = 'Please fill in all fields';
        errorElement.style.display = 'block';
        return;
    }
    
    try {
        const response = await fetch(`${API_URL}/login`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password})
        });
        
        const data = await response.json();
        
        if (data.success) {
            localStorage.setItem('user', JSON.stringify(data.user));
            showPage('dashboard');
        } else {
            errorElement.textContent = data.error || 'Login failed';
            errorElement.style.display = 'block';
        }
    } catch (error) {
        errorElement.textContent = 'Server error';
        errorElement.style.display = 'block';
    }
}

async function handleSignup() {
    const name = document.getElementById('signup-name').value;
    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-password').value;
    const errorElement = document.getElementById('signup-error');
    
    if (!name || !email || !password) {
        errorElement.textContent = 'Please fill in all fields';
        errorElement.style.display = 'block';
        return;
    }
    
    try {
        const response = await fetch(`${API_URL}/signup`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name, email, password})
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('Account created! Please login.');
            showPage('login');
        } else {
            errorElement.textContent = data.error || 'Signup failed';
            errorElement.style.display = 'block';
        }
    } catch (error) {
        errorElement.textContent = 'Server error';
        errorElement.style.display = 'block';
    }
}

function logout() {
    localStorage.removeItem('user');
    showPage('home');
}

// ============ USER CRUD FUNCTIONS ============
async function loadUsers() {
    const usersList = document.getElementById('users-list');
    
    try {
        const response = await fetch(`${API_URL}/users`);
        const users = await response.json();
        
        if (users.length === 0) {
            usersList.innerHTML = '<p>No users found</p>';
            return;
        }
        
        usersList.innerHTML = users.map(user => `
            <div class="user-item">
                <div>
                    <strong>${user.name}</strong>
                    <br>
                    <small>${user.email}</small>
                </div>
                <div>
                    <button onclick="editUser(${user.id}, '${user.name.replace(/'/g, "\\'")}', '${user.email}')">Edit</button>
                    <button onclick="deleteUser(${user.id})">Delete</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        usersList.innerHTML = '<p class="error">Failed to load users</p>';
    }
}

function showAddForm() {
    editingUserId = null;
    document.getElementById('form-title').textContent = 'Add User';
    document.getElementById('user-name').value = '';
    document.getElementById('user-email').value = '';
    document.getElementById('user-form').style.display = 'block';
}

function hideForm() {
    editingUserId = null;
    document.getElementById('user-form').style.display = 'none';
}

function editUser(id, name, email) {
    editingUserId = id;
    document.getElementById('form-title').textContent = 'Edit User';
    document.getElementById('user-name').value = name;
    document.getElementById('user-email').value = email;
    document.getElementById('user-form').style.display = 'block';
}

async function saveUser() {
    const name = document.getElementById('user-name').value;
    const email = document.getElementById('user-email').value;
    const errorElement = document.getElementById('dashboard-error');
    
    if (!name || !email) {
        errorElement.textContent = 'Please fill in all fields';
        errorElement.style.display = 'block';
        return;
    }
    
    try {
        const url = editingUserId 
            ? `${API_URL}/users/${editingUserId}`
            : `${API_URL}/users`;
        
        const method = editingUserId ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name, email})
        });
        
        const data = await response.json();
        
        if (data.success) {
            hideForm();
            loadUsers();
            errorElement.style.display = 'none';
        } else {
            errorElement.textContent = data.error || 'Failed to save';
            errorElement.style.display = 'block';
        }
    } catch (error) {
        errorElement.textContent = 'Server error';
        errorElement.style.display = 'block';
    }
}

async function deleteUser(id) {
    if (!confirm('Delete this user?')) return;
    
    const errorElement = document.getElementById('dashboard-error');
    
    try {
        const response = await fetch(`${API_URL}/users/${id}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            loadUsers();
            errorElement.style.display = 'none';
        } else {
            errorElement.textContent = data.error || 'Failed to delete';
            errorElement.style.display = 'block';
        }
    } catch (error) {
        errorElement.textContent = 'Server error';
        errorElement.style.display = 'block';
    }
}

// ============ ADMIN FUNCTIONS ============
function showAdminPanel() {
    const password = document.getElementById('adminPassword').value;
    if (password === ADMIN_PASSWORD) {
        document.getElementById('dashboard').style.display = 'block';
        document.getElementById('adminPassword').value = '';
        checkDatabaseHealth();
        getDatabaseStats();
        startAutoRefresh();
    } else {
        alert('Invalid admin password!');
    }
}

function hideAdminPanel() {
    document.getElementById('dashboard').style.display = 'none';
    clearInterval(window.autoRefreshInterval);
}

async function checkDatabaseHealth() {
    try {
        const response = await fetch(`${API_URL}/admin/health`, {
            headers: {
                'Authorization': ADMIN_PASSWORD
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            updateHealthDisplay(data);
        } else {
            throw new Error('Failed to fetch health data');
        }
    } catch (error) {
        console.error('Health check failed:', error);
        document.getElementById('masterStatus').textContent = 'Master: Error checking';
        document.getElementById('slaveStatus').textContent = 'Slave: Error checking';
        document.getElementById('overallStatus').textContent = 'Overall: Error checking';
        
        document.getElementById('masterStatus').className = '';
        document.getElementById('slaveStatus').className = '';
        document.getElementById('overallStatus').className = '';
    }
}

function updateHealthDisplay(healthData) {
    const master = healthData.databases.master;
    const slave = healthData.databases.slave;
    
    // Update master status
    const masterElement = document.getElementById('masterStatus');
    masterElement.textContent = `Master: ${master.status} (${master.hostname || 'N/A'}:${master.port})`;
    masterElement.className = master.status === 'UP' ? 'status-up' : 'status-down';
    
    // Update slave status
    const slaveElement = document.getElementById('slaveStatus');
    slaveElement.textContent = `Slave: ${slave.status} (${slave.hostname || 'N/A'}:${slave.port})`;
    slaveElement.className = slave.status === 'UP' ? 'status-up' : 'status-down';
    
    // Update overall status
    const overallElement = document.getElementById('overallStatus');
    overallElement.textContent = `Overall: ${healthData.overall}`;
    overallElement.className = 
        healthData.overall === 'HEALTHY' ? 'status-up' :
        healthData.overall === 'DEGRADED' ? 'status-degraded' : 'status-down';
}

async function getDatabaseStats() {
    try {
        const response = await fetch(`${API_URL}/users`);
        const users = await response.json();
        document.getElementById('userCount').textContent = `Total Users: ${users.length}`;
    } catch (error) {
        console.error('Failed to get stats:', error);
        document.getElementById('userCount').textContent = 'Users: Error loading';
    }
}

function startAutoRefresh() {
    clearInterval(window.autoRefreshInterval);
    
    window.autoRefreshInterval = setInterval(() => {
        if (document.getElementById('dashboard').style.display === 'block') {
            checkDatabaseHealth();
            getDatabaseStats();
        }
    }, 10000);
}

// ============ INITIALIZE ON LOAD ============
window.onload = function() {
    const user = localStorage.getItem('user');
    if (user) {
        showPage('dashboard');
    } else {
        showPage('home');
    }
};