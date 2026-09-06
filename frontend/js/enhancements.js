/**
 * P12-P18 综合增强模块
 * P12: 快捷键系统
 * P15: 移动端适配
 * P17: 插件/扩展机制
 * P18: 知识地图与可视化概览
 */

const Enhancements = {

    // ==================== P12: 快捷键系统 ====================
    shortcuts: {
        _registered: {},
        _helpVisible: false,

        init() {
            document.addEventListener('keydown', (e) => {
                // 忽略输入框中的快捷键（除了特定全局快捷键）
                const tag = e.target.tagName;
                const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
                const key = this._buildKey(e);
                const handler = this._registered[key];
                if (handler) {
                    if (isInput && !handler.global) return;
                    e.preventDefault();
                    handler.fn();
                }
            });
            // 注册默认快捷键
            this.register('Ctrl+K', () => CommandPalette.toggle(), '全局搜索', true);
            this.register('Ctrl+/', () => this.toggleHelp(), '快捷键帮助', true);
            this.register('Ctrl+1', () => navigateTo('files'), '文件管理', true);
            this.register('Ctrl+2', () => navigateTo('kg'), '知识图谱', true);
            this.register('Ctrl+3', () => navigateTo('rag'), '知识问答', true);
            this.register('Ctrl+4', () => navigateTo('wiki'), '知识 Wiki', true);
            this.register('Ctrl+S', () => { if (typeof saveCurrentWikiPage === 'function') saveCurrentWikiPage(); }, '保存当前页面', false);
            this.register('Ctrl+N', () => { if (typeof createNewWikiPage === 'function') createNewWikiPage(); }, '新建 Wiki 页面', true);
            this.register('Ctrl+,', () => { if (typeof openSettings === 'function') openSettings(); }, '设置', true);
            this.register('Ctrl+Shift+D', () => { const s = window.AppStore; if (s) s.theme = 'light'; document.documentElement.removeAttribute('data-theme'); }, '切换主题', true);
        },

        register(keyCombo, fn, description, global) {
            this._registered[keyCombo.toLowerCase()] = { fn, description, global: !!global };
        },

        _buildKey(e) {
            const parts = [];
            if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
            if (e.shiftKey) parts.push('Shift');
            if (e.altKey) parts.push('Alt');
            parts.push(e.key);
            return parts.join('+').toLowerCase();
        },

        toggleHelp() {
            if (this._helpVisible) {
                const el = document.getElementById('shortcut-help');
                if (el) el.remove();
                this._helpVisible = false;
                return;
            }
            const overlay = document.createElement('div');
            overlay.id = 'shortcut-help';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
            overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); this._helpVisible = false; } };
            const dialog = document.createElement('div');
            dialog.style.cssText = 'background:var(--bg-surface,#fff);border-radius:12px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto;';
            let html = '<h3 style="margin-bottom:16px;color:var(--text-primary,#1e293b);">快捷键</h3>';
            for (const [key, handler] of Object.entries(this._registered)) {
                html += `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border,#e0e3ea);">
                    <span style="color:var(--text-secondary,#5a5f78);">${handler.description}</span>
                    <kbd style="background:var(--bg-elevated,#f0f2f5);padding:2px 8px;border-radius:4px;font-size:12px;font-family:var(--font-mono,monospace);">${key}</kbd>
                </div>`;
            }
            html += '<div style="margin-top:16px;text-align:center;color:var(--text-muted,#94a3b8);font-size:12px;">按 Esc 或点击外部关闭</div>';
            dialog.innerHTML = html;
            overlay.appendChild(dialog);
            document.body.appendChild(overlay);
            this._helpVisible = true;
        }
    },

    // ==================== P15: 移动端适配 ====================
    mobile: {
        init() {
            this._addViewportMeta();
            this._addMobileStyles();
            this._setupTouchGestures();
            this._handleResize();
            window.addEventListener('resize', () => this._handleResize());
        },

        _addViewportMeta() {
            if (!document.querySelector('meta[name="viewport"]')) {
                const meta = document.createElement('meta');
                meta.name = 'viewport';
                meta.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no';
                document.head.appendChild(meta);
            }
        },

        _addMobileStyles() {
            const style = document.createElement('style');
            style.textContent = `
                @media (max-width: 768px) {
                    .sidebar { width: 100% !important; position: fixed !important; z-index: 100; transform: translateX(-100%); transition: transform 0.3s ease; }
                    .sidebar.open { transform: translateX(0); }
                    .main-content { margin-left: 0 !important; width: 100% !important; }
                    .mobile-toggle { display: block !important; }
                    #cmd-palette-dialog { width: 95vw !important; top: 10% !important; }
                    .chat-input-area { padding: 8px !important; }
                    .chat-input-area textarea { font-size: 16px !important; }
                }
                .mobile-toggle { display: none; position: fixed; top: 8px; left: 8px; z-index: 101; width: 36px; height: 36px; border-radius: 8px; background: var(--primary); color: #fff; border: none; cursor: pointer; font-size: 18px; }
            `;
            document.head.appendChild(style);
            // 添加移动端菜单按钮
            if (!document.querySelector('.mobile-toggle')) {
                const btn = document.createElement('button');
                btn.className = 'mobile-toggle';
                btn.innerHTML = '☰';
                btn.onclick = () => {
                    const sidebar = document.querySelector('.sidebar');
                    if (sidebar) sidebar.classList.toggle('open');
                };
                document.body.appendChild(btn);
            }
        },

        _setupTouchGestures() {
            let startX = 0;
            document.addEventListener('touchstart', (e) => { startX = e.touches[0].clientX; });
            document.addEventListener('touchend', (e) => {
                const diff = e.changedTouches[0].clientX - startX;
                const sidebar = document.querySelector('.sidebar');
                if (!sidebar) return;
                if (diff > 80 && startX < 40) sidebar.classList.add('open');
                if (diff < -80) sidebar.classList.remove('open');
            });
        },

        _handleResize() {
            document.body.classList.toggle('is-mobile', window.innerWidth <= 768);
        }
    },

    // ==================== P17: 插件/扩展机制 ====================
    plugins: {
        _registry: {},

        register(name, plugin) {
            this._registry[name] = plugin;
            if (plugin.init) {
                try { plugin.init(); } catch (e) { console.error(`Plugin ${name} init error:`, e); }
            }
        },

        unregister(name) {
            const plugin = this._registry[name];
            if (plugin && plugin.destroy) plugin.destroy();
            delete this._registry[name];
        },

        list() {
            return Object.entries(this._registry).map(([name, p]) => ({
                name,
                version: p.version || '1.0.0',
                description: p.description || '',
                enabled: true,
            }));
        },

        // Hook 系统
        _hooks: {},
        addHook(hookName, fn, priority) {
            if (!this._hooks[hookName]) this._hooks[hookName] = [];
            this._hooks[hookName].push({ fn, priority: priority || 10 });
            this._hooks[hookName].sort((a, b) => a.priority - b.priority);
        },

        async applyHook(hookName, data) {
            let result = data;
            const hooks = this._hooks[hookName] || [];
            for (const hook of hooks) {
                try { result = await hook.fn(result); } catch (e) { console.error(`Hook ${hookName} error:`, e); }
            }
            return result;
        }
    },

    // ==================== P18: 知识地图与可视化概览 ====================
    knowledgeMap: {
        _canvas: null,
        _ctx: null,
        _nodes: [],
        _edges: [],
        _animFrame: null,

        async show() {
            // 创建全屏覆盖
            const overlay = document.createElement('div');
            overlay.id = 'knowledge-map-overlay';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:99998;background:var(--bg-base,#f5f6f8);';
            overlay.innerHTML = `
                <div style="position:absolute;top:16px;left:16px;z-index:1;display:flex;gap:8px;">
                    <button onclick="document.getElementById('knowledge-map-overlay').remove()" style="padding:6px 16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-surface);color:var(--text-primary);cursor:pointer;">关闭</button>
                    <span style="padding:6px 16px;background:var(--bg-surface);border-radius:8px;color:var(--text-secondary);font-size:13px;" id="map-stats">加载中...</span>
                </div>
                <canvas id="knowledge-map-canvas" style="width:100%;height:100%;"></canvas>
            `;
            document.body.appendChild(overlay);

            this._canvas = document.getElementById('knowledge-map-canvas');
            this._ctx = this._canvas.getContext('2d');
            this._resize();
            window.addEventListener('resize', () => this._resize());

            await this._loadData();
            this._animate();
        },

        _resize() {
            if (!this._canvas) return;
            const dpr = window.devicePixelRatio || 1;
            this._canvas.width = window.innerWidth * dpr;
            this._canvas.height = window.innerHeight * dpr;
            this._ctx.scale(dpr, dpr);
        },

        async _loadData() {
            try {
                const [nodesData, edgesData] = await Promise.all([
                    typeof api === 'function' ? api('GET', '/knowledge-graph/nodes?limit=100') : Promise.resolve({ nodes: [] }),
                    typeof api === 'function' ? api('GET', '/knowledge-graph/edges?limit=200') : Promise.resolve({ edges: [] }),
                ]);
                this._nodes = (nodesData.nodes || []).map((n, i) => ({
                    ...n,
                    x: Math.random() * (window.innerWidth - 100) + 50,
                    y: Math.random() * (window.innerHeight - 100) + 50,
                    vx: 0, vy: 0,
                    radius: Math.max(8, Math.min(30, (n.edge_count || 1) * 3)),
                }));
                this._edges = edgesData.edges || [];
                const statsEl = document.getElementById('map-stats');
                if (statsEl) statsEl.textContent = `${this._nodes.length} 个节点 · ${this._edges.length} 条关系`;
            } catch (e) {
                this._nodes = [];
                this._edges = [];
            }
        },

        _animate() {
            if (!document.getElementById('knowledge-map-overlay')) return;
            this._forceLayout();
            this._draw();
            this._animFrame = requestAnimationFrame(() => this._animate());
        },

        _forceLayout() {
            const w = window.innerWidth, h = window.innerHeight;
            const nodes = this._nodes;
            // 斥力
            for (let i = 0; i < nodes.length; i++) {
                for (let j = i + 1; j < nodes.length; j++) {
                    const dx = nodes[j].x - nodes[i].x;
                    const dy = nodes[j].y - nodes[i].y;
                    const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
                    const force = 500 / (dist * dist);
                    const fx = (dx / dist) * force;
                    const fy = (dy / dist) * force;
                    nodes[i].vx -= fx; nodes[i].vy -= fy;
                    nodes[j].vx += fx; nodes[j].vy += fy;
                }
            }
            // 引力（边）
            for (const edge of this._edges) {
                const src = nodes.find(n => n.id === edge.source);
                const tgt = nodes.find(n => n.id === edge.target);
                if (!src || !tgt) continue;
                const dx = tgt.x - src.x;
                const dy = tgt.y - src.y;
                const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
                const force = (dist - 120) * 0.01;
                const fx = (dx / dist) * force;
                const fy = (dy / dist) * force;
                src.vx += fx; src.vy += fy;
                tgt.vx -= fx; tgt.vy -= fy;
            }
            // 居中引力
            for (const n of nodes) {
                n.vx += (w / 2 - n.x) * 0.001;
                n.vy += (h / 2 - n.y) * 0.001;
                n.vx *= 0.9; n.vy *= 0.9;
                n.x += n.vx; n.y += n.vy;
                n.x = Math.max(30, Math.min(w - 30, n.x));
                n.y = Math.max(30, Math.min(h - 30, n.y));
            }
        },

        _draw() {
            const ctx = this._ctx;
            const w = window.innerWidth, h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            // 绘制边
            ctx.strokeStyle = 'var(--border,#e0e3ea)';
            ctx.lineWidth = 1;
            for (const edge of this._edges) {
                const src = this._nodes.find(n => n.id === edge.source);
                const tgt = this._nodes.find(n => n.id === edge.target);
                if (!src || !tgt) continue;
                ctx.beginPath();
                ctx.moveTo(src.x, src.y);
                ctx.lineTo(tgt.x, tgt.y);
                ctx.globalAlpha = 0.3;
                ctx.stroke();
                ctx.globalAlpha = 1;
            }

            // 绘制节点
            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            for (const n of this._nodes) {
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
                ctx.fillStyle = isDark ? '#818cf8' : '#4f46e5';
                ctx.globalAlpha = 0.8;
                ctx.fill();
                ctx.globalAlpha = 1;
                // 标签
                ctx.fillStyle = isDark ? '#e2e4f0' : '#1a1d2e';
                ctx.font = '11px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText(n.label || n.id, n.x, n.y + n.radius + 14);
            }
        }
    },

    // 全局初始化
    init() {
        this.shortcuts.init();
        this.mobile.init();
    }
};

window.Enhancements = Enhancements;
