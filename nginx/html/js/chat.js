// nginx/html/js/chat.js
// SSE Streaming Chat Controller for Corporate RAG

let currentSessionId = generateSessionId();
let abortController = null;
let activeEventSource = null;
let sessions = [];  // Cached session list for sidebar rendering

function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

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

async function loadSessions() {
    // Load session list from API and render sidebar.
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

function renderSidebar(activeId = null) {
    // Render session list in sidebar.
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

function formatSessionDate(dateStr) {
    // Format session date for display.
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

async function switchSession(sessionId) {
    // Switch to an existing session.
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

        // Update KB selector
        const session = sessions.find(s => s.id === sessionId);
        if (session) {
            document.getElementById('kb-select').value = session.kb_id || '';
        }

        // Highlight current session
        renderSidebar(sessionId);
    } catch (err) {
        console.error('Failed to load session messages:', err);
        showToast('加载消息失败', 'error');
    }
}

function newSession() {
    // Create a new session.
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

async function deleteSession(sessionId) {
    // Delete a session with confirmation.
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

function renderMessages(messages) {
    // Render a list of messages into the chat area.
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

function clearChatArea() {
    // Clear chat area and show welcome state.
    const container = document.getElementById('chat-messages');
    container.innerHTML = getWelcomeHtml();
}

function sendMessage() {
    const input = document.getElementById('chat-input');
    const query = input.value.trim();
    if (!query) return;

    const kbId = document.getElementById('kb-select').value;
    input.value = '';

    // Add user message
    addMessage(query, 'user');

    // Add placeholder assistant message
    const assistantDiv = addMessage('', 'assistant');
    const contentDiv = assistantDiv.querySelector('.message-content');
    const statusDiv = document.createElement('div');
    statusDiv.className = 'flex items-center gap-2 text-slate-400 text-sm';
    statusDiv.innerHTML = '<span class="spinner"></span> 思考中...';
    contentDiv.appendChild(statusDiv);

    // Connect to SSE
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

    // Close any existing SSE connection
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }

    abortController = new AbortController();
    const evtSource = new EventSource(`/api/chat/stream?${params}`);
    activeEventSource = evtSource;

    let fullText = '';
    const citations = [];

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

    evtSource.addEventListener('token', (e) => {
        try {
            const data = JSON.parse(e.data);
            fullText += data.token;
            // Remove status indicator, render markdown
            if (statusDiv.parentNode) statusDiv.remove();
            contentDiv.innerHTML = typeof marked !== 'undefined'
                ? marked.parse(fullText)
                : fullText;
            // Auto scroll
            const container = document.getElementById('chat-messages');
            container.scrollTop = container.scrollHeight;
        } catch (err) {
            console.error('Token parse error:', err);
        }
    });

    evtSource.addEventListener('citation', (e) => {
        try {
            const data = JSON.parse(e.data);
            citations.push(data);
        } catch (err) {
            console.error('Citation parse error:', err);
        }
    });

    evtSource.addEventListener('done', () => {
        evtSource.close();
        activeEventSource = null;
        abortController = null;

        // Append citations if any
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

        // Scroll to bottom
        document.getElementById('chat-messages').scrollTop =
            document.getElementById('chat-messages').scrollHeight;

        loadSessions();  // Refresh sidebar to show updated session list
    });

    evtSource.addEventListener('error', (e) => {
        evtSource.close();
        activeEventSource = null;
        abortController = null;

        // Parse error data into categorized user-friendly messages
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

    // For user messages, put text directly; for assistant, it's placeholder
    if (isUser) {
        // Already set in innerHTML above
    }

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function resetSession() {
    newSession();
}

// Send on Enter (without Shift)
document.addEventListener('DOMContentLoaded', () => {
    loadKbSelector();
    loadSessions();  // Load session list on page load
    clearChatArea();  // Show welcome state
    const input = document.getElementById('chat-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        input.focus();
    }
});
