/**
 * 全局搜索与快速跳转 - P1: Command Palette
 * 支持 Ctrl+K 唤起，搜索文件、Wiki页面、知识图谱节点、RAG历史
 */

const CommandPalette = {
    _visible: false,
    _input: null,
    _list: null,
    _overlay: null,
    _selectedIndex: 0,
    _results: [],
    _allItems: [],

    init() {
        if (this._overlay) return;
        this._buildUI();
        this._bindEvents();
        this._loadIndex();
    },

    _buildUI() {
        const overlay = document.createElement('div');
        overlay.id = 'cmd-palette-overlay';
        overlay.style.cssText = 'display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.5);backdrop-filter:blur(4px);';
        overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hide(); });

        const dialog = document.createElement('div');
        dialog.id = 'cmd-palette-dialog';
        dialog.style.cssText = 'position:absolute;top:15%;left:50%;transform:translateX(-50%);width:560px;max-width:90vw;background:var(--bg-surface,#fff);border:1px solid var(--border,#e0e3ea);border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.3);overflow:hidden;';

        const inputWrap = document.createElement('div');
        inputWrap.style.cssText = 'display:flex;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border,#e0e3ea);gap:10px;';
        inputWrap.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted,#94a3b8)" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

        const input = document.createElement('input');
        input.id = 'cmd-palette-input';
        input.type = 'text';
        input.placeholder = '搜索文件、页面、节点、问答...';
        input.style.cssText = 'flex:1;border:none;outline:none;font-size:15px;background:transparent;color:var(--text-primary,#1e293b);';
        input.setAttribute('autocomplete', 'off');
        inputWrap.appendChild(input);
        dialog.appendChild(inputWrap);

        const list = document.createElement('div');
        list.id = 'cmd-palette-list';
        list.style.cssText = 'max-height:380px;overflow-y:auto;padding:4px;';
        dialog.appendChild(list);

        const footer = document.createElement('div');
        footer.style.cssText = 'padding:8px 16px;border-top:1px solid var(--border,#e0e3ea);display:flex;gap:16px;font-size:11px;color:var(--text-muted,#94a3b8);';
        footer.innerHTML = '<span>↑↓ 导航</span><span>Enter 跳转</span><span>Esc 关闭</span>';
        dialog.appendChild(footer);

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        this._overlay = overlay;
        this._input = input;
        this._list = list;
    },

    _bindEvents() {
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                this.toggle();
            }
            if (e.key === 'Escape' && this._visible) {
                e.preventDefault();
                this.hide();
            }
        });

        this._input.addEventListener('input', () => {
            this._search(this._input.value);
        });

        this._input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this._selectedIndex = Math.min(this._selectedIndex + 1, this._results.length - 1);
                this._renderList();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this._selectedIndex = Math.max(this._selectedIndex - 1, 0);
                this._renderList();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                this._selectItem(this._selectedIndex);
            }
        });
    },

    async _loadIndex() {
        this._allItems = [];
        // 加载本地文件
        try {
            if (typeof LocalDB !== 'undefined') {
                const files = await LocalDB.getAllFiles();
                if (files) {
                    files.forEach(f => {
                        this._allItems.push({
                            type: 'file',
                            icon: '📄',
                            label: f.path || f.name,
                            path: f.path,
                            action: () => { if (typeof selectFile === 'function') selectFile(f.path); }
                        });
                    });
                }
            }
        } catch (e) { /* ignore */ }

        // 加载 Wiki 页面
        try {
            if (typeof wikiApi === 'function') {
                const data = await wikiApi('GET', '/pages');
                if (data && data.pages) {
                    data.pages.forEach(p => {
                        this._allItems.push({
                            type: 'wiki',
                            icon: '📝',
                            label: p.title,
                            page_id: p.page_id,
                            action: () => {
                                if (typeof navigateTo === 'function') navigateTo('wiki');
                                setTimeout(() => { if (typeof loadWikiPage === 'function') loadWikiPage(p.page_id); }, 200);
                            }
                        });
                    });
                }
            }
        } catch (e) { /* ignore */ }

        // 加载知识图谱节点
        try {
            if (typeof api === 'function') {
                const data = await api('GET', '/knowledge-graph/nodes');
                if (data && data.nodes) {
                    data.nodes.forEach(n => {
                        this._allItems.push({
                            type: 'kg-node',
                            icon: '🔗',
                            label: n.label || n.id,
                            node_id: n.id,
                            action: () => {
                                if (typeof navigateTo === 'function') navigateTo('kg');
                                setTimeout(() => { if (typeof focusKGNode === 'function') focusKGNode(n.id); }, 200);
                            }
                        });
                    });
                }
            }
        } catch (e) { /* ignore */ }

        // 加载知识库列表
        try {
            if (typeof api === 'function') {
                const data = await api('GET', '/kb/list');
                if (data && data.knowledge_bases) {
                    data.knowledge_bases.forEach(kb => {
                        this._allItems.push({
                            type: 'kb',
                            icon: '📚',
                            label: kb.name,
                            kb_id: kb.kb_id,
                            action: () => {
                                if (typeof navigateTo === 'function') navigateTo('rag');
                                setTimeout(() => { if (typeof setRAGKnowledgeBase === 'function') setRAGKnowledgeBase(kb.kb_id); }, 200);
                            }
                        });
                    });
                }
            }
        } catch (e) { /* ignore */ }

        // 添加导航命令
        const navCommands = [
            { type: 'nav', icon: '🌐', label: '知识图谱', action: () => navigateTo('kg') },
            { type: 'nav', icon: '💬', label: '知识库问答', action: () => navigateTo('rag') },
            { type: 'nav', icon: '📖', label: '知识 Wiki', action: () => navigateTo('wiki') },
            { type: 'nav', icon: '🔄', label: '格式转化', action: () => { if (typeof openConvertDialog === 'function') openConvertDialog(); } },
            { type: 'nav', icon: '📁', label: '打开文件夹', action: () => { if (typeof openLocalFolder === 'function') openLocalFolder(); } },
            { type: 'nav', icon: '📤', label: '上传到服务器', action: () => { if (typeof uploadToServer === 'function') uploadToServer(); } },
            { type: 'nav', icon: '🎨', label: '切换主题', action: () => { const s = window.AppStore; if (s) s.theme = 'light'; document.documentElement.removeAttribute('data-theme'); } },
        ];
        this._allItems = [...navCommands, ...this._allItems];
    },

    _search(query) {
        if (!query || !query.trim()) {
            this._results = this._allItems.slice(0, 20);
        } else {
            const q = query.toLowerCase().trim();
            this._results = this._allItems.filter(item => {
                const label = (item.label || '').toLowerCase();
                return label.includes(q) || this._pinyinMatch(label, q);
            }).slice(0, 20);
        }
        this._selectedIndex = 0;
        this._renderList();
    },

    _pinyinMatch(text, query) {
        // 简单拼音首字母匹配
        const pinyinMap = {'啊':'a','吧':'b','才':'c','大':'d','额':'e','发':'f','该':'g','还':'h','就':'j','可':'k','了':'l','吗':'m','那':'n','哦':'o','怕':'p','去':'q','人':'r','是':'s','他':'t','我':'w','小':'x','有':'y','在':'z'};
        const firstLetters = text.split('').map(c => pinyinMap[c] || c).join('');
        return firstLetters.includes(query);
    },

    _renderList() {
        if (!this._list) return;
        const typeLabels = { file: '文件', wiki: 'Wiki', 'kg-node': '图谱节点', kb: '知识库', nav: '命令' };
        let html = '';
        this._results.forEach((item, i) => {
            const selected = i === this._selectedIndex;
            const bg = selected ? 'var(--primary-light,rgba(79,70,229,0.08))' : 'transparent';
            const typeLabel = typeLabels[item.type] || '';
            html += `<div class="cmd-item" data-index="${i}" style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:6px;cursor:pointer;background:${bg};transition:background 0.1s;" onmouseenter="CommandPalette._hoverItem(${i})" onclick="CommandPalette._selectItem(${i})">
                <span style="font-size:16px;flex-shrink:0;">${item.icon}</span>
                <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-primary,#1e293b);font-size:14px;">${this._highlight(item.label, this._input.value)}</span>
                <span style="font-size:11px;color:var(--text-muted,#94a3b8);background:var(--bg-elevated,#f0f2f5);padding:1px 6px;border-radius:4px;">${typeLabel}</span>
            </div>`;
        });
        if (this._results.length === 0) {
            html = '<div style="padding:24px;text-align:center;color:var(--text-muted,#94a3b8);font-size:14px;">无匹配结果</div>';
        }
        this._list.innerHTML = html;
    },

    _highlight(text, query) {
        if (!query || !query.trim()) return escapeHtml(text);
        const q = query.trim();
        const regex = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        return escapeHtml(text).replace(regex, '<mark style="background:var(--accent-light,rgba(217,119,6,0.2));color:inherit;border-radius:2px;padding:0 1px;">$1</mark>');
    },

    _hoverItem(index) {
        this._selectedIndex = index;
        this._renderList();
    },

    _selectItem(index) {
        const item = this._results[index];
        if (item && item.action) {
            this.hide();
            try { item.action(); } catch (e) { console.error('CommandPalette action error:', e); }
        }
    },

    toggle() {
        if (this._visible) this.hide();
        else this.show();
    },

    show() {
        this.init();
        this._visible = true;
        this._overlay.style.display = 'block';
        this._input.value = '';
        this._search('');
        setTimeout(() => this._input.focus(), 50);
        // 刷新索引
        this._loadIndex();
    },

    hide() {
        this._visible = false;
        if (this._overlay) this._overlay.style.display = 'none';
    }
};

window.CommandPalette = CommandPalette;
