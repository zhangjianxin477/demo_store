var API_BASE = window.API_BASE || '/api/v1';
window.API_BASE = API_BASE;

var KH_ACCESS_TOKEN_KEY = 'kh_access_token';
var KH_REFRESH_TOKEN_KEY = 'kh_refresh_token';
var KH_ACCESS_ROLE_KEY = 'kh_access_role';
var KH_ACCESS_PERMISSIONS_KEY = 'kh_access_permissions';

function getAccessToken() {
    return sessionStorage.getItem(KH_ACCESS_TOKEN_KEY) || localStorage.getItem(KH_ACCESS_TOKEN_KEY) || '';
}

function getAccessRole() {
    return sessionStorage.getItem(KH_ACCESS_ROLE_KEY) || localStorage.getItem(KH_ACCESS_ROLE_KEY) || '';
}

function getAccessPermissions() {
    try {
        const raw = sessionStorage.getItem(KH_ACCESS_PERMISSIONS_KEY)
            || localStorage.getItem(KH_ACCESS_PERMISSIONS_KEY)
            || '[]';
        const value = JSON.parse(raw);
        return Array.isArray(value) ? value : [];
    } catch {
        return [];
    }
}

function hasWriteAccess() {
    return getAccessRole() === 'owner' && getAccessPermissions().includes('write');
}

function isGuestAccess() {
    return getAccessRole() === 'guest';
}

function getAccessHeaders() {
    const token = getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
}

function isWriteApiRequest(method, path) {
    const verb = String(method || 'GET').toUpperCase();
    const cleanPath = String(path || '').split('?')[0].replace(/\/$/, '') || '/';
    if (['GET', 'HEAD', 'OPTIONS'].includes(verb)) return false;
    if (['PUT', 'PATCH', 'DELETE'].includes(verb)) return true;
    if (verb !== 'POST') return false;
    const readOnlyPosts = new Set([
        '/auth/login', '/auth/guest', '/auth/refresh',
        '/documents/analyze-layout', '/documents/convert',
        '/file-resources/query', '/query',
        '/rag/query', '/rag/query/stream', '/rag/rewrite-query', '/rag/web-search', '/rag/session/create',
        '/knowledge-graph/query', '/knowledge-graph/rewrite-query', '/kg/rewrite-query',
        '/evaluate', '/wiki/share/access', '/wiki/versions/diff', '/wiki/versions/text-diff',
        '/wiki/embeds/resolve', '/wiki/api-keys/validate', '/wiki/ai/copilot',
    ]);
    return !readOnlyPosts.has(cleanPath)
        && !cleanPath.startsWith('/wiki/search/')
        && !cleanPath.startsWith('/wiki/ai/summarize/')
        && !cleanPath.startsWith('/wiki/ai/chapter-summary/')
        && !cleanPath.startsWith('/wiki/ai/mindmap/')
        && !cleanPath.startsWith('/wiki/ai/explain-terms/');
}

function requireWriteAccess() {
    if (hasWriteAccess()) return true;
    if (typeof showToast === 'function') {
        showToast('游客模式仅支持阅读和检索', 'warning');
    }
    return false;
}

window.getAccessToken = getAccessToken;
window.getAccessRole = getAccessRole;
window.getAccessPermissions = getAccessPermissions;
window.getAccessHeaders = getAccessHeaders;
window.hasWriteAccess = hasWriteAccess;
window.isGuestAccess = isGuestAccess;
window.isWriteApiRequest = isWriteApiRequest;
window.requireWriteAccess = requireWriteAccess;

var _backendOnline = typeof window._backendOnline === 'boolean' ? window._backendOnline : null;
var _backendCheckTime = window._backendCheckTime || 0;
var BACKEND_CHECK_INTERVAL = window.BACKEND_CHECK_INTERVAL || 30000;
var CLOUD_OFFLINE_FAILURE_THRESHOLD = window.CLOUD_OFFLINE_FAILURE_THRESHOLD || 3;
var _backendFailureCount = window._backendFailureCount || 0;
var _backendLastFailureAt = window._backendLastFailureAt || 0;

function ensureLocalEmbeddingService() {
    if (window.localEmbeddingService) return window.localEmbeddingService;
    const fallback = {
        get isAvailable() { return false; },
        get isLoading() { return false; },
        get dimension() { return 0; },
        setProgressCallback() {},
        async initialize() { return false; },
        async embed() { return null; },
        async embedText() { return null; },
        async embedTexts(texts) { return (texts || []).map(() => null); },
        keywordSearch(query, chunks, topK = 5) {
            const terms = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
            return (chunks || [])
                .map((chunk, index) => {
                    const text = String(chunk.text || chunk.content || '').toLowerCase();
                    const score = terms.reduce((sum, term) => sum + (text.includes(term) ? 1 : 0), 0);
                    return { ...chunk, score, chunk_id: chunk.chunk_id || chunk.id || `local-${index}` };
                })
                .filter(item => item.score > 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, topK);
        },
        vectorSearch() { return []; },
        hybridSearch(query, chunks, topK = 5) {
            return this.keywordSearch(query, chunks, topK);
        },
    };
    window.localEmbeddingService = fallback;
    return fallback;
}

