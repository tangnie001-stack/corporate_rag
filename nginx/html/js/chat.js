/**
 * chat.js — SSE 流式聊天控制器
 *
 * 管理会话生命周期，处理 SSE（Server-Sent Events）流式响应，
 * 负责 KB 选择器加载、消息渲染、引用展示等功能。
 */

let currentSessionId = generateSessionId();
let abortController = null;
let activeEventSource = null;
let sessions = [];  // 缓存的会话列表，供侧边栏渲染使用

/**
 * 生成本地会话 ID（非持久化标识，在首次请求时由后端注册）。
 * 格式：session_时间戳_随机串
 *
 * @returns {string} 会话 ID
 */
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

/**
 * 加载知识库下拉选择器。
 *
 * 从 API 获取当前用户的所有知识库列表，渲染到 #kb-select 中。
 * 加载失败时显示"加载失败"占位项。
 */
async function loadKbSelector() {
    const select = document.getElementById('kb-select');
    try {
        const kbs = await listKBs();
        select.innerHTML = '<option value="">所有知识库</option>';
        kbs.forEach(kb => {
            const option = document.createElement('option');
            option.value = kb.id;
            option.textContent = kb.name;
            select.appendChild(option);
        });
    } catch (err) {
        console.error('Failed to load KBs:', err);
        select.innerHTML = '<option value="">加载失败</option>';
    }
}

/**
 * 从 API 加载会话列表并渲染侧边栏。
 *
 * 将获取到的会话列表缓存到 sessions 变量中，
 * 然后调用 renderSidebar() 更新侧边栏 UI。
 * 加载失败时显示"重试"按钮。
 */
async function loadSessions() {
    const sidebarList = document.querySelector('.sidebar-session-list');
    if (!sidebarList) return;

    try {
        sessions = await fetchSessions();
        renderSidebar();
    } catch (err) {
        console.error('Failed to load sessions:', err);
        sidebarList.innerHTML = ''
            + '<div class="text-center py-8">'
            + '<div class="text-slate-500 text-xs mb-1">加载失败</div>'
            + '<button onclick="loadSessions()" class="text-xs text-blue-400 hover:text-blue-300">重试</button>'
            + '</div>';
    }
}

/**
 * 渲染侧边栏会话列表。
 *
 * @param {string|null} [activeId=null] - 要高亮显示的会话 ID，
 *   不传时使用 currentSessionId
 */
function renderSidebar(activeId = null) {
    const sidebarList = document.querySelector('.sidebar-session-list');
    if (!sidebarList) return;

    if (!sessions || sessions.length === 0) {
        sidebarList.innerHTML = ''
            + '<div class="text-center py-8 px-3">'
            + '<div class="text-slate-500 text-xs">暂无会话</div>'
            + '<div class="text-slate-600 text-[10px] mt-1">发送消息将自动创建</div>'
            + '</div>';
        return;
    }

    sidebarList.innerHTML = sessions.map(s => {
        const isActive = (activeId || currentSessionId) === s.id;
        const dateLabel = formatSessionDate(s.updated_at);
        return ''
            + '<div class="session-item group relative flex items-start gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors '
            + (isActive ? 'bg-blue-600/10 text-blue-300' : 'hover:bg-slate-800 text-slate-400')
            + '" onclick="switchSession(\'' + s.id + '\')">'
            + '<div class="flex-1 min-w-0">'
            + '<div class="text-sm font-medium truncate ' + (isActive ? 'text-blue-300' : 'text-slate-300') + '">'
            + escapeHtml(s.title || '新会话')
            + '</div>'
            + '<div class="flex items-center gap-2 mt-0.5">'
            + '<span class="text-[10px] text-slate-500">' + dateLabel + '</span>'
            + '<span class="text-[10px] text-slate-600">' + s.message_count + ' 条消息</span>'
            + '</div>'
            + '</div>'
            + '<button onclick="event.stopPropagation(); deleteSession(\'' + s.id + '\')" '
            + 'class="delete-btn flex-shrink-0 w-6 h-6 rounded-md bg-slate-800 hover:bg-red-500/20 text-slate-500 hover:text-red-400 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all" title="删除会话">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>'
            + '</button>'
            + '</div>';
    }).join('');
}

