// nginx/html/js/api.js — REST API helpers with trace ID support

const API_BASE = '/api';

function generateTraceId() {
    return crypto.randomUUID?.() ||
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
}

async function apiRequest(path, options = {}) {
    const traceId = generateTraceId();
    const url = `${API_BASE}${path}`;
    const config = {
        headers: {
            'Content-Type': 'application/json',
            'X-Trace-ID': traceId,
        },
        ...options,
    };

    // Don't set Content-Type for FormData (browser sets with boundary)
    if (options.body instanceof FormData) {
        if (config.headers) delete config.headers['Content-Type'];
    }

    const response = await fetch(url, config);
    const body = await response.json().catch(() => null);

    if (!body) throw new Error('请求失败');
    if (body.code === 'AUTH_REQUIRED' || body.code === 'AUTH_TOKEN_EXPIRED') {
        throw new Error('AUTH_REQUIRED');
    }
    if (body.code !== 'SUCCESS') throw new Error(body.message || '请求失败');
    return body.data;
}

// ====== Knowledge Bases ======
async function listKBs() {
    return apiRequest('/kbs/list', { method: 'POST', body: JSON.stringify({}) });
}

async function createKB(name, description = '') {
    return apiRequest('/kbs', {
        method: 'POST',
        body: JSON.stringify({ name, description }),
    });
}

async function deleteKB(kbId) {
    return apiRequest('/kbs/delete', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

// ====== Documents ======
async function listDocuments(kbId) {
    return apiRequest('/kbs/documents/list', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

async function uploadDocument(kbId, file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('kb_id', kbId);
    return apiRequest('/kbs/documents/upload', {
        method: 'POST',
        body: formData,
    });
}

// ====== Toast Notification ======
function showToast(message, type = 'success', duration = 3000) {
    const toast = document.getElementById('toast');
    const text = toast.querySelector('.toast-text');
    const icon = toast.querySelector('.toast-icon');

    // Cancel any pending hide animation
    if (window._hideToastTimer) {
        clearTimeout(window._hideToastTimer);
        window._hideToastTimer = null;
    }
    if (window._toastTimer) clearTimeout(window._toastTimer);

    // Set icon
    const icons = {
        success: '✓',
        error: '✕',
        info: 'ℹ',
        warning: '⚠',
    };
    icon.textContent = icons[type] || 'ℹ';

    // Set style
    const bgClasses = {
        success: 'bg-emerald-600',
        error: 'bg-red-500',
        info: 'bg-blue-500',
        warning: 'bg-amber-500',
    };
    toast.className = `fixed top-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-5 py-3 rounded-xl text-white text-sm shadow-xl max-w-2xl toast-enter ${bgClasses[type] || 'bg-blue-500'}`;
    text.textContent = message;

    // Auto dismiss (skip when duration=0 for persistent toasts)
    if (duration > 0) {
        window._toastTimer = setTimeout(() => {
            hideToast();
        }, duration);
    }
}

function hideToast() {
    const toast = document.getElementById('toast');
    toast.className = toast.className.replace('toast-enter', 'toast-leave');
    if (window._hideToastTimer) clearTimeout(window._hideToastTimer);
    window._hideToastTimer = setTimeout(() => {
        toast.classList.add('hidden');
    }, 300);
}

// ====== Session History ======
async function fetchSessions() {
    return apiRequest('/sessions/list', { method: 'POST', body: JSON.stringify({}) });
}

async function fetchSessionMessages(sessionId) {
    return apiRequest('/sessions/messages', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}

async function deleteSessionAPI(sessionId) {
    return apiRequest('/sessions/delete', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}
