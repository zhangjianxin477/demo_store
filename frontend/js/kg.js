let kgGraphData = { nodes: [], edges: [] };
let kgCurrentDocKey = null;
let kgSimulation = null;
let kgCanvas = null;
let kgCtx = null;
let kgDragNode = null;
let kgHoverNode = null;
let kgSelectedNode = null;
let kgHighlightedNodeIds = new Set();
let kgHighlightedEdgeIds = new Set();
let kgOffset = { x: 0, y: 0 };
let kgScale = 1;
let kgZoomTargetScale = 1;
let kgZoomAnchor = null;
let kgZoomAnimating = false;
let kgZoomSaveTimer = null;
let kgHoverFade = 0;
let kgHoverFadeTarget = 0;
let kgHoverFocusNodeId = null;
let kgHoverFrameNodeIds = new Set();
let kgHoverFrameEdgeIds = new Set();
let kgHoverFrameNodeId = null;
let kgGraphViewMode = 'current';
let kgLastAnimTime = 0;
let kgIsDragging = false;
let kgLastMouse = { x: 0, y: 0 };
let kgIsPanning = false;
let kgPanStart = { x: 0, y: 0 };
let kgInitialized = false;
let kgReasoningAnim = null;
let kgReasoningPath = [];
let kgReasoningStep = -1;
let kgAnimFrame = null;
let kgSimAlpha = 1;
let kgDragVelocity = { x: 0, y: 0 };
let kgLastDragPos = { x: 0, y: 0 };
let kgLastDragTime = 0;
let kgDragTarget = null;
let kgPulsePhase = 0;
let kgAggregationEnabled = true;
const KG_AGGREGATION_THRESHOLD = 200;
const KG_AGGREGATION_CELL_SIZE = 60;
let kgActiveCanvasId = 'kg-mini-canvas';
let kgFullGraphModal = null;
let kgLastRenderedNodeIds = new Set();
const kgViewStateByCanvas = new Map();
const kgFittedCanvasIds = new Set();
let kgHydrationVersion = 0;
let kgHydrationRunning = false;
let kgQuerySending = false;
let kgGraphOpenBindingDone = false;
let kgGlobalEventsBound = false;
let kgRefreshRetryTimer = null;
let kgGraphDataRevision = 0;
let kgVisibleGraphCacheKey = '';
let kgVisibleGraphCacheValue = null;
const KG_QUERY_TIMEOUT_MS = 12000;
const KG_RAG_FALLBACK_TIMEOUT_MS = 8000;

const ENTITY_COLORS = {
    person: '#7c7fd4',
    organization: '#4a9ea8',
    location: '#5a9e80',
    event: '#c49a40',
    concept: '#b06a94',
    technology: '#5a88c4',
    term: '#7e68a8',
    document: '#3b82f6',
    anchor: '#8b5cf6',
    tag: '#8e6ab0',
    reference: '#4a9a90',
    law: '#3a7bd5',
    crime: '#d04040',
    default: '#7e68a8',
};

const RELATIONSHIP_STYLES = {
    related_to: { color: '#6b7280', style: 'dashed', label: '关联' },
    part_of: { color: '#5a8e96', style: 'dashed', label: '部分' },
    located_in: { color: '#5a9e80', style: 'solid', label: '位于' },
    works_for: { color: '#7c7fd4', style: 'solid', label: '工作' },
    created_by: { color: '#b89a40', style: 'dashed', label: '创建' },
    depends_on: { color: '#a06060', style: 'dashed', label: '依赖' },
    supports: { color: '#5a9e80', style: 'solid', label: '支持' },
    used_by: { color: '#7e68a8', style: 'dashed', label: '使用' },
    belongs_to: { color: '#5a8e96', style: 'solid', label: '归属' },
    contains: { color: '#7c7fd4', style: 'solid', label: '包含' },
    influences: { color: '#b06a94', style: 'dashed', label: '影响' },
    derived_from: { color: '#b89a40', style: 'dashed', label: '派生' },
    implements: { color: '#5a9e80', style: 'solid', label: '实现' },
    precedes: { color: '#7e68a8', style: 'dashed', label: '先于' },
    contradicts: { color: '#a06060', style: 'dashed', label: '矛盾' },
    defined_by: { color: '#4a90d9', style: 'solid', label: '定义依据' },
    includes: { color: '#7c7fd4', style: 'solid', label: '包含' },
    applies_to: { color: '#5a9e80', style: 'solid', label: '适用于' },
    punishes: { color: '#c05050', style: 'solid', label: '处罚' },
    requires: { color: '#d4a030', style: 'solid', label: '要求' },
    causes: { color: '#e07040', style: 'solid', label: '导致' },
    derives_from: { color: '#b89a40', style: 'dashed', label: '衍生自' },
    competes_with: { color: '#a06060', style: 'dashed', label: '竞合' },
    restricts: { color: '#8060b0', style: 'dashed', label: '限制' },
    constitutes: { color: '#5a88c4', style: 'solid', label: '构成' },
    not_constitutes: { color: '#e05050', style: 'dashed', label: '不构成' },
    bears: { color: '#c49a40', style: 'solid', label: '承担' },
    protects: { color: '#4a9ea8', style: 'solid', label: '保护' },
    co_occurs_with: { color: '#9ca3af', style: 'dotted', label: '共现' },
    file_similarity: { color: '#d0d4dc', style: 'solid', label: '文件相关' },
    file_link: { color: '#b7bcc6', style: 'solid', label: '文档链接' },
    folder_neighbor: { color: '#c8ccd4', style: 'solid', label: '同目录' },
    theme_overlap: { color: '#aeb7c6', style: 'solid', label: '主题相似' },
    has_attribute: { color: '#7e68a8', style: 'solid', label: '属性' },
    has_penalty: { color: '#c05050', style: 'solid', label: '处罚方式' },
    has_standard: { color: '#4a90d9', style: 'solid', label: '标准' },
    has_subject: { color: '#7c7fd4', style: 'solid', label: '主体' },
    has_element: { color: '#d4a030', style: 'solid', label: '要素' },
    excludes: { color: '#e05050', style: 'dashed', label: '排除' },
    classifies: { color: '#5a8e96', style: 'solid', label: '分类' },
    has_type: { color: '#7e68a8', style: 'solid', label: '类型' },
    distinguishes: { color: '#5a9e80', style: 'dashed', label: '区分' },
    has_anchor: { color: '#8b5cf6', style: 'solid', label: '记忆锚点' },
    thematic_similarity: { color: '#3b82f6', style: 'solid', label: '主题相似' },
    shared_entities: { color: '#f59e0b', style: 'dashed', label: '共享实体' },
    causal_link: { color: '#ef4444', style: 'solid', label: '因果关联' },
    complementary: { color: '#10b981', style: 'dashed', label: '互补关系' },
};

const RELATIONSHIP_COLORS = {};
for (const [k, v] of Object.entries(RELATIONSHIP_STYLES)) {
    RELATIONSHIP_COLORS[k] = v.color;
}


function isMarkdownFileNode(node) {
    return node?.properties?.source === 'markdown_file_graph'
        || node?.properties?.source === 'markdown_file_name_similarity';
}

function replaceKGGraphData(nextGraph) {
    kgGraphData = nextGraph || { nodes: [], edges: [] };
    kgGraphDataRevision += 1;
    kgVisibleGraphCacheKey = '';
    kgVisibleGraphCacheValue = null;
}

function normalizeKGDocPath(path) {
    return String(path || '')
        .replace(/\\/g, '/')
        .replace(/\/+/g, '/')
        .replace(/^\.\//, '')
        .trim();
}

function getKGNodeDocPath(node) {
    return normalizeKGDocPath(node?.properties?.path || node?.id || node?.name || '');
}

function getKGCurrentDocPath() {
    if (typeof fileStore !== 'undefined' && fileStore.openedFile) {
        return normalizeKGDocPath(fileStore.openedFile.path || fileStore.openedFile.name || '');
    }
    return normalizeKGDocPath(kgCurrentDocKey || '');
}

function isSameKGDocPath(a, b) {
    const left = normalizeKGDocPath(a);
    const right = normalizeKGDocPath(b);
    if (!left || !right) return false;
    if (left === right) return true;
    const leftName = left.replace(/^.*\//, '').replace(/\.(md|markdown)$/i, '').toLowerCase();
    const rightName = right.replace(/^.*\//, '').replace(/\.(md|markdown)$/i, '').toLowerCase();
    return !!leftName && leftName === rightName;
}

function isCurrentKGDocNode(node) {
    return isMarkdownFileNode(node) && isSameKGDocPath(getKGNodeDocPath(node), getKGCurrentDocPath());
}

function hashKGString(value) {
    let h = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

function saveKGCanvasViewState() {
    if (!kgCanvas || !kgActiveCanvasId) return;
    const key = kgActiveCanvasId === 'kg-full-canvas' ? `${kgActiveCanvasId}:${kgGraphViewMode}` : kgActiveCanvasId;
    kgViewStateByCanvas.set(key, {
        scale: kgScale,
        offset: { x: kgOffset.x, y: kgOffset.y },
    });
}

function restoreKGCanvasViewState(canvasId) {
    const key = canvasId === 'kg-full-canvas' ? `${canvasId}:${kgGraphViewMode}` : canvasId;
    const state = kgViewStateByCanvas.get(key);
    if (!state) return false;
    kgScale = state.scale || 1;
    kgZoomTargetScale = kgScale;
    kgOffset = {
        x: Number.isFinite(state.offset?.x) ? state.offset.x : 0,
        y: Number.isFinite(state.offset?.y) ? state.offset.y : 0,
    };
    return true;
}

function scheduleKGViewStateSave() {
    if (kgZoomSaveTimer) clearTimeout(kgZoomSaveTimer);
    kgZoomSaveTimer = setTimeout(() => {
        kgZoomSaveTimer = null;
        saveKGCanvasViewState();
    }, 140);
}

function setKGSmoothZoom(targetScale, screenX, screenY) {
    if (!kgCanvas) return;
    const nextTarget = Math.max(0.2, Math.min(5, targetScale));
    const anchorX = Number.isFinite(screenX) ? screenX : kgCanvas.width / 2;
    const anchorY = Number.isFinite(screenY) ? screenY : kgCanvas.height / 2;
    kgZoomAnchor = {
        screenX: anchorX,
        screenY: anchorY,
        worldX: (anchorX - kgOffset.x) / kgScale,
        worldY: (anchorY - kgOffset.y) / kgScale,
    };
    kgZoomTargetScale = nextTarget;
    kgZoomAnimating = Math.abs(kgZoomTargetScale - kgScale) > 0.0005;
    scheduleKGViewStateSave();
}

function updateKGSmoothZoom() {
    if (!kgZoomAnimating || !kgZoomAnchor) return false;
    const distance = kgZoomTargetScale - kgScale;
    const nextScale = Math.abs(distance) < 0.001
        ? kgZoomTargetScale
        : kgScale + distance * 0.34;
    kgScale = nextScale;
    kgOffset.x = kgZoomAnchor.screenX - kgZoomAnchor.worldX * kgScale;
    kgOffset.y = kgZoomAnchor.screenY - kgZoomAnchor.worldY * kgScale;
    if (Math.abs(kgZoomTargetScale - kgScale) < 0.001) {
        kgScale = kgZoomTargetScale;
        kgOffset.x = kgZoomAnchor.screenX - kgZoomAnchor.worldX * kgScale;
        kgOffset.y = kgZoomAnchor.screenY - kgZoomAnchor.worldY * kgScale;
        kgZoomAnimating = false;
        kgZoomAnchor = null;
        scheduleKGViewStateSave();
    }
    return true;
}

function setKGHoverNode(node) {
    const nextId = node?.id || null;
    if (nextId === kgHoverFocusNodeId) return;
    kgHoverNode = node || null;
    kgHoverFocusNodeId = nextId;
    kgHoverFadeTarget = nextId ? 1 : 0;
    if (nextId) {
        kgHoverFade = 0;
        kgHoverFrameNodeId = nextId;
        kgHoverFrameNodeIds = new Set([node.id]);
        kgHoverFrameEdgeIds = new Set();
        for (const edge of kgGraphData.edges || []) {
            if (edge.source === node.id || edge.target === node.id) {
                kgHoverFrameEdgeIds.add(edge.id || `${edge.source}-${edge.target}`);
                kgHoverFrameNodeIds.add(edge.source === node.id ? edge.target : edge.source);
            }
        }
    }
}

function updateKGHoverFade() {
    const target = kgHoverFadeTarget;
    const step = target > kgHoverFade ? 0.07 : 0.055;
    const next = kgHoverFade + (target - kgHoverFade) * step;
    kgHoverFade = Math.abs(next - target) < 0.01 ? target : next;
    if (kgHoverFade === 0 && !kgHoverFocusNodeId) {
        kgHoverFrameNodeId = null;
        kgHoverFrameNodeIds.clear();
        kgHoverFrameEdgeIds.clear();
    }
    return Math.abs(kgHoverFade - target) > 0.001;
}

function easeKGHover(value) {
    const t = Math.max(0, Math.min(1, value));
    return t * t * (3 - 2 * t);
}

function getKGFileGraphFocusId(nodes = kgGraphData.nodes) {
    const currentPath = getKGCurrentDocPath();
    const selectedPath = getKGNodeDocPath(kgSelectedNode);
    const currentNode = nodes.find(node => isSameKGDocPath(getKGNodeDocPath(node), currentPath));
    if (currentNode) return currentNode.id;
    const selectedNode = nodes.find(node => isSameKGDocPath(getKGNodeDocPath(node), selectedPath));
    if (selectedNode) return selectedNode.id;
    const maxConnections = Math.max(0, ...nodes.map(node => node.connections || 0));
    return nodes.find(node => (node.connections || 0) === maxConnections)?.id || nodes[0]?.id || '';
}

function shouldShowKGCurrentScope() {
    return kgActiveCanvasId === 'kg-mini-canvas' || kgGraphViewMode === 'current';
}

function getKGVisibleFileGraph(nodes = kgGraphData.nodes, edges = kgGraphData.edges) {
    const cacheKey = [
        kgGraphDataRevision,
        kgActiveCanvasId,
        kgGraphViewMode,
        getKGCurrentDocPath(),
        nodes.length,
        edges.length,
    ].join('|');
    if (kgVisibleGraphCacheKey === cacheKey && kgVisibleGraphCacheValue) {
        return kgVisibleGraphCacheValue;
    }

    const cacheResult = (value) => {
        kgVisibleGraphCacheKey = cacheKey;
        kgVisibleGraphCacheValue = value;
        return value;
    };
    const currentScope = shouldShowKGCurrentScope();
    const maxVisible = kgActiveCanvasId === 'kg-mini-canvas' ? 42 : 72;
    if (!currentScope) {
        return cacheResult({ nodes, edges });
    }

    const focusId = getKGFileGraphFocusId(nodes);
    if (!focusId) {
        const topNodes = [...nodes]
            .sort((a, b) => (b.connections || 0) - (a.connections || 0))
            .slice(0, maxVisible);
        const keep = new Set(topNodes.map(node => node.id));
        return cacheResult({
            nodes: topNodes,
            edges: edges.filter(edge => keep.has(edge.source) && keep.has(edge.target)),
        });
    }

    const keep = new Set([focusId]);
    const directEdges = [];
    for (const edge of edges) {
        if (edge.source === focusId || edge.target === focusId) {
            directEdges.push(edge);
            keep.add(edge.source === focusId ? edge.target : edge.source);
        }
    }

    if (keep.size < 18) {
        for (const edge of edges) {
            if (keep.has(edge.source) || keep.has(edge.target)) {
                keep.add(edge.source);
                keep.add(edge.target);
            }
            if (keep.size >= Math.min(maxVisible, 44)) break;
        }
    }

    const visibleNodes = nodes.filter(node => keep.has(node.id)).slice(0, maxVisible);
    const visibleIds = new Set(visibleNodes.map(node => node.id));
    return cacheResult({
        nodes: visibleNodes,
        edges: edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    });
}

function fitKGGraphToCanvas(force = false) {
    if (!kgCanvas || !kgGraphData.nodes.length) return;
    const fitKey = [
        kgActiveCanvasId,
        kgGraphViewMode,
        kgGraphData.nodes.length,
        kgGraphData.edges.length,
        getKGCurrentDocPath(),
    ].join('|');
    if (!force && kgFittedCanvasIds.has(fitKey)) return;

    const isFileGraph = kgGraphData.nodes.some(isMarkdownFileNode);
    const renderGraph = isFileGraph
        ? getKGVisibleFileGraph(kgGraphData.nodes, kgGraphData.edges)
        : { nodes: kgGraphData.nodes, edges: kgGraphData.edges };
    const nodes = renderGraph.nodes.length ? renderGraph.nodes : kgGraphData.nodes;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;

    for (const node of nodes) {
        const r = node.radius || 6;
        minX = Math.min(minX, node.x - r);
        maxX = Math.max(maxX, node.x + r);
        minY = Math.min(minY, node.y - r);
        maxY = Math.max(maxY, node.y + r);
    }

    if (!Number.isFinite(minX) || !Number.isFinite(maxX) || minX === maxX || minY === maxY) return;

    const pad = kgActiveCanvasId === 'kg-mini-canvas' ? 28 : 82;
    const graphW = Math.max(1, maxX - minX);
    const graphH = Math.max(1, maxY - minY);
    const fitScale = Math.min(
        kgActiveCanvasId === 'kg-mini-canvas' ? 1.55 : 2.4,
        Math.max(0.18, Math.min((kgCanvas.width - pad * 2) / graphW, (kgCanvas.height - pad * 2) / graphH))
    );
    kgScale = Number.isFinite(fitScale) ? fitScale : 1;
    kgOffset = {
        x: (kgCanvas.width - graphW * kgScale) / 2 - minX * kgScale,
        y: (kgCanvas.height - graphH * kgScale) / 2 - minY * kgScale,
    };
    kgFittedCanvasIds.add(fitKey);
    saveKGCanvasViewState();
}

async function initKGPage() {
    bindKGGlobalEvents();
    if (kgInitialized) {
        setupKGChatEvents();
        setupKGGraphOpenEvents();
        ensureKGCanvas();
        if (isKGCanvasRenderable()) {
            resizeKGCanvas();
            await refreshKGGraph();
        } else {
            scheduleKGGraphRetry(160);
        }
        return;
    }
    kgInitialized = true;

    kgCanvas = document.getElementById('kg-mini-canvas');
    if (!kgCanvas) return;
    kgCtx = kgCanvas.getContext('2d');

    resizeKGCanvas();
    setupKGCanvasEvents();
    setupKGChatEvents();
    setupKGGraphOpenEvents();
    if (isKGCanvasRenderable()) {
        await refreshKGGraph();
    } else {
        scheduleKGGraphRetry(160);
    }
    startKGAnimLoop();
}

function bindKGGlobalEvents() {
    if (kgGlobalEventsBound || typeof document === 'undefined') return;
    kgGlobalEventsBound = true;

    document.addEventListener('click', (e) => {
        const sendBtn = e.target.closest?.('#kg-chat-panel .send-btn');
        if (sendBtn) {
            e.preventDefault();
            sendKGQuery();
            return;
        }

        const graphOpenTarget = e.target.closest?.('[data-kg-graph-open]');
        if (graphOpenTarget) {
            e.preventDefault();
            openKGGraphPage(graphOpenTarget.dataset.kgGraphOpen || 'global');
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.target?.id !== 'kg-chat-input') return;
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendKGQuery();
        }
    });
}

function ensureKGCanvas() {
    const expectedId = kgActiveCanvasId === 'kg-full-canvas' && kgFullGraphModal?.style.display !== 'none'
        ? 'kg-full-canvas'
        : 'kg-mini-canvas';
    if (!kgCanvas || !document.contains(kgCanvas) || kgCanvas.id !== expectedId) {
        setKGCanvas(expectedId);
    }
    return kgCanvas;
}

function isKGCanvasRenderable() {
    if (!kgCanvas) return false;
    const rect = kgCanvas.getBoundingClientRect();
    return rect.width > 4 && rect.height > 4;
}

function scheduleKGGraphRetry(delay = 160) {
    if (kgRefreshRetryTimer) return;
    kgRefreshRetryTimer = setTimeout(() => {
        kgRefreshRetryTimer = null;
        ensureKGCanvas();
        const fullVisible = kgFullGraphModal && kgFullGraphModal.style.display !== 'none';
        const kgVisible = typeof currentPage === 'undefined' || currentPage === 'kg';
        if (!fullVisible && !kgVisible) return;
        if (!isKGCanvasRenderable()) {
            scheduleKGGraphRetry(Math.min(1000, delay * 1.5));
            return;
        }
        refreshKGGraph().catch(err => console.warn('retry KG graph refresh failed:', err));
    }, delay);
}

function setupKGChatEvents() {
    const input = document.getElementById('kg-chat-input');
    const sendBtn = document.querySelector('#kg-chat-panel .send-btn');
    if (input && input.dataset.kgChatBound !== 'true') {
        input.dataset.kgChatBound = 'true';
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendKGQuery();
            }
        });
    }
    if (sendBtn && sendBtn.dataset.kgChatBound !== 'true') {
        sendBtn.dataset.kgChatBound = 'true';
        sendBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            sendKGQuery();
        });
    }
    window.sendKGQuery = sendKGQuery;
    window.kgChatKeyDown = kgChatKeyDown;
}