/**
 * 格式化会话日期用于侧边栏展示。
 *
 * 规则：今天显示时间、本周内显示星期、更早显示月/日。
 *
 * @param {string} dateStr - ISO 日期字符串
 * @returns {string} 格式化后的日期文本
 */
function formatSessionDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now - d;
    const oneDay = 86400000;
    if (diff < oneDay && d.getDate() === now.getDate()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    if (diff < 7 * oneDay) {
        const days = ['周日','周一','周二','周三','周四','周五','周六'];
        return days[d.getDay()];
    }
    return (d.getMonth() + 1) + '/' + d.getDate();
}

/**
 * 切换到已存在的会话。
 *
 * 关闭当前 SSE 连接，从 API 加载会话消息并渲染，
 * 同时更新 KB 选择器和侧边栏高亮状态。
 *
 * @param {string} sessionId - 目标会话 ID
 */
async function switchSession(sessionId) {
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }
    if (abortController) {
        abortController = null;
    }

    try {
        const messages = await fetchSessionMessages(sessionId);
        currentSessionId = sessionId;
        renderMessages(messages);

        // 同步 KB 选择器到该会话使用的知识库
        const session = sessions.find(s => s.id === sessionId);
        if (session) {
            document.getElementById('kb-select').value = session.kb_id || '';
        }

        // 高亮当前会话
        renderSidebar(sessionId);
    } catch (err) {
        console.error('Failed to load session messages:', err);
        showToast('加载消息失败', 'error');
    }
}

/**
 * 创建新会话。
 *
 * 关闭当前 SSE 连接、清空聊天区域、生成新的会话 ID，
 * 并重置 KB 选择器。
 */
function newSession() {
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }
    if (abortController) {
        abortController = null;
    }
    currentSessionId = generateSessionId();
    clearChatArea();
    renderSidebar(null);
    document.getElementById('kb-select').value = '';
    document.getElementById('chat-input').focus();
}

/**
 * 删除会话（需用户确认）。
 *
 * 调用 API 删除后，若删除的是当前会话则自动新建会话，
 * 最后刷新侧边栏列表。
 *
 * @param {string} sessionId - 要删除的会话 ID
 */
async function deleteSession(sessionId) {
    if (!confirm('确认删除此会话？')) return;

    try {
        await deleteSessionAPI(sessionId);
        if (sessionId === currentSessionId) {
            newSession();
        }
        await loadSessions();
    } catch (err) {
        console.error('Failed to delete session:', err);
        showToast('删除会话失败', 'error');
    }
}

/**
 * 生成欢迎页面的 HTML。
 *
 * 包含系统欢迎语和三个快捷提问按钮。
 *
 * @returns {string} 欢迎页面 HTML 片段
 */
function getWelcomeHtml() {
    return ''
        + '<div class="flex flex-col items-center justify-center h-full text-slate-400">'
        + '<div class="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center text-3xl mb-4">💬</div>'
        + '<p class="text-sm font-medium text-slate-600 mb-1">开始智能问答</p>'
        + '<p class="text-xs text-slate-400 mb-6">选择知识库，输入您的金融文档相关问题</p>'
        + '<div class="flex flex-wrap gap-2 justify-center max-w-md">'
        + '<button onclick="quickQuestion(\'本季度营收情况如何？\')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">本季度营收情况如何？</button>'
        + '<button onclick="quickQuestion(\'分析一下主要财务指标\')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">分析一下主要财务指标</button>'
        + '<button onclick="quickQuestion(\'净利润同比增长多少？\')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">净利润同比增长多少？</button>'
        + '</div>'
        + '</div>';
}

/**
 * 渲染消息列表到聊天区域。
 *
 * 遍历消息数组，对每条消息根据 role 调用 addMessage() 渲染，
 * AI 回复会提取引用来源并渲染为来源卡片。
 *
 * @param {Array} messages - 消息数组，每项含 role、content、sources 等字段
 */
