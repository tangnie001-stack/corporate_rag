/**
 * api.js — REST API 请求封装
 *
 * 提供统一的 API 请求函数（自动注入 X-Trace-ID 和认证 token），
 * 以及知识库、文档、会话等业务接口的快捷调用。
 */

/** API 基础路径 */
const API_BASE = '/api';

/**
 * 生成全链路追踪 ID（UUID v4 格式）。
 *
 * 优先使用浏览器内置 crypto.randomUUID()，
 * 不支持时 fallback 到纯 JS 实现。
 *
 * @returns {string} 36 位 UUID
 */
function generateTraceId() {
    return crypto.randomUUID?.() ||
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
}

/**
 * 通用 API 请求封装。
 *
 * 自动注入 X-Trace-ID 请求头，解析统一响应信封，
 * 遇认证过期自动抛出 AUTH_REQUIRED 异常。
 *
 * @param {string} path - API 路径（如 /kbs/list）
 * @param {object} [options={}] - fetch 配置项，会与默认 headers 合并
 * @returns {Promise<any>} 响应信封中的 data 字段
 * @throws {Error} 请求失败或业务错误时抛出
 */
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

    // FormData 不使用手动设置的 Content-Type（浏览器自动加 boundary）
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

// ====== 知识库（Knowledge Base）=====

/** 获取当前用户的所有知识库列表。 */
async function listKBs() {
    return apiRequest('/kbs/list', { method: 'POST', body: JSON.stringify({}) });
}

/**
 * 创建新知识库。
 * @param {string} name - 知识库名称
 * @param {string} [description=''] - 可选描述
 */
async function createKB(name, description = '') {
    return apiRequest('/kbs', {
        method: 'POST',
        body: JSON.stringify({ name, description }),
    });
}

/**
 * 删除知识库（级联删除其下所有文档）。
 * @param {string} kbId - 知识库 UUID
 */
async function deleteKB(kbId) {
    return apiRequest('/kbs/delete', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

// ====== 文档（Document） ======

/**
 * 获取知识库下的文档列表。
 * @param {string} kbId - 知识库 UUID
 * @returns {Promise<Array>} 文档数组
 */
async function listDocuments(kbId) {
    return apiRequest('/kbs/documents/list', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

/**
 * 上传文档到指定知识库。
 * @param {string} kbId - 知识库 UUID
 * @param {File} file - 要上传的文件对象
 */
async function uploadDocument(kbId, file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('kb_id', kbId);
    return apiRequest('/kbs/documents/upload', {
        method: 'POST',
        body: formData,
    });
}

// ====== Toast 通知 ======

/**
 * 显示 toast 通知。
 *
 * @param {string} message - 通知文本
 * @param {'success'|'error'|'info'|'warning'} [type='success'] - 通知类型
 * @param {number} [duration=3000] - 显示时长（ms），设为 0 则持续显示
 */
function showToast(message, type = 'success', duration = 3000) {
    const toast = document.getElementById('toast');
    const text = toast.querySelector('.toast-text');
    const icon = toast.querySelector('.toast-icon');

    // 取消上一个正在执行的消失动画
    if (window._hideToastTimer) {
        clearTimeout(window._hideToastTimer);
        window._hideToastTimer = null;
    }
    if (window._toastTimer) clearTimeout(window._toastTimer);

    // 设置图标
    const icons = {
        success: '✓',
        error: '✕',
        info: 'ℹ',
        warning: '⚠',
    };
    icon.textContent = icons[type] || 'ℹ';

    // 设置样式
    const bgClasses = {
        success: 'bg-emerald-600',
        error: 'bg-red-500',
        info: 'bg-blue-500',
        warning: 'bg-amber-500',
    };
    toast.className = `fixed top-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-5 py-3 rounded-xl text-white text-sm shadow-xl max-w-2xl toast-enter ${bgClasses[type] || 'bg-blue-500'}`;
    text.textContent = message;

    // 自动消失（duration=0 时保持显示，用于持久 toast）
    if (duration > 0) {
        window._toastTimer = setTimeout(() => {
            hideToast();
        }, duration);
    }
}

/** 隐藏 toast（带动画效果）。 */
function hideToast() {
    const toast = document.getElementById('toast');
    toast.className = toast.className.replace('toast-enter', 'toast-leave');
    if (window._hideToastTimer) clearTimeout(window._hideToastTimer);
    window._hideToastTimer = setTimeout(() => {
        toast.classList.add('hidden');
    }, 300);
}

// ====== 会话历史（Session） ======

/** 获取当前用户的会话列表。 */
async function fetchSessions() {
    return apiRequest('/sessions/list', { method: 'POST', body: JSON.stringify({}) });
}

/**
 * 获取指定会话的消息列表。
 * @param {string} sessionId - 会话 UUID
 */
async function fetchSessionMessages(sessionId) {
    return apiRequest('/sessions/messages', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}

/**
 * 删除指定会话。
 * @param {string} sessionId - 会话 UUID
 */
async function deleteSessionAPI(sessionId) {
    return apiRequest('/sessions/delete', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}