function setupKGGraphOpenEvents() {
    if (kgGraphOpenBindingDone) return;
    kgGraphOpenBindingDone = true;

    window.openKGGraphPage = openKGGraphPage;
    window.closeKGGraphPage = closeKGGraphPage;
    window.kgZoomIn = kgZoomIn;
    window.kgZoomOut = kgZoomOut;
    window.kgResetView = kgResetView;

    document.querySelectorAll('[data-kg-graph-open]').forEach((graphOpenBtn) => {
        if (graphOpenBtn.dataset.kgOpenBound === 'true') return;
        graphOpenBtn.dataset.kgOpenBound = 'true';
        graphOpenBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openKGGraphPage(graphOpenBtn.dataset.kgGraphOpen || 'global');
        });
    });
}

async function loadDocGraphList() {
    try {
        const data = await api('GET', '/knowledge-graph/doc-graphs');
        const graphs = data.graphs || [];
        const select = document.getElementById('kg-doc-select');
        if (!select) return;
        const currentVal = select.value;
        select.innerHTML = '<option value="">全部图谱</option>';
        for (const g of graphs) {
            if (g.doc_key === '__global__') continue;
            const opt = document.createElement('option');
            opt.value = g.doc_key;
            opt.textContent = `${g.doc_key} (${g.node_count}节点/${g.edge_count}边)`;
            select.appendChild(opt);
        }
        if (kgCurrentDocKey) {
            select.value = kgCurrentDocKey;
        }
    } catch (err) {
        console.error('loadDocGraphList error:', err);
    }
}

function resizeKGCanvas() {
    if (!kgCanvas) return;
    const container = kgCanvas.parentElement;
    if (!container) return;
    kgCanvas.width = container.clientWidth;
    kgCanvas.height = container.clientHeight;
}

function setKGCanvas(canvasId, options = {}) {
    const nextCanvas = document.getElementById(canvasId);
    if (!nextCanvas) return false;
    saveKGCanvasViewState();
    kgActiveCanvasId = canvasId;
    kgCanvas = nextCanvas;
    kgCtx = kgCanvas.getContext('2d');
    resizeKGCanvas();
    setupKGCanvasEvents();
    if (!restoreKGCanvasViewState(canvasId)) {
        kgScale = 1;
        kgZoomTargetScale = 1;
        kgOffset = { x: 0, y: 0 };
        fitKGGraphToCanvas(true);
    }
    if (options.restartSimulation === true) {
        startKGSimulation();
    }
    return true;
}

function setupKGCanvasEvents() {
    if (!kgCanvas) return;
    if (kgCanvas.dataset.kgEventsBound === 'true') return;
    kgCanvas.dataset.kgEventsBound = 'true';

    kgCanvas.addEventListener('mousedown', (e) => {
        const pos = getKGMousePos(e);
        const node = findNodeAt(pos.x, pos.y);
        if (node) {
            kgDragNode = node;
            kgIsDragging = false;
            kgLastMouse = { x: e.clientX, y: e.clientY };
            kgLastDragPos = { x: pos.x, y: pos.y };
            kgLastDragTime = performance.now();
            kgDragVelocity = { x: 0, y: 0 };
            kgDragTarget = { x: pos.x, y: pos.y };
            kgCanvas.style.cursor = 'grabbing';
            kgSimAlpha = Math.max(kgSimAlpha, 0.3);
        } else {
            kgIsPanning = true;
            kgPanStart = { x: e.clientX - kgOffset.x, y: e.clientY - kgOffset.y };
            kgCanvas.style.cursor = 'move';
        }
    });

    kgCanvas.addEventListener('mousemove', (e) => {
        const pos = getKGMousePos(e);
        if (kgDragNode) {
            const dx = e.clientX - kgLastMouse.x;
            const dy = e.clientY - kgLastMouse.y;
            if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
                kgIsDragging = true;
            }

            const now = performance.now();
            const dt = Math.max(1, now - kgLastDragTime);
            kgDragVelocity = {
                x: (pos.x - kgLastDragPos.x) / dt * 16,
                y: (pos.y - kgLastDragPos.y) / dt * 16,
            };

            kgDragTarget = { x: pos.x, y: pos.y };
            kgLastDragPos = { x: pos.x, y: pos.y };
            kgLastDragTime = now;
            kgLastMouse = { x: e.clientX, y: e.clientY };
        } else if (kgIsPanning) {
            kgOffset.x = e.clientX - kgPanStart.x;
            kgOffset.y = e.clientY - kgPanStart.y;
        } else {
            const node = findNodeAt(pos.x, pos.y);
            if (node !== kgHoverNode) {
                setKGHoverNode(node);
                kgCanvas.style.cursor = node ? 'pointer' : 'default';
            }
        }
    });

    kgCanvas.addEventListener('mouseup', (e) => {
        if (kgDragNode) {
            if (!kgIsDragging) {
                selectKGNode(kgDragNode);
            } else {
                kgDragNode._userPosition = true;
                kgDragNode.layoutX = kgDragNode.x;
                kgDragNode.layoutY = kgDragNode.y;
                kgDragNode.vx = kgDragVelocity.x * 0.5;
                kgDragNode.vy = kgDragVelocity.y * 0.5;
                kgSimAlpha = Math.max(kgSimAlpha, 0.5);
            }
        } else if (!kgIsPanning) {
            const pos = getKGMousePos(e);
            const node = findNodeAt(pos.x, pos.y);
            if (!node) {
                kgSelectedNode = null;
                kgHighlightedNodeIds.clear();
                kgHighlightedEdgeIds.clear();
                stopReasoningAnimation();
                setKGHoverNode(null);
            }
        }
        kgDragNode = null;
        kgDragTarget = null;
        kgIsDragging = false;
        kgIsPanning = false;
        kgCanvas.style.cursor = 'default';
        saveKGCanvasViewState();
    });

    kgCanvas.addEventListener('mouseleave', () => {
        if (kgDragNode) {
            kgDragNode.vx = kgDragVelocity.x * 0.3;
            kgDragNode.vy = kgDragVelocity.y * 0.3;
            kgSimAlpha = Math.max(kgSimAlpha, 0.3);
        }
        kgDragNode = null;
        kgDragTarget = null;
        kgIsDragging = false;
        kgIsPanning = false;
        setKGHoverNode(null);
        kgCanvas.style.cursor = 'default';
    });

    kgCanvas.addEventListener('dblclick', (e) => {
        const pos = getKGMousePos(e);
        const node = findNodeAt(pos.x, pos.y);
        if (node) {
            startReasoningAnimation(node);
        }
    });

    kgCanvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = kgCanvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const normalizedDelta = Math.max(-80, Math.min(80, e.deltaY));
        const baseScale = kgZoomAnimating ? kgZoomTargetScale : kgScale;
        const factor = Math.exp(-normalizedDelta * 0.0022);
        setKGSmoothZoom(baseScale * factor, mx, my);
    }, { passive: false });
}

function getKGMousePos(e) {
    const rect = kgCanvas.getBoundingClientRect();
    return {
        x: (e.clientX - rect.left - kgOffset.x) / kgScale,
        y: (e.clientY - rect.top - kgOffset.y) / kgScale,
    };
}

function findNodeAt(x, y) {
    const isFileGraph = kgGraphData.nodes.some(isMarkdownFileNode);
    const candidateNodes = isFileGraph
        ? getKGVisibleFileGraph(kgGraphData.nodes, kgGraphData.edges).nodes
        : kgGraphData.nodes;
    for (let i = candidateNodes.length - 1; i >= 0; i--) {
        const node = candidateNodes[i];
        const dx = node.x - x;
        const dy = node.y - y;
        const r = (node.radius || 12) + 4;
        if (dx * dx + dy * dy < r * r) return node;
    }
    return null;
}

async function openKGGraphDocumentNode(node) {
    const path = getKGNodeDocPath(node);
    if (!path) return;
    kgCurrentDocKey = path;
    kgSelectedNode = node;
    highlightNodeNetwork(node);
    stopReasoningAnimation();

    if (kgActiveCanvasId === 'kg-full-canvas') {
        closeKGGraphPage();
    }

    if (typeof selectFile === 'function') {
        try {
            await selectFile(path, { refreshGraph: false });
        } catch (err) {
            console.warn('open KG document node failed:', err);
        }
    }

    const latestNode = kgGraphData.nodes.find(candidate => isSameKGDocPath(getKGNodeDocPath(candidate), path));
    if (latestNode) {
        kgSelectedNode = latestNode;
        highlightNodeNetwork(latestNode);
    }
    saveKGCanvasViewState();
    await refreshKGGraph(undefined, { reuse: true });
}

function syncKGSelectionToCurrentDoc() {
    const currentPath = getKGCurrentDocPath();
    if (!currentPath) return;
    for (const node of kgGraphData.nodes) {
        if (!isMarkdownFileNode(node)) continue;
        const isCurrent = isSameKGDocPath(getKGNodeDocPath(node), currentPath);
        node.isCurrent = isCurrent;
        if (node.properties) node.properties.isCurrent = isCurrent;
    }
    const currentNode = kgGraphData.nodes.find(node => isSameKGDocPath(getKGNodeDocPath(node), currentPath));
    if (!currentNode) return;
    kgSelectedNode = currentNode;
    highlightNodeNetwork(currentNode);
}

async function selectKGNode(node) {
    if (isMarkdownFileNode(node)) {
        await openKGGraphDocumentNode(node);
        return;
    }
    if (kgSelectedNode === node) {
        kgSelectedNode = null;
        kgHighlightedNodeIds.clear();
        kgHighlightedEdgeIds.clear();
        stopReasoningAnimation();
    } else {
        kgSelectedNode = node;
        highlightNodeNetwork(node);
    }
    showKGNodeInfo(node);
}

function highlightNodeNetwork(node) {
    kgHighlightedNodeIds.clear();
    kgHighlightedEdgeIds.clear();

    kgHighlightedNodeIds.add(node.id);

    for (const edge of kgGraphData.edges) {
        if (edge.source === node.id || edge.target === node.id) {
            kgHighlightedEdgeIds.add(edge.id || `${edge.source}-${edge.target}`);
            kgHighlightedNodeIds.add(edge.source === node.id ? edge.target : edge.source);
        }
    }
}

async function startReasoningAnimation(node) {
    stopReasoningAnimation();

    try {
        const data = await api('GET', `/knowledge-graph/reasoning-path/${node.id}?max_depth=3`);
        kgReasoningPath = data.path || [];
        if (kgReasoningPath.length === 0) {
            showToast('该节点没有推理路径', 'info');
            return;
        }
    } catch (err) {
        kgReasoningPath = buildLocalReasoningPath(node);
        if (kgReasoningPath.length === 0) {
            showToast('该节点没有推理路径', 'info');
            return;
        }
    }

    kgSelectedNode = node;
    kgHighlightedNodeIds.clear();
    kgHighlightedEdgeIds.clear();
    kgHighlightedNodeIds.add(node.id);
    kgReasoningStep = 0;

    showReasoningToast(node);

    function animateStep() {
        if (kgReasoningStep >= kgReasoningPath.length) {
            kgReasoningAnim = null;
            return;
        }

        const step = kgReasoningPath[kgReasoningStep];
        if (step.new_nodes) {
            for (const nid of step.new_nodes) {
                kgHighlightedNodeIds.add(nid);
            }
        }
        if (step.edges) {
            for (const edgeData of step.edges) {
                const e = edgeData.edge;
                kgHighlightedEdgeIds.add(e.id || `${e.source}-${e.target}`);
            }
        }

        kgReasoningStep++;
        kgReasoningAnim = setTimeout(animateStep, 800);
    }

    animateStep();
}

function buildLocalReasoningPath(node) {
    const path = [];
    const visitedNodes = new Set([node.id]);
    let frontier = [node.id];

    for (let step = 0; step < 3; step++) {
        if (frontier.length === 0) break;
        const newNodes = [];
        const stepEdges = [];

        for (const nid of frontier) {
            for (const edge of kgGraphData.edges) {
                const edgeId = edge.id || `${edge.source}-${edge.target}`;
                if (edge.source === nid && !visitedNodes.has(edge.target)) {
                    visitedNodes.add(edge.target);
                    newNodes.push(edge.target);
                    stepEdges.push({ edge: { source: edge.source, target: edge.target, relation_type: edge.type, id: edgeId }, step: step + 1 });
                } else if (edge.target === nid && !visitedNodes.has(edge.source)) {
                    visitedNodes.add(edge.source);
                    newNodes.push(edge.source);
                    stepEdges.push({ edge: { source: edge.source, target: edge.target, relation_type: edge.type, id: edgeId }, step: step + 1 });
                } else if (edge.source === nid || edge.target === nid) {
                    if (!stepEdges.find(se => (se.edge.id || `${se.edge.source}-${se.edge.target}`) === edgeId)) {
                        stepEdges.push({ edge: { source: edge.source, target: edge.target, relation_type: edge.type, id: edgeId }, step: step + 1 });
                    }
                }
            }
        }

        path.push({ step: step + 1, new_nodes: newNodes, edges: stepEdges });
        frontier = newNodes;
    }

    return path;
}

function stopReasoningAnimation() {
    if (kgReasoningAnim) {
        clearTimeout(kgReasoningAnim);
        kgReasoningAnim = null;
    }
    kgReasoningStep = -1;
}

function showReasoningToast(node) {
    showToast(`推理路径追踪: ${node.name || node.id}，双击其他节点追踪新路径`, 'info');
}