function renderMessages(messages) {
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (!messages || messages.length === 0) {
        container.innerHTML = getWelcomeHtml();
        return;
    }

    messages.forEach(msg => {
        if (msg.role === 'user') {
            addMessage(msg.content, 'user');
        } else {
            const div = addMessage('', 'assistant');
            const contentDiv = div.querySelector('.message-content');
            const rendered = typeof marked !== 'undefined' ? marked.parse(msg.content) : msg.content;

            let sourcesHtml = '';
            if (msg.sources && msg.sources.length > 0) {
                const srcList = typeof msg.sources === 'string' ? JSON.parse(msg.sources) : msg.sources;
                sourcesHtml = '<div class="mt-3 pt-2 border-t border-slate-200">'
                    + '<div class="text-xs font-semibold text-slate-500 mb-1.5">📚 来源</div>'
                    + srcList.map(s => ''
                        + '<div class="flex items-start gap-2 py-1 px-3 bg-slate-50 rounded-md text-xs text-slate-600 mb-1">'
                        + '<span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">📄</span>'
                        + '<span>' + escapeHtml(s) + '</span>'
                        + '</div>'
                    ).join('')
                    + '</div>';
            }

            contentDiv.innerHTML = rendered + sourcesHtml;
        }
    });

    container.scrollTop = container.scrollHeight;
}

/**
 * 清空聊天区域并显示欢迎界面。
 */
function clearChatArea() {
    const container = document.getElementById('chat-messages');
    container.innerHTML = getWelcomeHtml();
}

/**
 * 发送聊天消息。
 *
 * 获取用户输入和当前选中的知识库，渲染用户消息气泡，
 * 创建占位 AI 回复，通过 SSE 连接接收流式响应。
 *
 * SSE 事件说明：
 * - status：处理阶段状态更新（检索中、思考中等）
 * - token：逐 token 流式输出
 * - citation：引用信息
 * - done：响应结束，渲染引用列表
 * - error：服务端错误，显示友好提示
 */
function sendMessage() {
    const input = document.getElementById('chat-input');
    const query = input.value.trim();
    if (!query) return;

    const kbId = document.getElementById('kb-select').value;
    input.value = '';

    // 添加用户消息气泡
    addMessage(query, 'user');

    // 创建 AI 回复占位气泡（显示"思考中..."状态）
    const assistantDiv = addMessage('', 'assistant');
    const contentDiv = assistantDiv.querySelector('.message-content');
    const statusDiv = document.createElement('div');
    statusDiv.className = 'flex items-center gap-2 text-slate-400 text-sm';
    statusDiv.innerHTML = '<span class="spinner"></span> 思考中...';
    contentDiv.appendChild(statusDiv);

    // 生成 trace_id 用于全链路追踪
    const traceId = crypto.randomUUID?.() ||
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });

    const params = new URLSearchParams({
        session_id: currentSessionId,
        kb_id: kbId,
        query: query,
        trace_id: traceId,
    });

    // 关闭已有 SSE 连接
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }

    abortController = new AbortController();
    const evtSource = new EventSource(`/api/chat/stream?${params}`);
    activeEventSource = evtSource;

    let fullText = '';
    const citations = [];

    // 状态事件：更新处理阶段提示
    evtSource.addEventListener('status', (e) => {
        try {
            const data = JSON.parse(e.data);
            if (statusDiv.parentNode) {
                statusDiv.innerHTML = `<span class="spinner"></span> ${escapeHtml(data.message)}`;
            }
        } catch (err) {
            console.error('Status parse error:', err);
        }
    });

    // token 事件：收到回复文本片段，追加渲染
    evtSource.addEventListener('token', (e) => {
        try {
            const data = JSON.parse(e.data);
            fullText += data.token;
            // 移除状态提示，渲染 Markdown 内容
            if (statusDiv.parentNode) statusDiv.remove();
            contentDiv.innerHTML = typeof marked !== 'undefined'
                ? marked.parse(fullText)
                : fullText;
            // 自动滚动到底部
            const container = document.getElementById('chat-messages');
            container.scrollTop = container.scrollHeight;
        } catch (err) {
            console.error('Token parse error:', err);
        }
    });

    // citation 事件：收集引用信息，响应结束后统一展示
    evtSource.addEventListener('citation', (e) => {
        try {
            const data = JSON.parse(e.data);
            citations.push(data);
        } catch (err) {
            console.error('Citation parse error:', err);
        }
    });

    // done 事件：响应完成，渲染引用列表并刷新侧边栏
    evtSource.addEventListener('done', () => {
        evtSource.close();
        activeEventSource = null;
        abortController = null;

        // 若有引用信息，追加到回复内容末尾
        if (citations.length > 0) {
            const citationsHtml = citations.map((c, i) => {
                const snippet = c.highlighted_snippet
                    ? c.highlighted_snippet
                    : escapeHtml(c.snippet || '');
                const scorePct = c.score ? Math.round(c.score * 100) + '%' : '';
                return ''
                    + '<div class="citation-item flex items-start gap-2 py-2 px-3 bg-slate-50 rounded-md text-xs text-slate-600 mb-1.5">'
                    + '<span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">'
                    + (i + 1)
                    + '</span>'
                    + '<div class="flex-1 min-w-0">'
                    + '<div class="flex items-center gap-2 mb-1">'
                    + '<strong>' + escapeHtml(c.source) + '</strong>'
                    + (c.page ? '<span class="text-slate-400">· 第' + c.page + '页</span>' : '')
                    + (scorePct ? '<span class="citation-score">' + scorePct + '</span>' : '')
                    + '</div>'
                    + '<div class="text-slate-500 leading-relaxed">' + snippet + '</div>'
                    + '</div>'
                    + '</div>';
            }).join('');

            const rendered = typeof marked !== 'undefined' ? marked.parse(fullText) : fullText;
            contentDiv.innerHTML = rendered
                + '<div class="mt-4 pt-3 border-t border-slate-200">'
                + '<div class="text-xs font-semibold text-slate-500 mb-2">📚 来源</div>'
                + citationsHtml
                + '</div>';
        }

        // 滚动到底部
        document.getElementById('chat-messages').scrollTop =
            document.getElementById('chat-messages').scrollHeight;

        loadSessions();  // 刷新侧边栏会话列表
    });

    // error 事件：服务端异常，显示友好错误提示
    evtSource.addEventListener('error', (e) => {
        evtSource.close();
        activeEventSource = null;
        abortController = null;

        // 解析错误信息并映射为用户友好的提示文本
        let displayText = '';
        try {
            const data = JSON.parse(e.data);
            const errorMsg = data.error || '';
            if (errorMsg.includes('检索') || errorMsg.includes('search')) {
                displayText = '未找到相关信息，请尝试换个问法';
            } else if (errorMsg.includes('超时') || errorMsg.includes('timeout')) {
                displayText = '模型响应超时，请稍后重试';
            } else if (errorMsg.includes('知识库') || errorMsg.includes('KB') || errorMsg.includes('exist')) {
                displayText = '知识库不存在，请刷新页面';
            } else {
                displayText = '服务异常，请稍后重试';
            }
        } catch (err) {
            displayText = '服务异常，请稍后重试';
        }

        if (statusDiv.parentNode) {
            statusDiv.remove();
        }
        contentDiv.innerHTML = `<div class="error-banner">${displayText}</div>`;
        console.error('SSE error:', e);
    });
}

