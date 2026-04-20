// GiteaCopilot Frontend JS

// Toggle mobile navbar menu
function toggleMenu() {
    const menu = document.getElementById('navbar-menu');
    if (menu) {
        menu.classList.toggle('open');
    }
}

// Toggle mobile sidebar
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) {
        sidebar.classList.toggle('open');
    }
    if (overlay) {
        overlay.classList.toggle('open');
    }
}

// Close menu/sidebar when clicking outside
document.addEventListener('click', (e) => {
    // Close navbar menu
    const menu = document.getElementById('navbar-menu');
    const toggle = document.querySelector('.navbar-toggle');
    if (menu && menu.classList.contains('open') &&
        !menu.contains(e.target) &&
        toggle && !toggle.contains(e.target)) {
        menu.classList.remove('open');
    }

    // Close sidebar
    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.querySelector('.sidebar-toggle');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar && sidebar.classList.contains('open') &&
        !sidebar.contains(e.target) &&
        sidebarToggle && !sidebarToggle.contains(e.target) &&
        overlay && !overlay.contains(e.target)) {
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
    }
});

function showMessage(type, message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;

    const container = document.querySelector('.main-content');
    if (container) {
        container.insertBefore(alertDiv, container.firstChild);
    }

    setTimeout(() => alertDiv.remove(), 5000);
}

// Copy text to clipboard with fallback for older browsers
function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showMessage('success', '已复制');
        }).catch(() => {
            fallbackCopy(text);
        });
    } else {
        fallbackCopy(text);
    }
}

function fallbackCopy(text) {
    const input = document.createElement('input');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    input.value = text;
    document.body.appendChild(input);
    input.select();
    try {
        document.execCommand('copy');
        showMessage('success', '已复制');
    } catch (e) {
        showMessage('error', '复制失败，请手动复制');
    }
    document.body.removeChild(input);
}

async function apiCall(url, method, data) {
    const options = {
        method: method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);
    return response.json();
}

// Handle OAuth redirect
function startOAuth(instanceId) {
    fetch(`/oauth/${instanceId}/redirect`)
        .then(res => res.json())
        .then(data => {
            if (data.redirect_url) {
                window.location.href = data.redirect_url;
            } else {
                showMessage('error', 'Failed to get OAuth redirect URL');
            }
        })
        .catch(err => showMessage('error', err.message));
}

// Delete instance/account/repo
function deleteItem(url, callback) {
    if (confirm('确定要删除吗？')) {
        fetch(url, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
                showMessage('success', data.message || '已删除');
                if (callback) callback();
            })
            .catch(err => showMessage('error', err.message));
    }
}

// Form submission
function handleFormSubmit(formId, url, successCallback) {
    const form = document.getElementById(formId);
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const formData = new FormData(form);
        const data = {};
        formData.forEach((value, key) => data[key] = value);

        try {
            const result = await apiCall(url, 'POST', data);
            if (successCallback) {
                successCallback(result);
            } else {
                showMessage('success', result.message || '操作成功');
            }
        } catch (err) {
            showMessage('error', err.message);
        }
    });
}