async function refreshKGGraph(docKey, options = {}) {
    try {
        if (docKey !== undefined) {
            kgCurrentDocKey = docKey;
        }
        ensureKGCanvas();
        if (!kgCanvas) return;
        resizeKGCanvas();
        if (!isKGCanvasRenderable() || !kgCanvas.width || !kgCanvas.height) {
            scheduleKGGraphRetry(180);
            return;
        }
        const reuseExisting = options.reuse === true && kgGraphData.nodes.length > 0;
        if (!reuseExisting) {
            replaceKGGraphData(buildMarkdownFileGraph());
        }
        syncKGSelectionToCurrentDoc();
        fitKGGraphToCanvas();
        drawKGGraph();
        if (!reuseExisting || options.restartSimulation === true) {
            startKGSimulation();
        }
        if (typeof updateStats === 'function') updateStats();
        if (!reuseExisting && typeof LocalDB !== 'undefined' && LocalDB?.saveKGGraph) {
            LocalDB.saveKGGraph(kgCurrentDocKey || '__global__', kgGraphData)
                .catch(err => console.warn('save KG graph cache failed:', err));
        }
        if (!reuseExisting) hydrateMarkdownFileContentsInBackground();
    } catch (err) {
        console.error('refreshKGGraph error:', err);
        drawKGEmpty();
    }
}

function getMarkdownFileEntries() {
    const sourceFiles = (typeof fileStore !== 'undefined' && fileStore.files) ? fileStore.files : [];
    const result = [];

    function walk(items) {
        for (const item of items || []) {
            if (item.isDirectory) {
                walk(item.children || []);
            } else {
                const ext = (item.name.split('.').pop() || '').toLowerCase();
                if (ext === 'md' || ext === 'markdown') {
                    result.push(item);
                }
            }
        }
    }

    walk(sourceFiles);
    return result;
}

function normalizeFileTitle(name) {
    return (name || '').replace(/\.(md|markdown)$/i, '').trim();
}