/**
 * 向聊天区域添加一条消息气泡。
 *
 * @param {string} text - 消息文本
 * @param {'user'|'assistant'} role - 消息角色
 * @returns {HTMLElement} 消息气泡的 DOM 元素
 */
function addMessage(text, role) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');

    const isUser = role === 'user';
    const alignClass = isUser ? 'items-end' : 'items-start';
    const bubbleClass = isUser
        ? 'message-bubble user bg-blue-600 text-white rounded-2xl rounded-br-sm'
        : 'message-bubble assistant bg-white border border-slate-200 rounded-2xl rounded-bl-sm';
    const maxWidth = isUser ? 'max-w-[75%]' : 'max-w-[85%]';
    const padding = isUser ? 'px-4 py-2.5' : 'px-4 py-3';
    const textClass = isUser ? 'text-white' : 'text-slate-800';

    div.className = `flex flex-col ${alignClass} mb-4 message-bubble`;
    div.innerHTML = `
        <div class="${bubbleClass} ${maxWidth} ${padding} relative shadow-sm">
            <div class="message-content ${textClass} text-sm leading-relaxed markdown-body">
                ${isUser ? '<span>' + escapeHtml(text) + '</span>' : ''}
            </div>
        </div>
    `;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

/**
 * HTML 转义，防止 XSS。
 *
 * @param {string} text - 原始文本
 * @returns {string} 转义后的文本
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/** 重置为新建会话。 */
function resetSession() {
    newSession();
}

// ====== 页面初始化 ======
document.addEventListener('DOMContentLoaded', () => {
    loadKbSelector();
    loadSessions();    // 加载会话列表
    clearChatArea();   // 显示欢迎界面
    const input = document.getElementById('chat-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            // Enter 发送消息（Shift+Enter 换行）
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        input.focus();
    }
});
