const WikiEditor = {
    mode: 'markdown',
    previewActive: false,

    init(containerId, initialContent) {
        this.containerId = containerId;
        this.content = initialContent || '';
        this.render();
    },

    render() {
        const container = document.getElementById(this.containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="wiki-editor-wrapper" style="display:flex;flex-direction:column;height:100%">
                <div class="wiki-editor-toolbar" style="display:flex;align-items:center;gap:4px;padding:6px 8px;border-bottom:1px solid var(--border);background:var(--bg-secondary);flex-wrap:wrap">
                    <div style="display:flex;gap:2px;margin-right:8px">
                        <button class="editor-mode-btn ${this.mode === 'markdown' ? 'active' : ''}" onclick="WikiEditor.switchMode('markdown')" title="Markdown编辑器" style="padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:3px;cursor:pointer;background:${this.mode === 'markdown' ? 'var(--primary)' : 'var(--bg-primary)'};color:${this.mode === 'markdown' ? '#fff' : 'var(--text-primary)'}">Markdown</button>
                        <button class="editor-mode-btn ${this.mode === 'richtext' ? 'active' : ''}" onclick="WikiEditor.switchMode('richtext')" title="富文本编辑器" style="padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:3px;cursor:pointer;background:${this.mode === 'richtext' ? 'var(--primary)' : 'var(--bg-primary)'};color:${this.mode === 'richtext' ? '#fff' : 'var(--text-primary)'}">富文本</button>
                    </div>
                    <div style="width:1px;height:16px;background:var(--border);margin:0 4px"></div>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('bold')" title="加粗" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-weight:bold;font-size:14px">B</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('italic')" title="斜体" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-style:italic;font-size:14px">I</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('strike')" title="删除线" style="padding:4px 6px;border:none;background:none;cursor:pointer;text-decoration:line-through;font-size:14px">S</button>
                    <div style="width:1px;height:16px;background:var(--border);margin:0 4px"></div>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('h1')" title="标题1" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:12px">H1</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('h2')" title="标题2" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:12px">H2</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('h3')" title="标题3" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:12px">H3</button>
                    <div style="width:1px;height:16px;background:var(--border);margin:0 4px"></div>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('ul')" title="无序列表" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">•</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('ol')" title="有序列表" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">1.</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('quote')" title="引用" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">❝</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('code')" title="代码" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">&lt;/&gt;</button>
                    <div style="width:1px;height:16px;background:var(--border);margin:0 4px"></div>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('link')" title="链接" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">🔗</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('image')" title="图片" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">🖼️</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('table')" title="表格" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">📊</button>
                    <button class="editor-tool-btn" onclick="WikiEditor.insertFormat('hr')" title="分割线" style="padding:4px 6px;border:none;background:none;cursor:pointer;font-size:14px">—</button>
                    <div style="flex:1"></div>
                    <button class="editor-tool-btn" onclick="WikiEditor.togglePreview()" title="预览" style="padding:4px 8px;border:1px solid var(--border);border-radius:3px;cursor:pointer;background:${this.previewActive ? 'var(--primary)' : 'var(--bg-primary)'};color:${this.previewActive ? '#fff' : 'var(--text-primary)'};font-size:12px">👁 预览</button>
                </div>
                <div class="wiki-editor-body" style="flex:1;display:flex;overflow:hidden">
                    <div id="wiki-editor-input-wrap" style="flex:1;display:${this.previewActive ? 'none' : 'flex'};flex-direction:column">
                        <textarea id="wiki-editor-textarea" style="flex:1;width:100%;padding:12px;font-family:'Consolas','Monaco','Courier New',monospace;font-size:14px;line-height:1.6;border:none;outline:none;resize:none;background:var(--bg-primary);color:var(--text-primary)" placeholder="开始编写Wiki内容...">${escapeHtml(this.content)}</textarea>
                    </div>
                    <div id="wiki-editor-preview-wrap" style="flex:1;display:${this.previewActive ? 'block' : 'none'};padding:16px;overflow-y:auto;background:var(--bg-primary)">
                        <div id="wiki-editor-preview" class="wiki-preview-content"></div>
                    </div>
                </div>
                <div class="wiki-editor-status" style="display:flex;justify-content:space-between;padding:4px 8px;border-top:1px solid var(--border);font-size:11px;color:var(--text-muted);background:var(--bg-secondary)">
                    <span id="wiki-editor-mode-label">${this.mode === 'markdown' ? 'Markdown' : '富文本'} 模式</span>
                    <span id="wiki-editor-stats">字数: ${(this.content || '').length}</span>
                </div>
            </div>
        `;

        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) {
            textarea.addEventListener('input', () => {
                this.content = textarea.value;
                const stats = document.getElementById('wiki-editor-stats');
                if (stats) stats.textContent = `字数: ${this.content.length}`;
                if (this.previewActive) this.updatePreview();
            });
            textarea.addEventListener('keydown', (e) => {
                if (e.key === 'Tab') {
                    e.preventDefault();
                    const start = textarea.selectionStart;
                    const end = textarea.selectionEnd;
                    textarea.value = textarea.value.substring(0, start) + '    ' + textarea.value.substring(end);
                    textarea.selectionStart = textarea.selectionEnd = start + 4;
                    this.content = textarea.value;
                }
            });
        }

        if (this.previewActive) this.updatePreview();
    },

    switchMode(mode) {
        if (this.mode === mode) return;
        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) this.content = textarea.value;

        if (mode === 'richtext' && this.mode === 'markdown') {
            this.mode = 'richtext';
            this.render();
        } else if (mode === 'markdown' && this.mode === 'richtext') {
            this.mode = 'markdown';
            this.render();
        }
    },

    togglePreview() {
        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) this.content = textarea.value;
        this.previewActive = !this.previewActive;
        this.render();
    },

    updatePreview() {
        const preview = document.getElementById('wiki-editor-preview');
        if (!preview) return;
        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) this.content = textarea.value;
        preview.innerHTML = this.renderMarkdown(this.content);
    },

    getContent() {
        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) return textarea.value;
        return this.content;
    },

    setContent(content) {
        this.content = content;
        const textarea = document.getElementById('wiki-editor-textarea');
        if (textarea) textarea.value = content;
        const stats = document.getElementById('wiki-editor-stats');
        if (stats) stats.textContent = `字数: ${content.length}`;
        if (this.previewActive) this.updatePreview();
    },

    insertAtCursor(content) {
        const textarea = document.getElementById('wiki-editor-textarea');
        if (!textarea) return;
        const start = textarea.selectionStart ?? textarea.value.length;
        const end = textarea.selectionEnd ?? start;
        textarea.value = textarea.value.slice(0, start) + content + textarea.value.slice(end);
        textarea.selectionStart = textarea.selectionEnd = start + content.length;
        this.content = textarea.value;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    },

    insertFormat(type) {
        const textarea = document.getElementById('wiki-editor-textarea');
        if (!textarea) return;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const selected = textarea.value.substring(start, end);
        let insert = '';
        let cursorOffset = 0;

        switch (type) {
            case 'bold':
                insert = `**${selected || '粗体文本'}**`;
                cursorOffset = selected ? insert.length : 2;
                break;
            case 'italic':
                insert = `*${selected || '斜体文本'}*`;
                cursorOffset = selected ? insert.length : 1;
                break;
            case 'strike':
                insert = `~~${selected || '删除线文本'}~~`;
                cursorOffset = selected ? insert.length : 2;
                break;
            case 'h1':
                insert = `# ${selected || '标题1'}`;
                cursorOffset = insert.length;
                break;
            case 'h2':
                insert = `## ${selected || '标题2'}`;
                cursorOffset = insert.length;
                break;
            case 'h3':
                insert = `### ${selected || '标题3'}`;
                cursorOffset = insert.length;
                break;
            case 'ul':
                insert = `- ${selected || '列表项'}`;
                cursorOffset = insert.length;
                break;
            case 'ol':
                insert = `1. ${selected || '列表项'}`;
                cursorOffset = insert.length;
                break;
            case 'quote':
                insert = `> ${selected || '引用内容'}`;
                cursorOffset = insert.length;
                break;
            case 'code':
                if (selected.includes('\n')) {
                    insert = `\`\`\`\n${selected}\n\`\`\``;
                } else {
                    insert = `\`${selected || '代码'}\``;
                }
                cursorOffset = insert.length;
                break;
            case 'link':
                insert = `[${selected || '链接文本'}](url)`;
                cursorOffset = selected ? insert.length : 1;
                break;
            case 'image':
                insert = `![${selected || '图片描述'}](url)`;
                cursorOffset = selected ? insert.length : 2;
                break;
            case 'table':
                insert = `\n| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n| 内容 | 内容 | 内容 |\n`;
                cursorOffset = insert.length;
                break;
            case 'hr':
                insert = `\n---\n`;
                cursorOffset = insert.length;
                break;
        }

        textarea.value = textarea.value.substring(0, start) + insert + textarea.value.substring(end);
        textarea.selectionStart = textarea.selectionEnd = start + cursorOffset;
        textarea.focus();
        this.content = textarea.value;
        const stats = document.getElementById('wiki-editor-stats');
        if (stats) stats.textContent = `字数: ${this.content.length}`;
    },

    renderMarkdown(text) {
        if (!text) return '';
        let html = escapeHtml(text);

        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');
        html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-secondary);padding:2px 4px;border-radius:3px;font-size:0.9em">$1</code>');

        html = html.replace(/^&gt; (.+)$/gm, '<blockquote style="border-left:3px solid var(--primary);padding:4px 12px;margin:4px 0;color:var(--text-secondary)">$1</blockquote>');

        html = html.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:12px 0">');

        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:var(--primary)">$1</a>');
        html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" style="max-width:100%;border-radius:4px">');

        html = html.replace(/^- (.+)$/gm, '<li style="margin-left:16px">$1</li>');
        html = html.replace(/^\d+\. (.+)$/gm, '<li style="margin-left:16px;list-style-type:decimal">$1</li>');

        html = html.replace(/\n\n/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');
        html = '<p>' + html + '</p>';

        html = html.replace(/<p><\/p>/g, '');
        html = html.replace(/<p>(<h[1-3]>)/g, '$1');
        html = html.replace(/(<\/h[1-3]>)<\/p>/g, '$1');
        html = html.replace(/<p>(<hr[^>]*>)<\/p>/g, '$1');
        html = html.replace(/<p>(<blockquote[^>]*>)/g, '$1');
        html = html.replace(/(<\/blockquote>)<\/p>/g, '$1');

        return html;
    },

    // P2: 双链笔记支持
    _linkPopup: null,
    _linkCandidates: [],

    setupBiDirectionalLinks(textarea) {
        if (!textarea) return;
        textarea.addEventListener('input', (e) => {
            const val = textarea.value;
            const pos = textarea.selectionStart;
            // 检测 [[ 触发
            const before = val.substring(0, pos);
            const match = before.match(/\[\[([^\]]*?)$/);
            if (match) {
                this._showLinkPopup(textarea, match[1]);
            } else {
                this._hideLinkPopup();
            }
        });
        textarea.addEventListener('keydown', (e) => {
            if (this._linkPopup && this._linkPopup.style.display !== 'none') {
                if (e.key === 'Escape') {
                    this._hideLinkPopup();
                    e.preventDefault();
                }
            }
        });
    },

    async _showLinkPopup(textarea, query) {
        if (!this._linkPopup) {
            this._linkPopup = document.createElement('div');
            this._linkPopup.className = 'wiki-link-popup';
            this._linkPopup.style.cssText = 'position:absolute;z-index:1000;background:var(--bg-surface,#fff);border:1px solid var(--border,#e0e3ea);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.15);max-height:200px;overflow-y:auto;min-width:200px;';
            document.body.appendChild(this._linkPopup);
        }
        try {
            const data = await wikiApi('GET', `/pages/link-candidates?q=${encodeURIComponent(query)}`);
            this._linkCandidates = data.pages || [];
        } catch (e) {
            this._linkCandidates = [];
        }
        if (this._linkCandidates.length === 0) {
            this._linkPopup.innerHTML = '<div style="padding:8px 12px;color:var(--text-muted,#94a3b8);font-size:13px;">无匹配页面</div>';
        } else {
            this._linkPopup.innerHTML = this._linkCandidates.map((p, i) =>
                `<div class="link-candidate" data-index="${i}" style="padding:6px 12px;cursor:pointer;font-size:13px;color:var(--text-primary,#1e293b);border-bottom:1px solid var(--border,#e0e3ea);" onmouseenter="this.style.background='var(--primary-light,rgba(79,70,229,0.08))'" onmouseleave="this.style.background=''" onclick="WikiEditor._insertLink(${i})">${escapeHtml(p.title)}</div>`
            ).join('');
        }
        // 定位
        const rect = textarea.getBoundingClientRect();
        this._linkPopup.style.left = rect.left + 10 + 'px';
        this._linkPopup.style.top = rect.bottom + 4 + 'px';
        this._linkPopup.style.display = 'block';
    },

    _hideLinkPopup() {
        if (this._linkPopup) this._linkPopup.style.display = 'none';
    },

    _insertLink(index) {
        const page = this._linkCandidates[index];
        if (!page) return;
        const textarea = document.querySelector('.wiki-textarea');
        if (!textarea) return;
        const val = textarea.value;
        const pos = textarea.selectionStart;
        const before = val.substring(0, pos);
        const after = val.substring(pos);
        // 替换 [[partial 为 [[页面名]]
        const newBefore = before.replace(/\[\[([^\]]*?)$/, `[[${page.title}]]`);
        textarea.value = newBefore + after;
        textarea.selectionStart = textarea.selectionEnd = newBefore.length;
        textarea.focus();
        this._hideLinkPopup();
        // 触发 change
        textarea.dispatchEvent(new Event('input'));
    },

    // P2: 渲染双链为可点击链接
    renderWikiLinks(html) {
        return html.replace(/\[\[([^\]]+)\]\]/g, (match, title) => {
            return `<a class="wiki-bilink" href="#" onclick="WikiEditor._navigateToLink('${escapeHtml(title)}');return false;" style="color:var(--primary,#4f46e5);text-decoration:none;border-bottom:1px dashed var(--primary,#4f46e5);cursor:pointer;">${escapeHtml(title)}</a>`;
        });
    },

    async _navigateToLink(title) {
        try {
            const data = await wikiApi('GET', `/pages/search/${encodeURIComponent(title)}`);
            if (data.pages && data.pages.length > 0) {
                const page = data.pages[0];
                if (typeof loadWikiPage === 'function') loadWikiPage(page.page_id);
            } else {
                showToast('未找到页面: ' + title, 'warning');
            }
        } catch (e) {
            showToast('跳转失败', 'error');
        }
    }
};