function tokenizeFileName(text) {
    const normalized = (text || '')
        .replace(/\.(md|markdown)$/i, '')
        .replace(/[_\-+~()[\]{}#.,，。/\\|:：;；!?！？]/g, ' ')
        .toLowerCase();
    const tokens = new Set();
    const stopWords = new Set([
        'the', 'and', 'for', 'with', 'from', 'this', 'that', 'note', 'notes', 'md',
        '一个', '一种', '这个', '那个', '什么', '如何', '怎么', '以及', '可以', '需要',
        '文件', '文档', '笔记', '内容', '说明', '总结', '整理',
    ]);

    for (const part of normalized.split(/\s+/).filter(Boolean)) {
        if (stopWords.has(part)) continue;
        if (/^[0-9]+$/.test(part)) continue;

        if (/[\u4e00-\u9fff]/.test(part)) {
            if (part.length >= 2 && !stopWords.has(part)) tokens.add(part);
            for (let size = 2; size <= 4; size++) {
                for (let i = 0; i <= part.length - size; i++) {
                    const gram = part.slice(i, i + size);
                    if (!stopWords.has(gram)) tokens.add(gram);
                }
            }
        } else if (part.length >= 3) {
            tokens.add(part);
        }
    }
    return tokens;
}

function stripGraphMarkdownCodeSegments(content) {
    const lines = String(content || '').split(/\r?\n/);
    const kept = [];
    let inFence = false;
    let fenceChar = '';
    let fenceSize = 0;

    for (const line of lines) {
        const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
        if (fence) {
            const marker = fence[1];
            const markerChar = marker[0];
            if (!inFence) {
                inFence = true;
                fenceChar = markerChar;
                fenceSize = marker.length;
            } else if (markerChar === fenceChar && marker.length >= fenceSize) {
                inFence = false;
                fenceChar = '';
                fenceSize = 0;
            }
            kept.push('');
            continue;
        }
        if (inFence || /^\s{4,}\S/.test(line)) {
            kept.push('');
            continue;
        }
        kept.push(line.replace(/`[^`]*`/g, ''));
    }

    return kept.join('\n');
}

function extractMarkdownHeadings(content) {
    const headings = [];
    const lines = stripGraphMarkdownCodeSegments(content || '').split(/\r?\n/);
    for (const line of lines.slice(0, 160)) {
        const m = line.match(/^\s{0,3}(#{1,4})[ \t]+(.{2,100})$/);
        if (m) headings.push(m[2].replace(/[ \t]+#+[ \t]*$/, '').trim());
        if (headings.length >= 10) break;
    }
    return headings;
}

function extractGraphTerms(file) {
    const headings = extractMarkdownHeadings(file.content || '');
    const textParts = [
        file.name || '',
        file.path || '',
        ...headings,
        String(file.content || '').slice(0, 900),
    ];
    const terms = tokenizeFileName(textParts.join(' '));
    const weighted = new Map();
    for (const term of terms) {
        let weight = 1;
        if ((file.name || '').includes(term)) weight += 1.8;
        if ((file.path || '').includes(term)) weight += 1.1;
        if (headings.some(h => h.includes(term))) weight += 1.6;
        weighted.set(term, weight);
    }
    return weighted;
}

function weightedTermSimilarity(aTerms, bTerms) {
    if (!aTerms.size || !bTerms.size) return 0;
    let inter = 0;
    let aSum = 0;
    let bSum = 0;
    for (const weight of aTerms.values()) aSum += weight * weight;
    for (const weight of bTerms.values()) bSum += weight * weight;
    for (const [term, weight] of aTerms.entries()) {
        if (bTerms.has(term)) inter += weight * bTerms.get(term);
    }
    return inter / Math.sqrt(Math.max(1, aSum) * Math.max(1, bSum));
}

function getFileParentPath(file) {
    const path = file.path || file.name || '';
    const idx = path.lastIndexOf('/');
    return idx > -1 ? path.slice(0, idx) : '';
}

function folderAffinity(a, b) {
    const aDirs = (a.path || '').split('/').slice(0, -1);
    const bDirs = (b.path || '').split('/').slice(0, -1);
    if (!aDirs.length || !bDirs.length) return 0;
    let common = 0;
    for (let i = 0; i < Math.min(aDirs.length, bDirs.length); i++) {
        if (aDirs[i] !== bDirs[i]) break;
        common++;
    }
    const sameParent = getFileParentPath(a) && getFileParentPath(a) === getFileParentPath(b);
    return (sameParent ? 0.42 : 0) + Math.min(0.28, common * 0.07);
}

function relationReason(type, score) {
    if (type === 'file_link') return '显式 Markdown 链接';
    if (type === 'folder_neighbor') return '同一文件夹/目录结构相邻';
    if (type === 'theme_overlap') return `共享主题 ${Math.round(score * 100)}%`;
    return `相关度 ${Math.round(score * 100)}%`;
}

function fileNameSimilarity(a, b) {
    const nameScore = weightedTermSimilarity(
        extractGraphTerms({ ...a, content: '' }),
        extractGraphTerms({ ...b, content: '' })
    );
    return Math.min(1, nameScore * 0.82 + folderAffinity(a, b));
}

async function hydrateMarkdownFileContents() {
    const files = getMarkdownFileEntries();
    for (const file of files) {
        if (typeof file.content === 'string' || file._kgContentLoaded) continue;
        try {
            let content = '';
            if (file.source === 'server' && typeof readServerFileResource === 'function') {
                const data = await readServerFileResource(file.path || file.name);
                content = data.content || '';
            } else if (file.handle && typeof readFileContent === 'function') {
                const readResult = await readFileContent(file);
                content = typeof readResult === 'string' ? readResult : (readResult?.markdown || '');
            }
            file.content = content;
            file._kgContentLoaded = true;
        } catch (err) {
            file._kgContentLoaded = true;
            console.warn('hydrate markdown content failed:', file.path || file.name, err);
        }
    }
}

function hydrateMarkdownFileContentsInBackground() {
    if (kgHydrationRunning) return;
    // Server libraries can contain hundreds of large notes. Fetching every note
    // on page load competes with the file the user is opening and can freeze a
    // small VPS. Server documents join the content graph as they are opened.
    const files = getMarkdownFileEntries().filter(file =>
        file.source !== 'server'
        && typeof file.content !== 'string'
        && !file._kgContentLoaded
    );
    if (!files.length) return;

    const version = ++kgHydrationVersion;
    kgHydrationRunning = true;

    (async () => {
        const maxToLoad = Math.min(files.length, 80);
        for (let i = 0; i < maxToLoad; i++) {
            const file = files[i];
            if (version !== kgHydrationVersion) break;
            try {
                let content = '';
                if (file.source === 'server' && typeof readServerFileResource === 'function') {
                    const data = await readServerFileResource(file.path || file.name);
                    content = data.content || '';
                } else if (file.handle && typeof readFileContent === 'function') {
                    const readResult = await readFileContent(file);
                    content = typeof readResult === 'string' ? readResult : (readResult?.markdown || '');
                }
                file.content = content;
            } catch (err) {
                console.warn('hydrate markdown content failed:', file.path || file.name, err);
            } finally {
                file._kgContentLoaded = true;
            }

            if ((i + 1) % 8 === 0) {
                await new Promise(resolve => setTimeout(resolve, 0));
            }
        }

        if (version === kgHydrationVersion && typeof fileStore !== 'undefined') {
            replaceKGGraphData(buildMarkdownFileGraph());
            syncKGSelectionToCurrentDoc();
            fitKGGraphToCanvas();
            startKGSimulation();
            drawKGGraph();
            if (typeof renderKGBacklinks === 'function') renderKGBacklinks();
            LocalDB.saveKGGraph(kgCurrentDocKey || '__global__', kgGraphData).catch(() => {});
        }
    })().finally(() => {
        kgHydrationRunning = false;
    });
}

function normalizeGraphLookupKey(text) {
    return (text || '')
        .replace(/\\/g, '/')
        .replace(/^.*\//, '')
        .replace(/\.(md|markdown)$/i, '')
        .replace(/[#?].*$/, '')
        .trim()
        .toLowerCase();
}

function isGraphExternalHref(href) {
    return /^(https?:|mailto:|tel:|data:|blob:|javascript:)/i.test(String(href || ''))
        || String(href || '').startsWith('#');
}

function isGraphReferenceBoundary(ch) {
    return !ch || /[\s,，.。;；:：!?！？()[\]{}<>《》'"“”‘’、]/.test(ch);
}

function hasGraphMarkedReference(text, key) {
    const lower = String(text || '').toLowerCase();
    const normalizedKey = String(key || '').toLowerCase();
    if (!normalizedKey || normalizedKey.length < 2) return false;

    let pos = lower.indexOf(`@${normalizedKey}`);
    while (pos >= 0) {
        if (isGraphReferenceBoundary(lower[pos - 1]) && isGraphReferenceBoundary(lower[pos + normalizedKey.length + 1])) {
            return true;
        }
        pos = lower.indexOf(`@${normalizedKey}`, pos + 1);
    }

    for (const marker of ['@', '$']) {
        const needle = `${normalizedKey}${marker}`;
        pos = lower.indexOf(needle);
        while (pos >= 0) {
            if (isGraphReferenceBoundary(lower[pos + needle.length])) return true;
            pos = lower.indexOf(needle, pos + 1);
        }
    }

    return false;
}

function extractMarkdownOutgoingLinks(content, knownKeys = []) {
    const links = [];
    const text = stripGraphMarkdownCodeSegments(content || '');
    const wikiRe = /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]/g;
    const mdRe = /(!?)\[[^\]]+\]\(([^)]+)\)/gi;
    const prefixRe = /(^|[\s([（【《,，。；;:：])@([^\s@#$[\]()<>，。！？!?；;:：'"“”‘’]{2,80})/g;
    const suffixRe = /([^\s@#$[\]()<>，。！？!?；;:：'"“”‘’]{2,80})[@$](?=$|[\s)）】》,，。；;:：!?！？])/g;
    let match;
    while ((match = wikiRe.exec(text))) links.push(match[1]);
    while ((match = mdRe.exec(text))) {
        if (match[1]) continue;
        const href = match[2].trim();
        if (!href || isGraphExternalHref(href)) continue;
        try {
            links.push(decodeURIComponent(href));
        } catch {
            links.push(href);
        }
    }

    while ((match = prefixRe.exec(text))) links.push(match[2]);
    while ((match = suffixRe.exec(text))) links.push(match[1]);

    const keys = Array.from(knownKeys || [])
        .map(normalizeGraphLookupKey)
        .filter(key => key && key.length >= 2 && key.length <= 80)
        .sort((a, b) => b.length - a.length);
    for (const key of keys) {
        if (hasGraphMarkedReference(text, key)) links.push(key);
    }

    return links.map(normalizeGraphLookupKey).filter(Boolean);
}

function fileContentSimilarity(a, b) {
    const aText = `${a.name} ${a.path || ''} ${(a.content || '').slice(0, 1400)}`;
    const bText = `${b.name} ${b.path || ''} ${(b.content || '').slice(0, 1400)}`;
    const aTokens = tokenizeFileName(aText);
    const bTokens = tokenizeFileName(bText);
    if (!aTokens.size || !bTokens.size) return 0;
    let inter = 0;
    for (const t of aTokens) if (bTokens.has(t)) inter++;
    const score = inter / Math.sqrt(aTokens.size * bTokens.size);
    return Math.min(1, score);
}

function stabilizeMarkdownFileGraphLayout(nodes, edges, selectedPath, canvasW, canvasH, previousNodeById) {
    const centerX = canvasW / 2;
    const centerY = canvasH / 2;
    const scoped = shouldShowKGCurrentScope();
    const currentNode = scoped ? nodes.find(node => isSameKGDocPath(node.id, selectedPath)) : null;
    const currentId = currentNode?.id || '';
    const neighborIds = new Set();
    const adjacency = new Map(nodes.map(node => [node.id, new Set()]));
    for (const edge of edges) {
        adjacency.get(edge.source)?.add(edge.target);
        adjacency.get(edge.target)?.add(edge.source);
        if (currentId && edge.source === currentId) neighborIds.add(edge.target);
        if (currentId && edge.target === currentId) neighborIds.add(edge.source);
    }

    const setPosition = (node, x, y) => {
        node.layoutX = x;
        node.layoutY = y;
        if (!node._userPosition) {
            node.x = x;
            node.y = y;
        }
    };
    if (currentNode) setPosition(currentNode, centerX, centerY);

    const ringNodes = [];
    if (currentId) {
        const firstRing = nodes
            .filter(node => neighborIds.has(node.id))
            .sort((a, b) => (b.connections || 0) - (a.connections || 0) || a.id.localeCompare(b.id));
        const orbit = Math.min(canvasW, canvasH) * 0.16;
        firstRing.forEach((node, i) => {
            const angle = (Math.PI * 2 * i) / Math.max(1, firstRing.length) - Math.PI / 2;
            setPosition(node, centerX + Math.cos(angle) * orbit, centerY + Math.sin(angle) * orbit);
        });
        const secondRingIds = new Set();
        firstRing.forEach(node => (adjacency.get(node.id) || []).forEach(id => {
            if (id !== currentId && !neighborIds.has(id)) secondRingIds.add(id);
        }));
        ringNodes.push(...nodes.filter(node => secondRingIds.has(node.id)));
        ringNodes.push(...nodes.filter(node => node.id !== currentId && !neighborIds.has(node.id) && !secondRingIds.has(node.id)));
    } else {
        ringNodes.push(...nodes);
    }

    ringNodes.sort((a, b) => (b.connections || 0) - (a.connections || 0) || a.id.localeCompare(b.id));
    const ringCapacity = Math.max(8, Math.min(28, Math.ceil(Math.sqrt(Math.max(1, ringNodes.length)) * 3.2)));
    const firstRingNumber = currentId ? 2 : 1;
    for (let index = 0; index < ringNodes.length; index++) {
        const ringOffset = Math.floor(index / ringCapacity);
        const ring = firstRingNumber + ringOffset;
        const ringStart = ringOffset * ringCapacity;
        const ringCount = Math.min(ringCapacity, ringNodes.length - ringStart);
        const angle = (Math.PI * 2 * (index - ringStart)) / Math.max(1, ringCount) - Math.PI / 2;
        const radius = Math.min(canvasW, canvasH) * (currentId ? 0.24 : 0.20)
            + ringOffset * Math.max(42, Math.min(canvasW, canvasH) * 0.05);
        setPosition(ringNodes[index], centerX + Math.cos(angle) * radius, centerY + Math.sin(angle) * radius);
    }
}

function buildMarkdownFileGraph() {
    const files = getMarkdownFileEntries();
    const canvasW = Math.max(320, kgCanvas ? kgCanvas.width / (kgScale || 1) : 320);
    const canvasH = Math.max(220, kgCanvas ? kgCanvas.height / (kgScale || 1) : 220);
    const selectedPath = getKGCurrentDocPath();
    const previousNodeById = new Map((kgGraphData.nodes || []).map(n => [n.id, n]));

    const nodes = files.map((file, i) => {
        const id = file.path || file.name;
        const old = previousNodeById.get(id);
        const angle = (Math.PI * 2 * i) / Math.max(files.length, 1);
        const orbit = Math.min(canvasW, canvasH) * (files.length > 12 ? 0.22 : 0.16);
        const seed = hashKGString(id);
        const jitter = ((seed % 17) - 8) * 0.8;
        const isSelected = selectedPath && (isSameKGDocPath(file.path, selectedPath) || isSameKGDocPath(file.name, selectedPath));
        return {
            id,
            name: normalizeFileTitle(file.name),
            label: normalizeFileTitle(file.name),
            type: 'document',
            node_type: 'document',
            connections: 0,
            x: old?.x ?? (canvasW / 2 + Math.cos(angle) * (orbit + jitter)),
            y: old?.y ?? (canvasH / 2 + Math.sin(angle) * (orbit + jitter)),
            layoutX: old?.layoutX,
            layoutY: old?.layoutY,
            _userPosition: old?._userPosition === true,
            vx: old?.vx || 0,
            vy: old?.vy || 0,
            radius: isSelected ? 5.5 : 3.2,
            isCurrent: isSelected,
            properties: {
                path: id,
                filename: file.name,
                isCurrent: isSelected,
                source: 'markdown_file_graph',
            },
        };
    });

    const nodeById = new Map(nodes.map(n => [n.id, n]));
    const termById = new Map(files.map(file => [file.path || file.name, extractGraphTerms(file)]));
    const nameTermById = new Map(files.map(file => [
        file.path || file.name,
        extractGraphTerms({ ...file, content: '' }),
    ]));
    const titleToIds = new Map();
    for (const file of files) {
        const id = file.path || file.name;
        const keys = new Set([
            normalizeGraphLookupKey(file.name),
            normalizeGraphLookupKey(file.path || file.name),
            normalizeGraphLookupKey(normalizeFileTitle(file.name)),
        ]);
        for (const key of keys) {
            if (!key) continue;
            if (!titleToIds.has(key)) titleToIds.set(key, []);
            titleToIds.get(key).push(id);
        }
    }

    const edgeMap = new Map();
    function addEdge(source, target, type, score, reason) {
        if (!source || !target || source === target || !nodeById.has(source) || !nodeById.has(target)) return;
        const key = `${source}__${target}`;
        const reverseKey = `${target}__${source}`;
        const existing = edgeMap.get(key) || edgeMap.get(reverseKey);
        if (existing && existing.score >= score) return;
        if (existing) edgeMap.delete(existing.id);
        edgeMap.set(key, {
            id: key,
            source,
            target,
            type,
            relation_type: type,
            score,
            style: 'solid',
            edgeColor: type === 'file_link' ? '#b7bcc6' : '#d0d4dc',
            properties: { reason },
        });
    }

    for (const file of files) {
        const sourceId = file.path || file.name;
        for (const targetKey of extractMarkdownOutgoingLinks(file.content || '', titleToIds.keys())) {
            const targets = titleToIds.get(targetKey) || [];
            for (const targetId of targets) {
                addEdge(sourceId, targetId, 'file_link', 1, 'Markdown internal link');
            }
        }
    }

    const explicitEdgeCount = Array.from(edgeMap.values()).filter(e => e.type === 'file_link').length;
    const candidates = [];
    for (let i = 0; i < files.length; i++) {
        for (let j = i + 1; j < files.length; j++) {
            const source = files[i].path || files[i].name;
            const target = files[j].path || files[j].name;
            const structureScore = folderAffinity(files[i], files[j]);
            const titleScore = Math.min(
                1,
                weightedTermSimilarity(nameTermById.get(source) || new Map(), nameTermById.get(target) || new Map()) * 0.82
                    + structureScore,
            );
            const topicScore = weightedTermSimilarity(termById.get(source) || new Map(), termById.get(target) || new Map());
            const score = Math.min(1, Math.max(titleScore, topicScore * 0.92, structureScore));
            const type = structureScore >= 0.42 && topicScore < 0.22 ? 'folder_neighbor' : (topicScore >= 0.2 ? 'theme_overlap' : 'file_similarity');
            const threshold = type === 'folder_neighbor' ? 0.42 : 0.2;
            if (score >= threshold) {
                candidates.push({
                    source,
                    target,
                    type,
                    score,
                });
            }
        }
    }

    const degree = new Map(nodes.map(n => [n.id, 0]));
    for (const edge of edgeMap.values()) {
        degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
        degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
    }

    const fallbackLimit = explicitEdgeCount > 0 ? 3 : 5;
    const sameFolderLimit = 2;
    const folderEdgeCount = new Map();
    for (const edge of candidates.sort((a, b) => b.score - a.score)) {
        const sDegree = degree.get(edge.source) || 0;
        const tDegree = degree.get(edge.target) || 0;
        if (sDegree >= fallbackLimit && tDegree >= fallbackLimit) continue;
        if (edge.type === 'folder_neighbor') {
            const folderKey = [getFileParentPath({ path: edge.source }), getFileParentPath({ path: edge.target })].sort().join('__');
            const count = folderEdgeCount.get(folderKey) || 0;
            if (count >= sameFolderLimit) continue;
            folderEdgeCount.set(folderKey, count + 1);
        }
        addEdge(edge.source, edge.target, edge.type, edge.score, relationReason(edge.type, edge.score));
        degree.set(edge.source, sDegree + 1);
        degree.set(edge.target, tDegree + 1);
    }

    if (edgeMap.size === 0 && files.length > 1) {
        const byParent = new Map();
        for (const file of files) {
            const parent = getFileParentPath(file) || '__root__';
            if (!byParent.has(parent)) byParent.set(parent, []);
            byParent.get(parent).push(file);
        }
        for (const group of byParent.values()) {
            for (let i = 0; i < group.length - 1; i++) {
                addEdge(
                    group[i].path || group[i].name,
                    group[i + 1].path || group[i + 1].name,
                    'folder_neighbor',
                    0.42,
                    relationReason('folder_neighbor', 0.42)
                );
            }
        }
    }

    if (edgeMap.size === 0 && files.length > 1) {
        for (let i = 0; i < files.length - 1; i++) {
            addEdge(
                files[i].path || files[i].name,
                files[i + 1].path || files[i + 1].name,
                'file_similarity',
                0.18,
                '文件资源相邻'
            );
        }
    }

    const edges = Array.from(edgeMap.values());
    for (const edge of edges) {
        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        if (source) source.connections++;
        if (target) target.connections++;
    }

    const maxDegree = Math.max(1, ...nodes.map(n => n.connections || 0));
    for (const node of nodes) {
        const isSelected = selectedPath && isSameKGDocPath(node.id, selectedPath);
        const degreeRatio = (node.connections || 0) / maxDegree;
        const smallGraphBoost = nodes.length <= 12 ? 2.2 : (nodes.length <= 30 ? 1.1 : 0);
        const radius = 3.4 + smallGraphBoost + Math.pow(degreeRatio, 1.35) * 5.6 + (isSelected ? 2.2 : 0);
        node.radius = Math.max(4.6, Math.min(10.5, radius));
        node.degreeRatio = degreeRatio;
        node.isCurrent = isSelected;
        node.properties.isCurrent = isSelected;
    }

    stabilizeMarkdownFileGraphLayout(nodes, edges, selectedPath, canvasW, canvasH, previousNodeById);

    return { nodes, edges };
}
async function loadDocGraph(docKey) {
    if (!docKey) {
        kgCurrentDocKey = null;
        await refreshKGGraph();
        return;
    }
    kgCurrentDocKey = docKey;
    const select = document.getElementById('kg-doc-select');
    if (select) select.value = docKey;
    if (typeof showToast === 'function') {
        showToast(`正在加载 ${docKey} 的知识图谱…`, 'info');
    }
    await refreshKGGraph();
}

async function openKGGraphPage(mode = 'global') {
    kgGraphViewMode = mode === 'current' ? 'current' : 'global';
    kgVisibleGraphCacheKey = '';
    kgVisibleGraphCacheValue = null;
    if (kgFullGraphModal) {
        kgFullGraphModal.style.display = 'flex';
        updateKGGraphModalText();
        setKGCanvas('kg-full-canvas');
        drawKGGraph();
        requestAnimationFrame(() => {
            refreshKGGraph(undefined, { force: true })
                .then(() => {
                    resizeKGCanvas();
                    fitKGGraphToCanvas(true);
                    drawKGGraph();
                })
                .catch(err => console.warn('refresh full KG graph failed:', err));
        });
        return;
    }

    const modal = document.createElement('div');
    modal.className = 'kg-graph-modal';
    modal.id = 'kg-graph-modal';
    modal.innerHTML = `
        <div class="kg-graph-modal-toolbar">
            <div>
                <div class="kg-graph-modal-title" id="kg-graph-modal-title">INTERACTIVE GRAPH</div>
                <div class="kg-graph-modal-subtitle" id="kg-graph-modal-subtitle">点击节点打开文档 · 滚轮缩放 · 拖动画布浏览</div>
            </div>
            <div class="kg-graph-modal-actions">
                <button class="icon-btn" onclick="openKGGraphPage('current')" title="当前文档图谱">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 7.5 10 10"/><path d="M17 7.5 14 10"/><path d="M7 16.5 10 14"/><path d="M17 16.5 14 14"/></svg>
                </button>
                <button class="icon-btn" onclick="openKGGraphPage('global')" title="全局图谱">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8"/><path d="M3.6 15h16.8"/><path d="M12 3a14 14 0 0 1 0 18"/><path d="M12 3a14 14 0 0 0 0 18"/></svg>
                </button>
                <button class="icon-btn" onclick="kgZoomOut()" title="缩小">−</button>
                <button class="icon-btn" onclick="kgResetView()" title="重置">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
                </button>
                <button class="icon-btn" onclick="kgZoomIn()" title="放大">+</button>
                <button class="icon-btn" onclick="closeKGGraphPage()" title="关闭">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
        </div>
        <canvas id="kg-full-canvas"></canvas>
        <div class="kg-graph-powered">Knowledge Hub Graph</div>`;
    document.body.appendChild(modal);
    kgFullGraphModal = modal;
    updateKGGraphModalText();
    setKGCanvas('kg-full-canvas');
    drawKGGraph();
    requestAnimationFrame(() => {
        refreshKGGraph(undefined, { force: true })
            .then(() => {
                resizeKGCanvas();
                fitKGGraphToCanvas(true);
                drawKGGraph();
            })
            .catch(err => console.warn('refresh full KG graph failed:', err));
    });
}

function updateKGGraphModalText() {
    const title = document.getElementById('kg-graph-modal-title');
    const subtitle = document.getElementById('kg-graph-modal-subtitle');
    if (title) title.textContent = kgGraphViewMode === 'current' ? 'LOCAL GRAPH' : 'GLOBAL GRAPH';
    if (subtitle) {
        subtitle.textContent = kgGraphViewMode === 'current'
            ? '当前文档邻域 · 点击节点打开文档 · 拖动/缩放浏览'
            : '全局文档网络 · 点击节点打开文档 · 悬停查看关联群组';
    }
    document.querySelectorAll('.kg-graph-modal-actions [onclick*="openKGGraphPage"]').forEach(btn => {
        const isCurrentBtn = btn.getAttribute('onclick')?.includes("'current'");
        btn.classList.toggle('active', isCurrentBtn ? kgGraphViewMode === 'current' : kgGraphViewMode === 'global');
    });
}

function closeKGGraphPage() {
    if (kgFullGraphModal) {
        kgFullGraphModal.style.display = 'none';
    }
    setKGCanvas('kg-mini-canvas');
    requestAnimationFrame(() => {
        refreshKGGraph(undefined, { force: true })
            .then(() => {
                resizeKGCanvas();
                drawKGGraph();
            })
            .catch(err => console.warn('refresh mini KG graph failed:', err));
    });
}

function clearDocGraphView() {
    kgCurrentDocKey = null;
    replaceKGGraphData({ nodes: [], edges: [] });
    if (kgCtx && kgCanvas) {
        drawKGGraph();
    }
}

function startKGSimulation() {
    kgSimAlpha = 1;
}

function startKGAnimLoop() {
    if (kgAnimFrame) cancelAnimationFrame(kgAnimFrame);
    kgLastAnimTime = 0;

    function loop(now = performance.now()) {
        const hoverAnimating = Math.abs(kgHoverFade - kgHoverFadeTarget) > 0.001;
        const simActive = kgSimAlpha > 0.0015 || kgDragNode || kgIsDragging || kgIsPanning;
        const zoomActive = kgZoomAnimating;
        const fastFrame = hoverAnimating || simActive || zoomActive;
        const frameMs = fastFrame ? 16 : 120;
        if (!kgLastAnimTime || now - kgLastAnimTime >= frameMs) {
            const dt = kgLastAnimTime ? Math.min(48, now - kgLastAnimTime) : 16;
            kgLastAnimTime = now;
            kgPulsePhase += 0.0012 * dt;
            const stillHoverAnimating = updateKGHoverFade();
            const stillZoomAnimating = updateKGSmoothZoom();
            if (simActive) simulateKGStep();
            if (simActive || stillHoverAnimating || stillZoomAnimating || fastFrame || now % 500 < frameMs) {
                drawKGGraph();
            }
        }
        kgAnimFrame = requestAnimationFrame(loop);
    }

    loop(performance.now());
}

function buildKGSpatialGrid(nodes, cellSize) {
    const grid = new Map();
    for (const node of nodes) {
        const cx = Math.floor(node.x / cellSize);
        const cy = Math.floor(node.y / cellSize);
        const key = `${cx},${cy}`;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(node);
    }
    return { grid, cellSize };
}

function getKGSpatialNeighbors(spatial, node) {
    if (!spatial) return null;
    const { grid, cellSize } = spatial;
    const cx = Math.floor(node.x / cellSize);
    const cy = Math.floor(node.y / cellSize);
    const result = [];
    for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
            const bucket = grid.get(`${cx + dx},${cy + dy}`);
            if (bucket) result.push(...bucket);
        }
    }
    return result;
}

function simulateKGStep() {
    const nodes = kgGraphData.nodes;
    const edges = kgGraphData.edges;
    if (!nodes.length) return;

    const centerX = kgCanvas.width / 2 / kgScale;
    const centerY = kgCanvas.height / 2 / kgScale;

    const isDragging = kgDragNode !== null && kgIsDragging;
    const isFileGraph = nodes.some(isMarkdownFileNode);

    if (isDragging) {
        kgSimAlpha = Math.max(kgSimAlpha, isFileGraph ? 0.36 : 0.6);
    } else {
        const alphaDecay = isFileGraph ? 0.0075 : 0.004;
        const alphaMin = 0.001;
        if (kgSimAlpha > alphaMin) {
            kgSimAlpha = Math.max(alphaMin, kgSimAlpha - alphaDecay);
        }
    }

    const alpha = kgSimAlpha;

    const nodeMap = new Map();
    for (const n of nodes) nodeMap.set(n.id, n);

    const adjacency = new Map();
    for (const n of nodes) adjacency.set(n.id, []);
    for (const edge of edges) {
        const s = nodeMap.get(edge.source);
        const t = nodeMap.get(edge.target);
        if (s && t) {
            adjacency.get(edge.source).push({ node: t, edge });
            adjacency.get(edge.target).push({ node: s, edge });
        }
    }

    const spatial = isFileGraph && nodes.length > 24
        ? buildKGSpatialGrid(nodes, Math.max(96, 130 / Math.max(0.6, kgScale)))
        : null;

    for (const node of nodes) {
        if (node === kgDragNode) {
            if (kgDragTarget) {
                const follow = Math.min(0.46, 0.18 + alpha * 0.12);
                node.x += (kgDragTarget.x - node.x) * follow;
                node.y += (kgDragTarget.y - node.y) * follow;
                node.vx = (kgDragTarget.x - node.x) * 0.08;
                node.vy = (kgDragTarget.y - node.y) * 0.08;
                node.layoutX = kgDragTarget.x;
                node.layoutY = kgDragTarget.y;
            }
            continue;
        }

        let fx = (centerX - node.x) * (isFileGraph ? 0.0022 : 0.002) * alpha;
        let fy = (centerY - node.y) * (isFileGraph ? 0.0022 : 0.002) * alpha;
        if (isFileGraph && Number.isFinite(node.layoutX) && Number.isFinite(node.layoutY)) {
            fx += (node.layoutX - node.x) * 0.036;
            fy += (node.layoutY - node.y) * 0.036;
        }
        if (isFileGraph && kgActiveCanvasId === 'kg-mini-canvas' && isCurrentKGDocNode(node)) {
            fx += (centerX - node.x) * 0.012 * alpha;
            fy += (centerY - node.y) * 0.012 * alpha;
        }

        const repulsionNodes = spatial ? getKGSpatialNeighbors(spatial, node) : nodes;
        for (const other of repulsionNodes) {
            if (node === other) continue;
            const dx = node.x - other.x;
            const dy = node.y - other.y;
            const distSq = dx * dx + dy * dy;
            const dist = Math.sqrt(distSq) || 1;
            const minDist = (node.radius || 12) + (other.radius || 12) + (isFileGraph ? 12 : 30);

            const repulsion = (isFileGraph ? 360 : 2000) / (distSq + 100);
            fx += (dx / dist) * repulsion * alpha;
            fy += (dy / dist) * repulsion * alpha;

            if (dist < minDist) {
                const overlap = (minDist - dist);
                const pushForce = overlap * 0.5;
                fx += (dx / dist) * pushForce;
                fy += (dy / dist) * pushForce;

                if (other !== kgDragNode) {
                    other.vx -= (dx / dist) * pushForce * 0.25;
                    other.vy -= (dy / dist) * pushForce * 0.25;
                }
            }
        }

        const damping = isFileGraph ? 0.58 : 0.6;
        node.vx = (node.vx + fx) * damping;
        node.vy = (node.vy + fy) * damping;
    }

    const springStrength = isFileGraph ? (isDragging ? 0.0065 : 0.0018) : (isDragging ? 0.012 : 0.003);
    const springAlpha = isDragging ? 1.0 : alpha;

    for (const edge of edges) {
        const source = nodeMap.get(edge.source);
        const target = nodeMap.get(edge.target);
        if (!source || !target) continue;

        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const sourceR = source.radius || 12;
        const targetR = target.radius || 12;
        const idealDist = sourceR + targetR + (isFileGraph ? 52 : 100);
        const force = (dist - idealDist) * springStrength * springAlpha;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;

        if (source !== kgDragNode) { source.vx += fx; source.vy += fy; }
        if (target !== kgDragNode) { target.vx -= fx; target.vy -= fy; }
    }

    if (isDragging && kgDragNode) {
        const dragNeighbors = adjacency.get(kgDragNode.id) || [];
        for (const { node: neighbor } of dragNeighbors) {
            const dx = kgDragNode.x - neighbor.x;
            const dy = kgDragNode.y - neighbor.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const followForce = 0.08;
            neighbor.vx += (dx / dist) * followForce * dist * 0.01;
            neighbor.vy += (dy / dist) * followForce * dist * 0.01;
        }
    }

    for (const node of nodes) {
        if (node === kgDragNode) continue;

        const speed = Math.sqrt(node.vx * node.vx + node.vy * node.vy);
        const maxSpeed = isFileGraph ? 4.0 : 12;
        if (speed > maxSpeed) {
            node.vx = (node.vx / speed) * maxSpeed;
            node.vy = (node.vy / speed) * maxSpeed;
        }

        node.x += node.vx;
        node.y += node.vy;
    }
}

function aggregateNodes(nodes, cellSize) {
    const cellMap = new Map();
    const aggregated = [];
    const original = [];

    for (const node of nodes) {
        const cx = Math.floor(node.x / cellSize);
        const cy = Math.floor(node.y / cellSize);
        const key = `${cx}_${cy}_${node.type || 'default'}`;

        if (!cellMap.has(key)) {
            const aggNode = {
                id: `__agg_${key}`,
                name: '',
                type: node.type || 'default',
                x: 0,
                y: 0,
                radius: 0,
                connections: 0,
                _isAggregated: true,
                _count: 0,
                _children: [],
            };
            cellMap.set(key, aggNode);
            aggregated.push(aggNode);
        }

        const aggNode = cellMap.get(key);
        aggNode._children.push(node);
        aggNode._count++;
        aggNode.x += node.x;
        aggNode.y += node.y;
        aggNode.connections += node.connections || 0;
        original.push({ original: node, aggregated: aggNode });
    }

    for (const agg of aggregated) {
        agg.x /= agg._count;
        agg.y /= agg._count;
        agg.radius = Math.max(10, Math.min(35, 10 + agg._count * 4));
        agg.name = agg._count === 1 ? agg._children[0].name : `${agg._count}个${agg.type}`;
    }

    return { aggregated, original };
}

function getThemeColors() {
    const style = getComputedStyle(document.documentElement);
    return {
        bgBase: style.getPropertyValue('--bg-base').trim() || '#0c0e14',
        bgSurface: style.getPropertyValue('--bg-surface').trim() || '#131620',
        textPrimary: style.getPropertyValue('--text-primary').trim() || '#e8eaf0',
        textSecondary: style.getPropertyValue('--text-secondary').trim() || '#8b8fa8',
        textMuted: style.getPropertyValue('--text-muted').trim() || '#5a5e78',
        border: style.getPropertyValue('--border').trim() || '#1e2235',
        primary: style.getPropertyValue('--primary').trim() || '#6366f1',
    };
}

function truncateKGGraphLabel(label, maxLength = 24) {
    const text = String(label || '').trim();
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 1)}…`;
}

function getKGFileNodeRadius(node, isFullGraph, maxConnections, isCurrent = false) {
    const ratio = Math.max(0, Math.min(1, (node.connections || 0) / Math.max(1, maxConnections)));
    const fullRadius = 3.6 + Math.pow(ratio, 0.72) * 8.6;
    const miniRadius = 2.8 + Math.pow(ratio, 0.78) * 5.6;
    const zoomCompensation = Math.max(1, Math.sqrt(Math.max(0.2, kgScale)));
    return ((isFullGraph ? fullRadius : miniRadius) + (isCurrent ? 1.5 : 0)) / zoomCompensation;
}

function createKGLabelPlacementGrid(cellSize = 44) {
    const cells = new Map();
    const keysFor = (box) => {
        const keys = [];
        const minX = Math.floor(box.left / cellSize);
        const maxX = Math.floor(box.right / cellSize);
        const minY = Math.floor(box.top / cellSize);
        const maxY = Math.floor(box.bottom / cellSize);
        for (let x = minX; x <= maxX; x++) {
            for (let y = minY; y <= maxY; y++) keys.push(`${x}:${y}`);
        }
        return keys;
    };
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    return {
        collides(box) {
            for (const key of keysFor(box)) {
                for (const placed of cells.get(key) || []) {
                    if (overlaps(box, placed)) return true;
                }
            }
            return false;
        },
        add(box) {
            for (const key of keysFor(box)) {
                if (!cells.has(key)) cells.set(key, []);
                cells.get(key).push(box);
            }
        },
    };
}

function getKGFileLabelCandidates(node, radius, textWidth, lineHeight) {
    const gap = 5;
    const half = textWidth / 2;
    return [
        {
            x: node.x + radius + gap, y: node.y, align: 'left', baseline: 'middle',
            box: { left: node.x + radius + gap, right: node.x + radius + gap + textWidth, top: node.y - lineHeight / 2, bottom: node.y + lineHeight / 2 },
        },
        {
            x: node.x, y: node.y + radius + 4, align: 'center', baseline: 'top',
            box: { left: node.x - half, right: node.x + half, top: node.y + radius + 4, bottom: node.y + radius + 4 + lineHeight },
        },
        {
            x: node.x - radius - gap, y: node.y, align: 'right', baseline: 'middle',
            box: { left: node.x - radius - gap - textWidth, right: node.x - radius - gap, top: node.y - lineHeight / 2, bottom: node.y + lineHeight / 2 },
        },
        {
            x: node.x, y: node.y - radius - 4, align: 'center', baseline: 'bottom',
            box: { left: node.x - half, right: node.x + half, top: node.y - radius - 4 - lineHeight, bottom: node.y - radius - 4 },
        },
    ];
}

function drawKGFileGraph(ctx, w, h) {
    if (!kgGraphData.nodes.length) {
        kgLastRenderedNodeIds = new Set();
        drawKGEmpty();
        return;
    }

    const { nodes: renderNodes, edges: renderEdges } = getKGVisibleFileGraph(kgGraphData.nodes, kgGraphData.edges);
    const nodeMap = new Map(renderNodes.map(node => [node.id, node]));
    kgLastRenderedNodeIds = new Set(renderNodes.map(node => node.id));
    const isFullGraph = kgActiveCanvasId === 'kg-full-canvas';
    const currentPath = getKGCurrentDocPath();
    const currentNode = renderNodes.find(node => isSameKGDocPath(getKGNodeDocPath(node), currentPath));
    const currentId = currentNode?.id || '';
    const maxConnections = Math.max(1, ...renderNodes.map(node => node.connections || 0));

    const hoverT = easeKGHover(kgHoverFade);
    const hasHoverFocus = hoverT > 0.02 && kgHoverFrameNodeId && kgLastRenderedNodeIds.has(kgHoverFrameNodeId) && !kgDragNode && !kgIsPanning;
    const activeNodeIds = hasHoverFocus ? kgHoverFrameNodeIds : new Set();
    const activeEdgeIds = hasHoverFocus ? kgHoverFrameEdgeIds : new Set();

    ctx.save();
    ctx.translate(kgOffset.x, kgOffset.y);
    ctx.scale(kgScale, kgScale);

    for (const edge of renderEdges) {
        const source = nodeMap.get(edge.source);
        const target = nodeMap.get(edge.target);
        if (!source || !target) continue;

        const edgeId = edge.id || `${edge.source}-${edge.target}`;
        const isHoverEdge = activeEdgeIds.has(edgeId);
        const isCurrentEdge = currentId && (edge.source === currentId || edge.target === currentId);
        const isExplicitLink = edge.type === 'file_link';
        const isDimmed = hasHoverFocus && !isHoverEdge;
        const score = Number.isFinite(edge.score) ? edge.score : 0.2;

        ctx.beginPath();
        ctx.moveTo(source.x, source.y);
        ctx.lineTo(target.x, target.y);
        ctx.lineCap = 'round';
        ctx.strokeStyle = isHoverEdge
            ? 'rgba(96, 136, 186, 0.42)'
            : (isCurrentEdge ? 'rgba(86, 138, 204, 0.34)' : 'rgba(48, 54, 62, 0.28)');
        ctx.lineWidth = isHoverEdge
            ? 0.32 + hoverT * 0.44
            : (isExplicitLink ? Math.max(0.40, 0.28 + score * 0.30) : Math.max(0.22, 0.16 + score * 0.22));
        const baseAlpha = isCurrentEdge ? 0.52 : 0.40;
        ctx.globalAlpha = isDimmed ? Math.max(0.02, 0.14 - hoverT * 0.10) : (isHoverEdge ? 0.16 + hoverT * 0.24 : baseAlpha);
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    for (const node of renderNodes) {
        const isCurrent = isCurrentKGDocNode(node) || node.isCurrent;
        const isHover = node === kgHoverNode;
        const isSelected = kgSelectedNode && node.id === kgSelectedNode.id;
        const isActive = activeNodeIds.has(node.id);
        const isDimmed = hasHoverFocus && !isActive;
        const connectionRatio = (node.connections || 0) / maxConnections;
        const r = getKGFileNodeRadius(node, isFullGraph, maxConnections, isCurrent);

        if (isCurrent || isHover || isSelected) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, r + (isCurrent ? 7 : 5), 0, Math.PI * 2);
            ctx.fillStyle = isCurrent ? 'rgba(77, 166, 255, 0.16)' : 'rgba(40, 45, 52, 0.10)';
            ctx.globalAlpha = isDimmed ? 0.18 : (isHover ? Math.max(0.2, hoverT) : 1);
            ctx.fill();
            ctx.globalAlpha = 1;
        }

        if (isCurrent) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, r + 3.4 + Math.sin(kgPulsePhase * 2) * 0.8, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(73, 154, 244, 0.34)';
            ctx.lineWidth = 1.15;
            ctx.stroke();
        }

        const nodeGray = connectionRatio > 0.58 ? '#4f555d' : (connectionRatio > 0.22 ? '#858c96' : '#c3c8d0');
        const fill = isCurrent ? '#3698f5' : (isHover || isSelected ? '#272b31' : nodeGray);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
        ctx.fillStyle = fill;
        const normalAlpha = isCurrent || isSelected ? 1 : (0.48 + Math.min(0.34, connectionRatio * 0.42));
        const hoverAlpha = isHover || isActive ? Math.max(normalAlpha, 0.45 + hoverT * 0.5) : normalAlpha;
        ctx.globalAlpha = isDimmed ? Math.max(0.08, normalAlpha * (1 - hoverT * 0.78)) : hoverAlpha;
        ctx.fill();
        ctx.globalAlpha = 1;

        if (isCurrent) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, Math.max(1.7, r * 0.38), 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.globalAlpha = 0.82;
            ctx.fill();
            ctx.globalAlpha = 1;
        }
    }

    const prioritizedLabelNodes = [...renderNodes].sort((a, b) => {
        const aPinned = Number(isCurrentKGDocNode(a) || a.isCurrent || a === kgHoverNode || kgSelectedNode?.id === a.id);
        const bPinned = Number(isCurrentKGDocNode(b) || b.isCurrent || b === kgHoverNode || kgSelectedNode?.id === b.id);
        return bPinned - aPinned || (b.connections || 0) - (a.connections || 0) || String(a.name || '').localeCompare(String(b.name || ''));
    });
    const fullLabelLimit = kgScale >= 1.25 ? 220 : (kgScale >= 0.9 ? 150 : 78);
    const labelLimit = isFullGraph ? fullLabelLimit : Math.min(18, renderNodes.length);
    const labelGrid = createKGLabelPlacementGrid(isFullGraph ? 48 : 42);

    for (let labelIndex = 0; labelIndex < prioritizedLabelNodes.length; labelIndex++) {
        const node = prioritizedLabelNodes[labelIndex];
        const isCurrent = isCurrentKGDocNode(node) || node.isCurrent;
        const isHover = node === kgHoverNode;
        const isSelected = kgSelectedNode && node.id === kgSelectedNode.id;
        const isActive = activeNodeIds.has(node.id);
        const isDimmed = hasHoverFocus && !isActive;
        const connectionRatio = (node.connections || 0) / maxConnections;
        const isPinned = isCurrent || isHover || isSelected || (hasHoverFocus && isActive);
        const label = truncateKGGraphLabel(node.name || node.label || node.id, isFullGraph ? 34 : 19);
        const shouldShowLabel = !isDimmed && labelIndex < labelLimit && (
            isCurrent
            || isHover
            || isSelected
            || (hasHoverFocus && isActive)
            || (isFullGraph && (kgScale >= 0.72 || connectionRatio > 0.42))
            || (!isFullGraph && (renderNodes.length <= 12 || connectionRatio > 0.42))
        );
        if (!shouldShowLabel) continue;

        const r = getKGFileNodeRadius(node, isFullGraph, maxConnections, isCurrent);
        const desiredFontSize = isFullGraph ? 11.5 + Math.min(1.2, connectionRatio * 1.5) : 10.5;
        const fontSize = desiredFontSize / Math.max(0.72, kgScale);
        const fontWeight = isPinned ? 600 : (connectionRatio > 0.58 ? 500 : 400);
        ctx.font = `${fontWeight} ${fontSize}px Inter, "Microsoft YaHei", sans-serif`;
        const textWidth = Math.min(ctx.measureText(label).width, isFullGraph ? 230 : 145);
        const candidates = getKGFileLabelCandidates(node, r, textWidth, fontSize + 3);
        const offset = Math.abs(hashKGString(node.id || label)) % candidates.length;
        const orderedCandidates = candidates.slice(offset).concat(candidates.slice(0, offset));
        let placement = orderedCandidates.find(candidate => !labelGrid.collides(candidate.box));
        if (!placement && isPinned) placement = candidates[0];
        if (!placement) continue;
        labelGrid.add(placement.box);

        ctx.textAlign = placement.align;
        ctx.textBaseline = placement.baseline;
        ctx.fillStyle = isCurrent ? '#176fc1' : (isHover || isSelected ? '#111318' : '#2f343b');
        ctx.globalAlpha = isCurrent || isSelected ? 0.98 : (isHover || isActive ? 0.42 + hoverT * 0.48 : 0.76);
        ctx.fillText(label, placement.x, placement.y);
        ctx.globalAlpha = 1;
    }

    ctx.restore();

    if (kgActiveCanvasId === 'kg-mini-canvas') {
        ctx.save();
        ctx.font = '10px Inter, "Microsoft YaHei", sans-serif';
        ctx.fillStyle = 'rgba(112, 119, 130, 0.74)';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        const hiddenCount = Math.max(0, kgGraphData.nodes.length - renderNodes.length);
        const suffix = hiddenCount ? ` · +${hiddenCount}` : '';
        ctx.fillText(`${kgGraphData.nodes.length} 文件 · ${kgGraphData.edges.length} 关系${suffix}`, 10, h - 9);
        ctx.textAlign = 'right';
        ctx.fillStyle = 'rgba(60, 64, 72, 0.78)';
        ctx.fillText('Knowledge Hub Graph', w - 10, h - 9);
        ctx.restore();
    }
}

function drawKGGraph() {
    if (!kgCtx || !kgCanvas) return;
    const ctx = kgCtx;
    const w = kgCanvas.width;
    const h = kgCanvas.height;

    const isFileGraph = kgGraphData.nodes.some(isMarkdownFileNode);
    ctx.clearRect(0, 0, w, h);

    if (isFileGraph) {
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        drawKGFileGraph(ctx, w, h);
        return;
    }

    const themeColors = getThemeColors();
    const isLight = true;
    const bgColor = themeColors.bgBase;
    const textColor = themeColors.textPrimary;
    const mutedColor = themeColors.textMuted;
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, w, h);

    let renderNodes = kgGraphData.nodes;
    let renderEdges = kgGraphData.edges;
    let nodeMap = new Map(kgGraphData.nodes.map(n => [n.id, n]));

    const shouldAggregate = kgAggregationEnabled
        && kgGraphData.nodes.length > KG_AGGREGATION_THRESHOLD
        && kgScale < 0.6;

    if (shouldAggregate) {
        const cellSize = KG_AGGREGATION_CELL_SIZE / kgScale;
        const { aggregated, original } = aggregateNodes(kgGraphData.nodes, cellSize);
        renderNodes = aggregated;
        nodeMap = new Map(aggregated.map(n => [n.id, n]));

        const aggEdgeSet = new Set();
        renderEdges = [];
        for (const edge of kgGraphData.edges) {
            const srcOrig = original.find(o => o.original.id === edge.source);
            const tgtOrig = original.find(o => o.original.id === edge.target);
            if (!srcOrig || !tgtOrig) continue;
            const srcAgg = srcOrig.aggregated;
            const tgtAgg = tgtOrig.aggregated;
            if (srcAgg.id === tgtAgg.id) continue;
            const edgeKey = `${srcAgg.id}-${tgtAgg.id}`;
            if (!aggEdgeSet.has(edgeKey)) {
                aggEdgeSet.add(edgeKey);
                renderEdges.push({
                    source: srcAgg.id,
                    target: tgtAgg.id,
                    type: edge.type || 'related_to',
                    style: 'solid',
                    edgeColor: RELATIONSHIP_COLORS[edge.type] || '#6b7280',
                    id: edgeKey,
                });
            }
        }
    }

    let activeNodeIds = kgHighlightedNodeIds;
    let activeEdgeIds = kgHighlightedEdgeIds;
    if (kgHoverNode && !kgDragNode && !kgIsPanning) {
        activeNodeIds = new Set([kgHoverNode.id]);
        activeEdgeIds = new Set();
        for (const edge of renderEdges) {
            if (edge.source === kgHoverNode.id || edge.target === kgHoverNode.id) {
                activeEdgeIds.add(edge.id || `${edge.source}-${edge.target}`);
                activeNodeIds.add(edge.source === kgHoverNode.id ? edge.target : edge.source);
            }
        }
    }
    const hasHighlight = activeNodeIds.size > 0;

    ctx.save();
    ctx.translate(kgOffset.x, kgOffset.y);
    ctx.scale(kgScale, kgScale);
    const fileGraphMaxConnections = isFileGraph
        ? Math.max(1, ...renderNodes.map(n => n.connections || 0))
        : 1;

    for (const edge of renderEdges) {
        const source = nodeMap.get(edge.source);
        const target = nodeMap.get(edge.target);
        if (!source || !target) continue;

        const edgeId = edge.id || `${edge.source}-${edge.target}`;
        const isHighlighted = activeEdgeIds.has(edgeId);
        const isDimmed = hasHighlight && !isHighlighted;
        const isFileEdge = edge.type === 'file_similarity'
            || edge.type === 'file_link'
            || edge.type === 'folder_neighbor'
            || edge.type === 'theme_overlap';
        const edgeStyle = isFileEdge ? 'solid' : (edge.style || 'solid');
        const edgeColor = isFileEdge
            ? (isHighlighted ? '#7655e8' : (RELATIONSHIP_COLORS[edge.type] || '#d8dce3'))
            : (edge.edgeColor || RELATIONSHIP_COLORS[edge.type] || '#6b7280');

        const sx = source.x;
        const sy = source.y;
        const tx = target.x;
        const ty = target.y;
        const dx = tx - sx;
        const dy = ty - sy;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const nx = -dy / dist;
        const ny = dx / dist;

        const curvature = isFileEdge ? 0 : 0.08;
        const cpx = (sx + tx) / 2 + nx * dist * curvature;
        const cpy = (sy + ty) / 2 + ny * dist * curvature;

        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.quadraticCurveTo(cpx, cpy, tx, ty);

        if (edgeStyle === 'dashed') {
            ctx.setLineDash([6, 4]);
        } else {
            ctx.setLineDash([]);
        }

        ctx.strokeStyle = edgeColor;
        ctx.lineWidth = isFileEdge ? (isHighlighted ? 1.05 : Math.min(0.58, 0.24 + (edge.score || 0) * 0.44)) : (isHighlighted ? 2 : 1);
        ctx.globalAlpha = isDimmed ? 0.018 : (isHighlighted ? 0.72 : (isFileEdge ? 0.14 : 0.26));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;

        if (isHighlighted && !isFileEdge) {
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.quadraticCurveTo(cpx, cpy, tx, ty);
            ctx.strokeStyle = edgeColor;
            ctx.lineWidth = 8;
            ctx.globalAlpha = 0.08;
            ctx.stroke();
            ctx.globalAlpha = 1;
        }

        const labelT = 0.5;
        const labelX = (1 - labelT) * (1 - labelT) * sx + 2 * (1 - labelT) * labelT * cpx + labelT * labelT * tx;
        const labelY = (1 - labelT) * (1 - labelT) * sy + 2 * (1 - labelT) * labelT * cpy + labelT * labelT * ty;
        const relLabel = RELATIONSHIP_STYLES[edge.type]?.label || edge.type;
        if (relLabel && !isFileEdge && kgScale > 0.4 && !isDimmed) {
            ctx.font = `${isHighlighted ? 'bold ' : ''}${isHighlighted ? 11 : 9}px sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';

            const padding = 3;
            const textWidth = ctx.measureText(relLabel).width;
            ctx.fillStyle = isLight ? 'rgba(248,249,251,0.9)' : 'rgba(12,14,20,0.9)';
            ctx.beginPath();
            ctx.roundRect(labelX - textWidth / 2 - padding, labelY - 15, textWidth + padding * 2, 15, 3);
            ctx.fill();
            ctx.fillStyle = isHighlighted ? edgeColor : mutedColor;
            ctx.fillText(relLabel, labelX, labelY - 2);
        }

        if (!isFileEdge) {
            const tAngle = Math.atan2(ty - cpy, tx - cpx);
            const arrowLen = isHighlighted ? 10 : 7;
            const targetR = target.radius || 12;
            const ax = tx - Math.cos(tAngle) * (targetR + 3);
            const ay = ty - Math.sin(tAngle) * (targetR + 3);
            ctx.beginPath();
            ctx.moveTo(ax, ay);
            ctx.lineTo(ax - arrowLen * Math.cos(tAngle - 0.35), ay - arrowLen * Math.sin(tAngle - 0.35));
            ctx.lineTo(ax - arrowLen * Math.cos(tAngle + 0.35), ay - arrowLen * Math.sin(tAngle + 0.35));
            ctx.closePath();
            ctx.fillStyle = edgeColor;
            ctx.globalAlpha = isDimmed ? 0.08 : (isHighlighted ? 0.9 : 0.45);
            ctx.fill();
            ctx.globalAlpha = 1;
        }
    }

    for (const node of renderNodes) {
        const r = node.radius || 12;
        const isFileNode = isMarkdownFileNode(node);
        const color = isFileNode ? '#575b60' : (ENTITY_COLORS[node.type] || ENTITY_COLORS.default);
        const isHover = node === kgHoverNode;
        const isSelected = node === kgSelectedNode;
        const isHighlighted = activeNodeIds.has(node.id);
        const isDimmed = hasHighlight && !isHighlighted;
        const isDocNode = node.type === 'document';
        const isAnchorNode = node.type === 'anchor';

        if (isFileNode) {
            const connectionRatio = (node.connections || 0) / fileGraphMaxConnections;
            const baseColor = connectionRatio > 0.7 ? '#7f8792' : (connectionRatio > 0.25 ? '#adb3bc' : '#d9dde3');
            const activeColor = isHover ? '#6247d9' : '#707781';
            const nodeColor = isSelected || isHover || isHighlighted ? activeColor : baseColor;
            const isSmallFileGraph = kgGraphData.nodes.length <= 30;

            ctx.beginPath();
            ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
            ctx.fillStyle = nodeColor;
            ctx.globalAlpha = isDimmed ? 0.07 : (isHighlighted || isHover || isSelected ? 0.96 : (connectionRatio > 0 ? 0.72 : (isSmallFileGraph ? 0.62 : 0.42)));
            ctx.fill();
            ctx.globalAlpha = 1;

            const isFullGraph = kgActiveCanvasId === 'kg-full-canvas';
            const shouldShowLabel = isHover || isSelected || isHighlighted || isSmallFileGraph || (isFullGraph && (kgScale > 0.8 || connectionRatio > 0.35)) || (!isFullGraph && connectionRatio > 0.55);
            if (shouldShowLabel && !isDimmed) {
                const label = node.name || node.id;
                ctx.font = `${isSelected || isHover ? '600 ' : ''}${Math.max(7.5, Math.min(9.5, r * 0.28 + 6.8))}px sans-serif`;
                ctx.fillStyle = isSelected || isHover || isHighlighted ? '#25282d' : '#4b5056';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.globalAlpha = isSelected || isHover || isHighlighted ? 0.96 : 0.62;
                ctx.fillText(label, node.x, node.y + r + 4);
                ctx.globalAlpha = 1;
            }

            continue;
        }

        if (isSelected || isHover) {
            ctx.beginPath();
            if (isDocNode && !isFileNode) {
                const w = r * 2.2, h = r * 1.6;
                ctx.roundRect(node.x - w / 2, node.y - h / 2, w, h, 6);
            } else {
                ctx.arc(node.x, node.y, r + 10, 0, Math.PI * 2);
            }
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.12;
            ctx.fill();
            ctx.globalAlpha = 1;
        }

        if (isSelected) {
            const pulseR = r + 14 + Math.sin(kgPulsePhase * 2) * 3;
            ctx.beginPath();
            if (isDocNode && !isFileNode) {
                const w = (r + 14) * 2.2, h = (r + 14) * 1.6;
                ctx.roundRect(node.x - w / 2, node.y - h / 2, w, h, 8);
            } else {
                ctx.arc(node.x, node.y, pulseR, 0, Math.PI * 2);
            }
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.globalAlpha = 0.3 + Math.sin(kgPulsePhase * 2) * 0.1;
            ctx.setLineDash([4, 4]);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.globalAlpha = 1;
        }

        if (isHighlighted && !isSelected) {
            ctx.beginPath();
            if (isDocNode && !isFileNode) {
                const w = (r + 6) * 2.2, h = (r + 6) * 1.6;
                ctx.roundRect(node.x - w / 2, node.y - h / 2, w, h, 7);
            } else {
                ctx.arc(node.x, node.y, r + 6, 0, Math.PI * 2);
            }
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.globalAlpha = 0.25;
            ctx.stroke();
            ctx.globalAlpha = 1;
        }

        ctx.save();
        if (!isDimmed) {
            ctx.shadowColor = color;
            ctx.shadowBlur = isHover || isSelected ? 16 : (isHighlighted ? 10 : 4);
            ctx.shadowOffsetX = 0;
            ctx.shadowOffsetY = 0;
        }

        ctx.beginPath();
        if (isDocNode && !isFileNode) {
            // 文档节点：圆角矩形
            const w = r * 2.2, h = r * 1.6;
            ctx.roundRect(node.x - w / 2, node.y - h / 2, w, h, 6);
        } else if (isAnchorNode) {
            // 锚点节点：菱形
            ctx.moveTo(node.x, node.y - r);
            ctx.lineTo(node.x + r, node.y);
            ctx.lineTo(node.x, node.y + r);
            ctx.lineTo(node.x - r, node.y);
            ctx.closePath();
        } else {
            ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
        }

        if (isDimmed) {
            ctx.fillStyle = isFileNode ? '#c4c7cb' : '#d1d5db';
            ctx.globalAlpha = 0.25;
        } else {
            ctx.fillStyle = color;
            ctx.globalAlpha = isHover || isSelected ? 1 : (isHighlighted ? 0.95 : 0.8);
        }
        ctx.fill();
        ctx.restore();
        ctx.globalAlpha = 1;

        if (!isDimmed && !isFileNode) {
            ctx.beginPath();
            if (isDocNode) {
                const w = r * 2.2, h = r * 1.6;
                ctx.roundRect(node.x - w / 2, node.y - h / 2, w, h, 6);
            } else if (isAnchorNode) {
                ctx.moveTo(node.x, node.y - r);
                ctx.lineTo(node.x + r, node.y);
                ctx.lineTo(node.x, node.y + r);
                ctx.lineTo(node.x - r, node.y);
                ctx.closePath();
            } else {
                ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
            }
            ctx.strokeStyle = isLight ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.15)';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // 文档节点内部绘制文档图标
        if (isDocNode && !isFileNode && !isDimmed && r > 14) {
            ctx.save();
            ctx.globalAlpha = 0.4;
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.2;
            const iconW = r * 0.5, iconH = r * 0.65;
            const ix = node.x - iconW / 2, iy = node.y - iconH / 2;
            ctx.beginPath();
            ctx.roundRect(ix, iy, iconW, iconH, 2);
            ctx.stroke();
            // 折角
            ctx.beginPath();
            ctx.moveTo(ix + iconW * 0.65, iy);
            ctx.lineTo(ix + iconW, iy + iconH * 0.3);
            ctx.stroke();
            // 横线
            ctx.beginPath();
            ctx.moveTo(ix + 3, iy + iconH * 0.5);
            ctx.lineTo(ix + iconW - 3, iy + iconH * 0.5);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(ix + 3, iy + iconH * 0.7);
            ctx.lineTo(ix + iconW * 0.6, iy + iconH * 0.7);
            ctx.stroke();
            ctx.restore();
        }

        // 锚点节点内部绘制锚点图标（小圆点）
        if (isAnchorNode && !isDimmed && r > 5) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, r * 0.3, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.globalAlpha = 0.5;
            ctx.fill();
            ctx.globalAlpha = 1;
        }

        if (!isFileNode) {
            const innerR = r * 0.55;
            ctx.beginPath();
            ctx.arc(node.x - r * 0.15, node.y - r * 0.15, innerR, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(255,255,255,0.15)';
            ctx.globalAlpha = isDimmed ? 0.1 : 1;
            ctx.fill();
            ctx.globalAlpha = 1;
        }

        if ((kgScale > (isFileNode ? 0.75 : 0.35) || node === kgSelectedNode || node === kgHoverNode) && !isDimmed) {
            ctx.font = `${isSelected || isHover ? 'bold ' : ''}${Math.max(10, Math.min(13, r * 0.7))}px sans-serif`;
            ctx.fillStyle = textColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.globalAlpha = isDimmed ? 0.3 : 1;

            const label = node.name || node.id;
            const textW = ctx.measureText(label).width;
            const labelY = node.y + r + 5;

            ctx.fillStyle = isFileNode ? 'rgba(255,255,255,0.82)' : 'rgba(248,249,251,0.75)';
            ctx.beginPath();
            ctx.roundRect(node.x - textW / 2 - 3, labelY - 1, textW + 6, 16, 3);
            ctx.fill();

            ctx.fillStyle = isDimmed ? mutedColor : (isSelected || isHover ? color : textColor);
            ctx.fillText(label, node.x, labelY);
            ctx.globalAlpha = 1;
        }

        if (!isFileNode && node.connections > 0 && kgScale > 0.6 && !isDimmed && r > 12) {
            ctx.font = 'bold 9px sans-serif';
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(node.connections, node.x, node.y);
        }
    }

    ctx.restore();

    if (kgHighlightedNodeIds.size > 0) {
        ctx.save();
        ctx.font = '12px sans-serif';
        ctx.fillStyle = mutedColor;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`高亮 ${kgHighlightedNodeIds.size} 节点 / ${kgHighlightedEdgeIds.size} 关系  |  单击节点切换高亮 / 双击追踪推理路径 / 点击空白取消`, 12, h - 24);
        ctx.restore();
    }

    if (shouldAggregate) {
        ctx.save();
        ctx.font = '11px sans-serif';
        ctx.fillStyle = mutedColor;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`聚合模式: ${renderNodes.length} 聚类 / ${kgGraphData.nodes.length} 原始节点 (放大查看详情)`, 12, h - 24);
        ctx.restore();
    }

    if (isFileGraph && kgGraphData.nodes.length > 0 && kgActiveCanvasId === 'kg-mini-canvas') {
        ctx.save();
        ctx.font = '10px sans-serif';
        ctx.fillStyle = 'rgba(111,118,128,0.72)';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`${kgGraphData.nodes.length} 文件 · ${kgGraphData.edges.length} 关系`, 10, h - 10);
        ctx.restore();
    }

    if (kgGraphData.nodes.length === 0) {
        drawKGEmpty();
    }
}

function lightenColor(hex, percent) {
    const num = parseInt(hex.replace('#', ''), 16);
    const r = Math.min(255, (num >> 16) + Math.round(2.55 * percent));
    const g = Math.min(255, ((num >> 8) & 0x00FF) + Math.round(2.55 * percent));
    const b = Math.min(255, (num & 0x0000FF) + Math.round(2.55 * percent));
    return `rgb(${r},${g},${b})`;
}

function drawKGEmpty() {
    if (!kgCtx || !kgCanvas) return;
    const ctx = kgCtx;
    const w = kgCanvas.width;
    const h = kgCanvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#8b929e';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('暂无图谱数据，请打开文件或上传文档构建知识图谱', w / 2, h / 2);
}

function renderKGLegend() {
    const legend = document.getElementById('kg-legend');
    if (!legend) return;

    const entityItems = Object.entries(ENTITY_COLORS)
        .filter(([k]) => k !== 'default')
        .map(([type, color]) =>
            `<div class="legend-item"><span class="legend-dot" style="background:${color}"></span>${type}</div>`
        ).join('');

    const lineItems = Object.entries(RELATIONSHIP_STYLES)
        .slice(0, 8)
        .map(([type, info]) =>
            `<div class="legend-item"><span class="legend-line" style="border-top:2px ${info.style} ${info.color};width:20px;display:inline-block"></span>${info.label}</div>`
        ).join('');

    legend.innerHTML = `
        <div style="margin-bottom:6px;font-weight:600;font-size:11px;color:var(--text-secondary)">实体类型</div>
        ${entityItems}
        <div style="margin:8px 0 6px;font-weight:600;font-size:11px;color:var(--text-secondary)">关系类型</div>
        ${lineItems}
    `;
}

function showKGNodeInfo(node) {
    const infoEl = document.getElementById('kg-node-info');
    if (!infoEl) return;
    const color = ENTITY_COLORS[node.type] || ENTITY_COLORS.default;
    const connections = node.connections || 0;
    const isDocNode = node.type === 'document';
    const isAnchorNode = node.type === 'anchor';

    const outgoing = kgGraphData.edges.filter(e => e.source === node.id);
    const incoming = kgGraphData.edges.filter(e => e.target === node.id);
    const allRelated = [...outgoing, ...incoming];

    const relatedByType = {};
    for (const edge of allRelated) {
        const relType = edge.type || 'related_to';
        const style = RELATIONSHIP_STYLES[relType] || { label: relType, style: 'solid', color: '#6b7280' };
        if (!relatedByType[relType]) {
            relatedByType[relType] = { ...style, items: [] };
        }
        const isOutgoing = edge.source === node.id;
        const otherId = isOutgoing ? edge.target : edge.source;
        const other = kgGraphData.nodes.find(n => n.id === otherId);
        relatedByType[relType].items.push({
            name: other?.name || otherId,
            direction: isOutgoing ? '→' : '←',
            edgeStyle: edge.style || style.style,
            reason: edge.properties?.reason || '',
        });
    }

    if (typeof switchKGRightView === 'function') {
        switchKGRightView('info');
    }

    const relatedHtml = Object.entries(relatedByType).map(([relType, info]) => {
        const itemsHtml = info.items.map(item => `
            <div style="padding:3px 0;font-size:13px;color:var(--text-secondary);display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                <span style="color:${info.color};font-weight:600">${item.direction}</span>
                <span style="color:${info.color}">${escapeHtml(info.label || relType)}</span>
                <span style="border-bottom:1px ${item.edgeStyle} ${info.color};min-width:20px"></span>
                <span style="color:var(--text-primary)">${escapeHtml(item.name)}</span>
                ${item.reason ? `<span style="font-size:11px;color:var(--text-muted);background:var(--bg-secondary);padding:1px 6px;border-radius:3px;margin-left:4px">${escapeHtml(item.reason)}</span>` : ''}
            </div>
        `).join('');

        return `
            <div style="margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
                    <span style="width:16px;border-top:2px ${info.style} ${info.color};display:inline-block"></span>
                    <span style="font-size:13px;font-weight:600;color:${info.color}">${escapeHtml(info.label || relType)}</span>
                    <span style="font-size:11px;color:var(--text-muted)">(${info.items.length})</span>
                </div>
                <div style="padding-left:22px">${itemsHtml}</div>
            </div>
        `;
    }).join('');

    // 文档/锚点节点的特殊信息展示
    let specialInfo = '';
    if (isDocNode) {
        const props = node.properties || {};
        const topicTags = (props.topic_tags || '').split(',').filter(Boolean);
        const anchorCount = props.anchor_count || 0;
        specialInfo = `
            ${topicTags.length ? `<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px">${topicTags.map(t => `<span style="font-size:11px;padding:2px 8px;border-radius:4px;background:#3b82f615;color:#3b82f6;border:1px solid #3b82f630">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
            ${anchorCount ? `<div style="margin-top:6px;font-size:12px;color:var(--text-muted)">包含 ${anchorCount} 个记忆锚点</div>` : ''}
            ${props.content_preview ? `<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);line-height:1.6;padding:8px;background:var(--bg-secondary);border-radius:6px">${escapeHtml(props.content_preview)}</div>` : ''}
        `;
    } else if (isAnchorNode) {
        const props = node.properties || {};
        const anchorTypeLabels = { core_topic: '核心主题', key_argument: '关键论点', key_entity: '关键实体', method_conclusion: '方法/结论', data_finding: '数据发现' };
        const anchorTypeColors = { core_topic: '#3b82f6', key_argument: '#8b5cf6', key_entity: '#f59e0b', method_conclusion: '#10b981', data_finding: '#ef4444' };
        const aType = props.anchor_type || '';
        const aColor = anchorTypeColors[aType] || '#6b7280';
        specialInfo = `
            <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px">
                <span style="font-size:11px;padding:2px 8px;border-radius:4px;background:${aColor}15;color:${aColor};border:1px solid ${aColor}30">${anchorTypeLabels[aType] || aType}</span>
                ${props.doc_title ? `<span style="font-size:11px;padding:2px 8px;border-radius:4px;background:#3b82f615;color:#3b82f6;border:1px solid #3b82f630">来自: ${escapeHtml(props.doc_title)}</span>` : ''}
            </div>
            ${props.content ? `<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);line-height:1.6;padding:8px;background:var(--bg-secondary);border-radius:6px">${escapeHtml(props.content)}</div>` : ''}
            ${props.keywords ? `<div style="margin-top:6px;font-size:12px;color:var(--text-muted)">关键词: ${escapeHtml(props.keywords)}</div>` : ''}
        `;
    }

    const nodeIcon = isDocNode ? '📄' : (isAnchorNode ? '◆' : '●');

    infoEl.innerHTML = `
        <div style="padding:14px;border-bottom:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
                <span style="width:16px;height:16px;border-radius:${isDocNode ? '4px' : '50%'};background:${color};flex-shrink:0;box-shadow:0 0 8px ${color}40;display:flex;align-items:center;justify-content:center;font-size:9px;color:#fff">${isDocNode ? '📄' : (isAnchorNode ? '◆' : '')}</span>
                <span style="font-size:17px;font-weight:700;color:var(--text-primary)">${escapeHtml(node.name || node.id)}</span>
            </div>
            <div style="display:flex;gap:12px;font-size:13px;color:var(--text-muted);flex-wrap:wrap">
                <span>类型: <b style="color:${color}">${isDocNode ? '文档' : (isAnchorNode ? '记忆锚点' : escapeHtml(node.type || 'unknown'))}</b></span>
                <span>连接数: <b style="color:var(--primary)">${connections}</b></span>
                <span>出边: ${outgoing.length}</span>
                <span>入边: ${incoming.length}</span>
            </div>
            ${specialInfo}
            ${node.description || node.properties?.description ? `<div style="font-size:13px;color:var(--text-secondary);margin-top:8px;line-height:1.6">${renderMarkdown(node.description || node.properties?.description)}</div>` : ''}
            <button class="toolbar-btn" onclick="previewNodeContent('${(node.id || '').replace(/'/g, "\\'")}')" style="margin-top:10px;font-size:12px;padding:4px 10px;width:100%;justify-content:center;border:1px solid var(--border);border-radius:6px">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                预览内容
            </button>
        </div>
        <div style="padding:14px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <div style="font-size:14px;font-weight:600;color:var(--text-primary)">关联关系</div>
                <button class="toolbar-btn" onclick="startReasoningAnimation(kgSelectedNode)" style="font-size:12px;padding:3px 8px" data-tooltip="追踪推理路径">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    推理路径
                </button>
            </div>
            ${allRelated.length ? relatedHtml : '<div style="font-size:13px;color:var(--text-muted)">无关联关系</div>'}
        </div>
    `;
}

function kgZoomIn() {
    setKGSmoothZoom((kgZoomAnimating ? kgZoomTargetScale : kgScale) * 1.22);
}

function kgZoomOut() {
    setKGSmoothZoom((kgZoomAnimating ? kgZoomTargetScale : kgScale) / 1.22);
}

function kgResetView() {
    kgZoomAnimating = false;
    kgZoomAnchor = null;
    kgScale = 1;
    kgZoomTargetScale = 1;
    kgOffset = { x: 0, y: 0 };
    if (kgGraphData.nodes.some(isMarkdownFileNode)) {
        syncKGSelectionToCurrentDoc();
        fitKGGraphToCanvas(true);
    } else {
        kgSelectedNode = null;
        kgHighlightedNodeIds.clear();
        kgHighlightedEdgeIds.clear();
    }
    stopReasoningAnimation();
    saveKGCanvasViewState();
}

function scoreKGFileForQuery(file, query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return 0;
    const title = `${file.name || ''} ${file.path || ''}`.toLowerCase();
    const content = (file.content || '').toLowerCase();
    let score = 0;
    if (title.includes(q)) score += 8;
    if (content.includes(q)) score += 5;
    const qTokens = tokenizeFileName(q);
    const docTokens = tokenizeFileName(`${title} ${content.slice(0, 1800)}`);
    for (const token of qTokens) {
        if (token.length <= 1 && !/[\u4e00-\u9fff]/.test(token)) continue;
        if (docTokens.has(token)) score += token.length > 1 ? 1.2 : 0.35;
    }
    return score;
}

function kgEscapeHtml(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function kgRenderMarkdown(value) {
    const text = String(value || '');
    try {
        if (typeof renderMarkdown === 'function') return renderMarkdown(text);
    } catch (err) {
        console.warn('KG markdown render failed:', err);
    }
    return kgEscapeHtml(text).replace(/\n/g, '<br>');
}

function getKGFileResourceStats(items = (typeof fileStore !== 'undefined' ? fileStore.files : [])) {
    const stats = { files: 0, folders: 0, markdown: 0, totalSize: 0, samples: [] };
    function walk(list) {
        for (const item of list || []) {
            if (item.isDirectory) {
                stats.folders += 1;
                walk(item.children || []);
            } else {
                stats.files += 1;
                if (/\.(md|markdown)$/i.test(item.name || item.path || '')) stats.markdown += 1;
                if (typeof item.size === 'number') stats.totalSize += item.size;
                if (stats.samples.length < 12) stats.samples.push(item.path || item.name);
            }
        }
    }
    walk(items);
    return stats;
}

function classifyKGAssistantIntent(query) {
    const q = String(query || '').trim().toLowerCase();
    const compact = q.replace(/\s+/g, '');
    if (!compact) return 'empty';
    if (/^(你好|您好|hi|hello|hey|嗨|在吗|在不在|哈喽)[!！。,.，？?]*$/.test(compact)) return 'greeting';
    if (/(多少|几个|几份|总数|数量|统计|count).*(文件|文档|资料|markdown|md)|(?:文件|文档|资料|markdown|md).*(多少|几个|几份|总数|数量|统计|count)/i.test(compact)) return 'file_count';
    if (/(列出|显示|有哪些|有什么|目录|清单|列表).*(文件|文档|资料|markdown|md)|(?:文件|文档|资料|markdown|md).*(列出|显示|有哪些|有什么|目录|清单|列表)/i.test(compact)) return 'file_list';
    if (/^(谢谢|thanks|thankyou|ok|好的|好|收到|明白)[!！。,.，]*$/.test(compact)) return 'ack';
    return 'content_query';
}

function buildKGAssistantDirectAnswer(query) {
    const intent = classifyKGAssistantIntent(query);
    const stats = getKGFileResourceStats();
    if (intent === 'greeting') {
        return `你好，我在。当前文档库有 ${stats.files} 个文件、${stats.folders} 个文件夹。`;
    }
    if (intent === 'ack') {
        return '好的。';
    }
    if (intent === 'file_count') {
        return [
            `当前左侧文档资源库共有 ${stats.files} 个文件、${stats.folders} 个文件夹。`,
            `其中 Markdown 文档 ${stats.markdown} 个。`,
        ].join('\n');
    }
    if (intent === 'file_list') {
        const list = stats.samples.length
            ? stats.samples.map((path, index) => `${index + 1}. ${path}`).join('\n')
            : '暂无文件。';
        return [
            `当前文档库共有 ${stats.files} 个文件。先列出前 ${Math.min(stats.samples.length, 12)} 个：`,
            '',
            list,
        ].join('\n');
    }
    return '';
}

function shouldKGUseFileResourceSearch(query) {
    return classifyKGAssistantIntent(query) === 'content_query';
}

function formatKGChatSourceLink(path) {
    const docPath = String(path || '').replace(/\\/g, '/');
    const files = typeof fileStore !== 'undefined' ? (fileStore.files || []) : [];
    const entry = typeof findFileEntry === 'function' ? findFileEntry(files, docPath) : null;
    const label = (entry?.name || docPath.split('/').pop() || docPath).replace(/\.(md|markdown)$/i, '');
    return `<a href="#" class="kg-doc-link kg-chat-source-link" data-doc-path="${kgEscapeHtml(docPath)}" title="${kgEscapeHtml(docPath)}">${kgEscapeHtml(label)}</a>`;
}

function cleanKGAssistantAnswerForChat(answer) {
    let text = String(answer || '').trim();
    text = text.replace(/\n+\s*\*\*?相关文档\*\*?[\s\S]*$/i, '');
    text = text.replace(/\n+\s*相关文档[\s\S]*$/i, '');
    text = text.replace(/<a\b[^>]*>(.*?)<\/a>/gi, '$1');
    text = text.replace(/<[^>]+>/g, '');
    text = text.replace(/【\d+】/g, '');
    text = text.replace(/\n{3,}/g, '\n\n').trim();
    return text;
}

function buildKGAssistantLocalAnswer(query, fileResourceData = null) {
    const files = getMarkdownFileEntries();

    const ranked = files
        .map(file => ({ file, score: scoreKGFileForQuery(file, query) }))
        .filter(item => item.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 5);

    const directAnswer = buildKGAssistantDirectAnswer(query);
    if (directAnswer) return directAnswer;

    const serverAnswer = cleanKGAssistantAnswerForChat(fileResourceData?.answer || '');
    const serverSources = fileResourceData?.sources || [];
    const hasServerSources = serverSources.length > 0;
    if (serverAnswer) {
        return serverAnswer;
    }

    if (!ranked.length && !hasServerSources) {
        return '文档中没有检索到足够明确的内容。你可以换一个更具体的问题，或直接把左侧文件路径发给我让我总结。';
    }

    return '检索到了相关文档，但当前没有生成稳定回答。请换一种问法，例如“总结这篇文档的核心观点”或“按场景归纳这些文档”。';
}

async function sendKGQuery() {
    if (kgQuerySending) return;
    const input = document.getElementById('kg-chat-input');
    const messages = document.getElementById('kg-chat-messages');
    if (!input || !input.value.trim()) return;
    if (!messages) {
        if (typeof showToast === 'function') showToast('问答面板未加载，请刷新页面后重试', 'error');
        return;
    }

    const query = input.value.trim();
    input.value = '';
    kgQuerySending = true;
    const sendBtn = document.querySelector('#kg-chat-panel .send-btn');
    if (sendBtn) sendBtn.disabled = true;

    messages.innerHTML += `
        <div class="chat-msg user">
            <div class="chat-avatar">你</div>
            <div class="chat-bubble">${escapeHtml(query)}</div>
        </div>`;

    const directAnswer = buildKGAssistantDirectAnswer(query);
    if (directAnswer) {
        messages.innerHTML += `
            <div class="chat-msg assistant">
                <div class="chat-avatar">AI</div>
                <div class="chat-bubble">${kgRenderMarkdown(directAnswer)}</div>
            </div>`;
        kgQuerySending = false;
        if (sendBtn) sendBtn.disabled = false;
        messages.scrollTop = messages.scrollHeight;
        return;
    }

    const loadingId = 'kg-loading-' + Date.now();
    messages.innerHTML += `
        <div class="chat-msg assistant" id="${loadingId}">
            <div class="chat-avatar">AI</div>
            <div class="chat-bubble"><div class="loading-dots"><span></span><span></span><span></span></div></div>
        </div>`;
    messages.scrollTop = messages.scrollHeight;

    try {
        let data = null;
        if ((!kgGraphData.nodes || kgGraphData.nodes.length === 0) && typeof refreshKGGraph === 'function') {
            await refreshKGGraph();
        }
        try {
            if (shouldKGUseFileResourceSearch(query)) {
                if (typeof queryServerFileResources === 'function') {
                    data = await queryServerFileResources(query, 8);
                } else {
                    data = await api('POST', '/file-resources/query', { query, top_k: 8 }, 60000);
                }
            }
        } catch (fileErr) {
            console.warn('file resource query failed, fallback to local files:', fileErr);
        }
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) {
            const answer = buildKGAssistantLocalAnswer(query, data);
            loadingEl.querySelector('.chat-bubble').innerHTML = kgRenderMarkdown(answer || '没有检索到可用结果。');
        }
    } catch (err) {
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) {
            const fallbackAnswer = buildKGAssistantLocalAnswer(query, null);
            loadingEl.querySelector('.chat-bubble').innerHTML = kgRenderMarkdown(fallbackAnswer || `查询失败: ${err.message}`);
        }
    } finally {
        kgQuerySending = false;
        if (sendBtn) sendBtn.disabled = false;
    }
    messages.scrollTop = messages.scrollHeight;
}

function kgChatKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendKGQuery();
    }
}

async function buildKGFromSelected() {
    if (!requireWriteAccess()) return;
    if (!fileStore.openedFile || !fileStore.openedFile.content) {
        showToast('请先在左侧选择一个文件并确保有内容', 'warning');
        return;
    }
    try {
        const content = fileStore.openedFile.content;
        const filename = fileStore.openedFile.name;
        const docKey = fileStore.openedFile.path || filename;
        showToast('正在拆解三元组并构建图谱，请稍候…', 'info');
        const data = await api('POST', '/knowledge-graph/build', { text: content, title: filename, doc_key: docKey });
        const entityCount = data.entity_count || 0;
        const relationCount = data.relation_count || 0;
        const tripleCount = (data.triples || []).length;
        showToast(`构建完成：${entityCount} 个实体，${relationCount} 个关系，${tripleCount} 个三元组`, 'success');
        kgCurrentDocKey = docKey;
        await refreshKGGraph();
        await loadDocGraphList();
    } catch (err) {
        showToast('构建图谱失败: ' + err.message, 'error');
    }
}

async function uploadFileToBuildKG() {
    if (!requireWriteAccess()) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.md,.txt,.pdf,.docx,.doc,.xlsx,.xls,.csv,.json,.html,.htm,.png,.jpg,.jpeg,.pptx';
    input.multiple = true;
    input.onchange = async () => {
        let lastDocKey = null;
        for (const file of input.files) {
            try {
                showToast(`正在拆解三元组并构建图谱: "${file.name}"…`, 'info');
                const formData = new FormData();
                formData.append('file', file);
                formData.append('doc_key', file.name);
                const data = await api('POST', '/documents/convert-and-build-kg', formData);
                const kgResult = data.kg_result || {};
                const entityCount = kgResult.entity_count || 0;
                const relationCount = kgResult.relation_count || 0;
                showToast(`"${file.name}" 构建完成：${entityCount} 个实体，${relationCount} 个关系`, 'success');
                if (data.doc_key) {
                    lastDocKey = data.doc_key;
                }
            } catch (err) {
                showToast(`处理 "${file.name}" 失败: ${err.message}`, 'error');
            }
        }
        if (lastDocKey) {
            kgCurrentDocKey = lastDocKey;
        }
        await refreshKGGraph();
        await loadDocGraphList();
    };
    input.click();
}

async function clearKGGraph() {
    if (!requireWriteAccess()) return;
    try {
        if (kgCurrentDocKey) {
            await api('DELETE', `/knowledge-graph/doc-graph/${encodeURIComponent(kgCurrentDocKey)}`);
        } else {
            await api('DELETE', '/knowledge-graph/nodes/all');
        }
        replaceKGGraphData({ nodes: [], edges: [] });
        kgSelectedNode = null;
        kgHighlightedNodeIds.clear();
        kgHighlightedEdgeIds.clear();
        stopReasoningAnimation();
        drawKGEmpty();
        showToast('图谱已清空', 'success');
    } catch (err) {
        replaceKGGraphData({ nodes: [], edges: [] });
        kgSelectedNode = null;
        kgHighlightedNodeIds.clear();
        kgHighlightedEdgeIds.clear();
        drawKGEmpty();
    }
}

function clearKGChat() {
    const messages = document.getElementById('kg-chat-messages');
    if (messages) {
        messages.innerHTML = `
            <div class="chat-msg assistant">
                <div class="chat-avatar">AI</div>
                <div class="chat-bubble">
                    你好！我是知识图谱问答助手。你可以基于知识图谱向我提问，我会通过实体链接和关系推理来回答你的问题。
                </div>
            </div>`;
    }
}

function exportKGGraph() {
    if (!kgGraphData.nodes.length && !kgGraphData.edges.length) {
        showToast('图谱为空，无法导出', 'warning');
        return;
    }
    const data = {
        nodes: kgGraphData.nodes.map(n => ({
            id: n.id,
            name: n.name,
            type: n.type,
            connections: n.connections,
            description: n.description || '',
        })),
        edges: kgGraphData.edges.map(e => ({
            source: e.source,
            target: e.target,
            type: e.type,
            style: e.style,
            weight: e.weight,
        })),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'knowledge-graph.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast('图谱已导出为 JSON', 'success');
}

function exportKGChat() {
    const messages = document.getElementById('kg-chat-messages');
    if (!messages) return;
    const msgEls = messages.querySelectorAll('.chat-msg');
    if (!msgEls.length) {
        showToast('没有对话可导出', 'warning');
        return;
    }
    let text = '# 知识图谱问答记录\n\n';
    msgEls.forEach(msg => {
        const role = msg.classList.contains('user') ? '用户' : 'AI';
        const content = msg.querySelector('.chat-bubble')?.textContent?.trim() || '';
        text += `**${role}**: ${content}\n\n`;
    });
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'kg-chat-export.md';
    a.click();
    URL.revokeObjectURL(url);
    showToast('对话已导出', 'success');
}

// ==================== 文件夹图谱构建 ====================

async function buildFolderGraph() {
    try {
        const data = await api('GET', '/kb/list');
        const bases = data.bases || [];
        if (bases.length === 0) {
            showToast('请先创建知识库并上传文档', 'warning');
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog';
        overlay.id = 'folder-kg-dialog';
        const options = bases.map(b => `<option value="${b.kb_id}">${escapeHtml(b.name)} (${b.doc_count || 0}文档)</option>`).join('');
        overlay.innerHTML = `
            <div class="dialog-box" style="width:520px">
                <div class="dialog-title">构建文档关系图谱</div>
                <div style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;line-height:1.7">
                    选择知识库，大模型将像人一样阅读每篇文章，提炼<span style="color:#3b82f6;font-weight:600">记忆锚点</span>（核心主题、关键论点、重要实体等），然后通过锚点的相似度和关联度，把相关文章用线条连接起来，形成脉络清晰的文档关系图谱。
                </div>
                <div style="background:var(--bg-secondary);border-radius:8px;padding:12px;margin-bottom:16px">
                    <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">关系类型说明</div>
                    <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:12px">
                        <span style="display:flex;align-items:center;gap:4px"><span style="width:20px;height:2px;background:#3b82f6;display:inline-block"></span><span style="color:#3b82f6">主题相似</span></span>
                        <span style="display:flex;align-items:center;gap:4px"><span style="width:20px;height:2px;background:#f59e0b;border-top:2px dashed #f59e0b;display:inline-block"></span><span style="color:#f59e0b">共享实体</span></span>
                        <span style="display:flex;align-items:center;gap:4px"><span style="width:20px;height:2px;background:#ef4444;display:inline-block"></span><span style="color:#ef4444">因果关联</span></span>
                        <span style="display:flex;align-items:center;gap:4px"><span style="width:20px;height:2px;background:#10b981;border-top:2px dashed #10b981;display:inline-block"></span><span style="color:#10b981">互补关系</span></span>
                    </div>
                </div>
                <div>
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">选择知识库 *</label>
                    <select class="dialog-input" id="folder-kg-kb-select" style="width:100%;padding:8px 12px">${options}</select>
                </div>
                <div class="dialog-actions" style="margin-top:16px">
                    <button class="dialog-btn" onclick="document.getElementById('folder-kg-dialog').remove()">取消</button>
                    <button class="dialog-btn primary" onclick="doBuildFolderGraph()">构建图谱</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
    } catch (err) {
        showToast('获取知识库列表失败: ' + err.message, 'error');
    }
}

async function doBuildFolderGraph() {
    if (!requireWriteAccess()) return;
    const kbId = document.getElementById('folder-kg-kb-select')?.value;
    if (!kbId) {
        showToast('请选择知识库', 'warning');
        return;
    }

    try {
        showToast('正在阅读文档并提炼记忆锚点，请稍候...', 'info');
        const data = await api('POST', `/knowledge-graph/build-folder-graph?kb_id=${encodeURIComponent(kbId)}`, {}, 300000);
        if (data.success) {
            document.getElementById('folder-kg-dialog')?.remove();

            // 显示构建结果摘要
            const resultOverlay = document.createElement('div');
            resultOverlay.className = 'new-item-dialog';
            resultOverlay.id = 'folder-kg-result-dialog';

            const profiles = data.profiles || [];
            const relationTypes = data.relation_types || {};
            const relTypeLabels = {
                thematic_similarity: '主题相似',
                shared_entities: '共享实体',
                causal_link: '因果关联',
                complementary: '互补关系',
                contradicts: '矛盾对立',
            };

            const profileListHtml = profiles.slice(0, 10).map(p => {
                const anchorBadges = (p.anchors || []).slice(0, 5).map(a => {
                    const colors = { core_topic: '#3b82f6', key_argument: '#8b5cf6', key_entity: '#f59e0b', method_conclusion: '#10b981', data_finding: '#ef4444' };
                    const labels = { core_topic: '主题', key_argument: '论点', key_entity: '实体', method_conclusion: '方法/结论', data_finding: '数据' };
                    const c = colors[a.anchor_type] || '#6b7280';
                    const l = labels[a.anchor_type] || '锚点';
                    return `<span style="font-size:11px;padding:1px 6px;border-radius:3px;background:${c}20;color:${c};border:1px solid ${c}40">${l}: ${escapeHtml(a.content.substring(0, 30))}${a.content.length > 30 ? '...' : ''}</span>`;
                }).join('');
                return `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
                    <div style="font-size:13px;font-weight:600;color:var(--text-primary);margin-bottom:4px">${escapeHtml(p.doc_title)}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">${escapeHtml(p.summary || '')}</div>
                    <div style="display:flex;flex-wrap:wrap;gap:4px">${anchorBadges}</div>
                </div>`;
            }).join('');

            const relTypeHtml = Object.entries(relationTypes).map(([type, count]) => {
                const colors = { thematic_similarity: '#3b82f6', shared_entities: '#f59e0b', causal_link: '#ef4444', complementary: '#10b981', contradicts: '#dc2626' };
                const c = colors[type] || '#6b7280';
                return `<span style="font-size:12px;padding:2px 8px;border-radius:4px;background:${c}15;color:${c};border:1px solid ${c}30">${relTypeLabels[type] || type}: ${count}</span>`;
            }).join('');

            resultOverlay.innerHTML = `
                <div class="dialog-box" style="width:600px;max-height:80vh;display:flex;flex-direction:column">
                    <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)">
                        <span style="font-size:16px;font-weight:700">文档关系图谱构建完成</span>
                        <button class="icon-btn sm" onclick="document.getElementById('folder-kg-result-dialog').remove()" title="关闭">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                    </div>
                    <div style="padding:16px;overflow-y:auto;flex:1">
                        <div style="display:flex;gap:16px;margin-bottom:16px">
                            <div style="flex:1;text-align:center;padding:12px;background:var(--bg-secondary);border-radius:8px">
                                <div style="font-size:24px;font-weight:700;color:#3b82f6">${data.document_count || 0}</div>
                                <div style="font-size:12px;color:var(--text-muted)">文档</div>
                            </div>
                            <div style="flex:1;text-align:center;padding:12px;background:var(--bg-secondary);border-radius:8px">
                                <div style="font-size:24px;font-weight:700;color:#8b5cf6">${data.anchor_count || 0}</div>
                                <div style="font-size:12px;color:var(--text-muted)">记忆锚点</div>
                            </div>
                            <div style="flex:1;text-align:center;padding:12px;background:var(--bg-secondary);border-radius:8px">
                                <div style="font-size:24px;font-weight:700;color:#f59e0b">${data.relation_count || 0}</div>
                                <div style="font-size:12px;color:var(--text-muted)">关联关系</div>
                            </div>
                        </div>
                        ${relTypeHtml ? `<div style="margin-bottom:16px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">关系分布</div><div style="display:flex;flex-wrap:wrap;gap:6px">${relTypeHtml}</div></div>` : ''}
                        <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">文档画像${profiles.length > 10 ? '（前10篇）' : ''}</div>
                        <div style="max-height:300px;overflow-y:auto">${profileListHtml}</div>
                    </div>
                    <div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:8px">
                        <button class="dialog-btn" onclick="document.getElementById('folder-kg-result-dialog').remove()">关闭</button>
                        <button class="dialog-btn primary" onclick="document.getElementById('folder-kg-result-dialog').remove();viewFolderGraph('${data.doc_key || ''}')">查看图谱</button>
                    </div>
                </div>`;
            document.body.appendChild(resultOverlay);

            if (data.doc_key) {
                kgCurrentDocKey = data.doc_key;
                const select = document.getElementById('kg-doc-select');
                if (select) {
                    const opt = document.createElement('option');
                    opt.value = data.doc_key;
                    opt.textContent = `${data.doc_key} (${data.node_count}节点/${data.edge_count}边)`;
                    select.appendChild(opt);
                    select.value = data.doc_key;
                }
                await refreshKGGraph(data.doc_key);
            }
        }
    } catch (err) {
        showToast('文档关系图谱构建失败: ' + err.message, 'error');
    }
}

async function viewFolderGraph(docKey) {
    if (!docKey) return;
    kgCurrentDocKey = docKey;
    const select = document.getElementById('kg-doc-select');
    if (select) select.value = docKey;
    await refreshKGGraph(docKey);
}

// ==================== 节点点击预览内容 ====================

async function previewNodeContent(nodeId) {
    try {
        const docKey = kgCurrentDocKey || '';
        const data = await api('GET', `/knowledge-graph/node-content/${encodeURIComponent(nodeId)}?doc_key=${encodeURIComponent(docKey)}`);
        if (!data.success) {
            showToast(data.message || '获取节点内容失败', 'warning');
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog';
        overlay.id = 'node-preview-dialog';

        const typeColor = ENTITY_COLORS[data.node_type] || ENTITY_COLORS.default;
        const relatedHtml = (data.related_nodes || []).map(r => {
            const rColor = ENTITY_COLORS[r.type] || ENTITY_COLORS.default;
            const dir = r.direction === 'outgoing' ? '→' : '←';
            return `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;background:var(--bg-secondary);font-size:12px;margin:2px">
                <span style="color:${rColor}">${dir} ${escapeHtml(r.relation)}</span>
                <span style="color:var(--text-primary)">${escapeHtml(r.label)}</span>
            </span>`;
        }).join('');

        let contentHtml = '';
        if (data.full_content) {
            contentHtml = `<div style="max-height:400px;overflow-y:auto;padding:12px;background:var(--bg-secondary);border-radius:8px;font-size:13px;line-height:1.7" class="md-content">${renderMarkdown(data.full_content)}</div>`;
        } else if (data.content_preview) {
            contentHtml = `<div style="max-height:300px;overflow-y:auto;padding:12px;background:var(--bg-secondary);border-radius:8px;font-size:13px;line-height:1.7" class="md-content">${renderMarkdown(data.content_preview)}</div>`;
        } else if (data.properties?.description) {
            contentHtml = `<div style="padding:12px;background:var(--bg-secondary);border-radius:8px;font-size:13px;line-height:1.7">${escapeHtml(data.properties.description)}</div>`;
        } else {
            contentHtml = '<div style="padding:12px;color:var(--text-muted);font-size:13px">该节点暂无关联内容</div>';
        }

        overlay.innerHTML = `
            <div class="dialog-box" style="width:640px;max-height:80vh;display:flex;flex-direction:column">
                <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)">
                    <div style="display:flex;align-items:center;gap:10px">
                        <span style="width:14px;height:14px;border-radius:50%;background:${typeColor};box-shadow:0 0 8px ${typeColor}40"></span>
                        <span style="font-size:16px;font-weight:700;color:var(--text-primary)">${escapeHtml(data.label)}</span>
                        <span style="font-size:12px;color:${typeColor};background:${typeColor}15;padding:2px 8px;border-radius:4px">${escapeHtml(data.node_type)}</span>
                    </div>
                    <button class="icon-btn sm" onclick="document.getElementById('node-preview-dialog').remove()" title="关闭">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>
                <div style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px">
                    ${contentHtml}
                    ${relatedHtml ? `<div>
                        <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:6px">关联节点</div>
                        <div style="display:flex;flex-wrap:wrap;gap:4px">${relatedHtml}</div>
                    </div>` : ''}
                </div>
            </div>`;
        document.body.appendChild(overlay);
    } catch (err) {
        showToast('获取节点内容失败: ' + err.message, 'error');
    }
}

// ==================== 问答面板收起/放出 ====================

let kgRightPanelCollapsed = false;
let ragRightPanelCollapsed = false;

function toggleKGRightPanel() {
    const rightPanel = document.getElementById('kg-right-panel');
    const resizer = document.getElementById('kg-resizer');
    if (!rightPanel) return;

    kgRightPanelCollapsed = !kgRightPanelCollapsed;
    if (kgRightPanelCollapsed) {
        rightPanel.dataset.prevWidth = rightPanel.style.width || rightPanel.offsetWidth + 'px';
        rightPanel.style.width = '40px';
        rightPanel.style.minWidth = '40px';
        rightPanel.style.overflow = 'hidden';
        if (resizer) resizer.style.display = 'none';
        rightPanel.classList.add('panel-collapsed');
    } else {
        rightPanel.style.width = rightPanel.dataset.prevWidth || '340px';
        rightPanel.style.minWidth = '';
        rightPanel.style.overflow = '';
        if (resizer) resizer.style.display = '';
        rightPanel.classList.remove('panel-collapsed');
    }
    if (kgCanvas) resizeKGCanvas();
}

function toggleRAGRightPanel() {
    const rightPanel = document.getElementById('rag-right-panel');
    const resizer = document.getElementById('rag-resizer');
    if (!rightPanel) return;

    ragRightPanelCollapsed = !ragRightPanelCollapsed;
    if (ragRightPanelCollapsed) {
        rightPanel.dataset.prevWidth = rightPanel.style.width || rightPanel.offsetWidth + 'px';
        rightPanel.style.width = '40px';
        rightPanel.style.minWidth = '40px';
        rightPanel.style.overflow = 'hidden';
        if (resizer) resizer.style.display = 'none';
        rightPanel.classList.add('panel-collapsed');
    } else {
        rightPanel.style.width = rightPanel.dataset.prevWidth || '340px';
        rightPanel.style.minWidth = '';
        rightPanel.style.overflow = '';
        if (resizer) resizer.style.display = '';
        rightPanel.classList.remove('panel-collapsed');
    }
}

Object.assign(window, {
    initKGPage,
    refreshKGGraph,
    resizeKGCanvas,
    openKGGraphPage,
    closeKGGraphPage,
    kgZoomIn,
    kgZoomOut,
    kgResetView,
    sendKGQuery,
    kgChatKeyDown,
    exportKGChat,
    clearKGChat,
    toggleKGRightPanel,
    toggleRAGRightPanel,
});

bindKGGlobalEvents();