ensureLocalEmbeddingService();

function markBackendOnline(isOnline, options = {}) {
    const wasOnline = _backendOnline === true;
    const nextOnline = isOnline === true;

    if (!nextOnline) {
        const failureAt = Date.now();
        if (!options.forceCount && failureAt - _backendLastFailureAt < 1500) {
            return;
        }
        _backendLastFailureAt = failureAt;
        window._backendLastFailureAt = failureAt;
        _backendFailureCount += 1;
        window._backendFailureCount = _backendFailureCount;
        _backendCheckTime = Date.now();
        window._backendCheckTime = _backendCheckTime;

        if (!options.immediate && wasOnline && _backendFailureCount < CLOUD_OFFLINE_FAILURE_THRESHOLD) {
            return;
        }
    } else {
        _backendFailureCount = 0;
        window._backendFailureCount = 0;
    }

    _backendOnline = isOnline === true;
    window._backendOnline = _backendOnline;
    _backendCheckTime = Date.now();
    window._backendCheckTime = _backendCheckTime;
    updateOnlineStatus();

    if (_backendOnline && (!wasOnline || options.sync)) {
        setTimeout(() => {
            if (typeof processPendingCloudSync === 'function') {
                processPendingCloudSync({ silent: true }).catch(() => {});
            }
            if (typeof loadServerFileResources === 'function') {
                loadServerFileResources({ silent: true }).catch(() => {});
            }
        }, 0);
    }
}

async function checkBackendOnline(options = {}) {
    const force = options.force === true;
    const now = Date.now();
    if (!force && _backendOnline !== null && now - _backendCheckTime < BACKEND_CHECK_INTERVAL) {
        return _backendOnline;
    }

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000);
        const res = await fetch(`${API_BASE}/info`, {
            signal: controller.signal,
            cache: 'no-store',
        });
        clearTimeout(timeoutId);
        markBackendOnline(res.ok);
    } catch {
        markBackendOnline(false);
    }

    return _backendOnline;
}

function updateOnlineStatus() {
    const statusEl = document.getElementById('system-status');
    if (!statusEl) return;

    if (_backendOnline === null) {
        statusEl.innerHTML = '<span class="status-dot" style="background:#94a3b8"></span> 正在连接';
        statusEl.title = '正在检测云端 API 连接状态';
        return;
    }

    if (_backendOnline) {
        statusEl.innerHTML = '<span class="status-dot" style="background:#22c55e"></span> 在线';
        statusEl.title = '云端 API 已连接，正在使用服务器数据';
        return;
    }

    statusEl.innerHTML = '<span class="status-dot" style="background:#f59e0b"></span> 云端未连接 · 无法保存到服务器';
    statusEl.title = '当前浏览器无法访问云端 API。编辑内容会先保存为本地草稿，云端恢复后再同步。';
}

async function api(method, path, body = null, timeoutMs = 0) {
    if (isGuestAccess() && isWriteApiRequest(method, path)) {
        const error = new Error('游客模式仅支持阅读和检索');
        error.isHttpError = true;
        error.status = 403;
        throw error;
    }
    const opts = {
        method,
        headers: getAccessHeaders(),
        cache: 'no-store',
    };

    if (body && !(body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
        opts.body = body;
    }

    let timeoutId = null;
    if (timeoutMs > 0) {
        const controller = new AbortController();
        opts.signal = controller.signal;
        timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    }

    try {
        const res = await fetch(`${API_BASE}${path}`, opts);
        if (timeoutId) clearTimeout(timeoutId);

        if (!res.ok) {
            let errMsg = `HTTP ${res.status}`;
            try {
                const data = await res.json();
                errMsg = data.detail
                    ? (typeof data.detail === 'string' ? data.detail : (data.detail.message || JSON.stringify(data.detail)))
                    : (data.message || errMsg);
            } catch {}
            const err = new Error(errMsg);
            err.isHttpError = true;
            err.status = res.status;
            throw err;
        }

        markBackendOnline(true);

        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) return await res.json();
        const text = await res.text();
        return text ? JSON.parse(text) : { success: true };
    } catch (err) {
        if (timeoutId) clearTimeout(timeoutId);
        if (!err.isHttpError) {
            markBackendOnline(false);
        }
        throw err;
    }
}

