/**
 * Web Components 组件库 - O7: 从 app.js 抽取可复用组件
 * 包含 Toast 通知、进度条、文件树项等常用 UI 组件
 */

// Toast 通知组件
class KhToast extends HTMLElement {
    connectedCallback() {
        this.className = 'toast-container';
    }

    show(message, type = 'info', duration = 3000) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        this.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            toast.style.transition = 'all 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
}
customElements.define('kh-toast', KhToast);

// 浮动进度条组件
class KhProgress extends HTMLElement {
    static get observedAttributes() {
        return ['message', 'percent'];
    }

    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
    }

    connectedCallback() {
        this.render();
    }

    attributeChangedCallback() {
        this.render();
    }

    render() {
        const message = this.getAttribute('message') || '';
        const percent = parseInt(this.getAttribute('percent') || '0');
        this.shadowRoot.innerHTML = `
            <style>
                :host { display: block; position: fixed; bottom: 20px; right: 20px; z-index: 10000; }
                .progress-card {
                    background: var(--bg-secondary, #1e293b);
                    border: 1px solid var(--border-color, #334155);
                    border-radius: 8px;
                    padding: 12px 16px;
                    min-width: 250px;
                    color: var(--text-primary, #e2e8f0);
                    font-size: 13px;
                }
                .msg { margin-bottom: 8px; }
                .track {
                    height: 4px;
                    background: var(--bg-hover, #334155);
                    border-radius: 2px;
                    overflow: hidden;
                }
                .fill {
                    height: 100%;
                    background: var(--accent, #3b82f6);
                    border-radius: 2px;
                    transition: width 0.3s ease;
                }
                .pct { text-align: right; margin-top: 4px; font-size: 11px; color: var(--text-secondary, #94a3b8); }
            </style>
            <div class="progress-card">
                <div class="msg">${message}</div>
                <div class="track"><div class="fill" style="width:${percent}%"></div></div>
                <div class="pct">${percent}%</div>
            </div>
        `;
    }
}
customElements.define('kh-progress', KhProgress);

// 文件树项组件
class KhFileItem extends HTMLElement {
    static get observedAttributes() {
        return ['name', 'path', 'type', 'depth', 'badge'];
    }

    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
    }

    connectedCallback() {
        this.render();
        this.setupEvents();
    }

    attributeChangedCallback() {
        this.render();
    }

    render() {
        const name = this.getAttribute('name') || '';
        const path = this.getAttribute('path') || '';
        const type = this.getAttribute('type') || 'file';
        const depth = parseInt(this.getAttribute('depth') || '0');
        const badge = this.getAttribute('badge') || '';

        const isDir = type === 'folder';
        const iconSvg = this.getIconSvg(type);

        this.shadowRoot.innerHTML = `
            <style>
                :host { display: block; }
                .item {
                    display: flex;
                    align-items: center;
                    padding: 4px 8px;
                    padding-left: ${depth * 16 + 8}px;
                    cursor: pointer;
                    border-radius: 4px;
                    font-size: 13px;
                    color: var(--text-primary, #e2e8f0);
                    gap: 6px;
                }
                .item:hover { background: var(--bg-hover, #334155); }
                .icon { flex-shrink: 0; display: flex; align-items: center; color: var(--text-secondary, #94a3b8); }
                .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .badge {
                    font-size: 11px;
                    color: var(--text-secondary, #94a3b8);
                    background: var(--bg-hover, #334155);
                    padding: 1px 6px;
                    border-radius: 8px;
                }
            </style>
            <div class="item" data-path="${path}" data-type="${type}">
                <span class="icon">${iconSvg}</span>
                <span class="name">${name}</span>
                ${badge ? `<span class="badge">${badge}</span>` : ''}
            </div>
        `;
    }

    setupEvents() {
        const item = this.shadowRoot.querySelector('.item');
        if (item) {
            item.addEventListener('click', () => {
                this.dispatchEvent(new CustomEvent('file-select', {
                    detail: {
                        path: this.getAttribute('path'),
                        type: this.getAttribute('type'),
                        name: this.getAttribute('name'),
                    },
                    bubbles: true,
                    composed: true,
                }));
            });
        }
    }

    getIconSvg(type) {
        const icons = {
            folder: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
            'file-md': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
            'file-pdf': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
            'file-img': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
        };
        return icons[type] || icons['file-md'];
    }
}
customElements.define('kh-file-item', KhFileItem);

// 状态指示器组件
class KhStatusDot extends HTMLElement {
    static get observedAttributes() {
        return ['status', 'label'];
    }

    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
    }

    connectedCallback() {
        this.render();
    }

    attributeChangedCallback() {
        this.render();
    }

    render() {
        const status = this.getAttribute('status') || 'offline';
        const label = this.getAttribute('label') || '';
        const colors = {
            online: '#22c55e',
            offline: '#f59e0b',
            error: '#ef4444',
            loading: '#3b82f6',
        };
        const color = colors[status] || colors.offline;
        this.shadowRoot.innerHTML = `
            <style>
                :host { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
                .dot { width: 8px; height: 8px; border-radius: 50%; background: ${color}; }
                .label { color: var(--text-secondary, #94a3b8); }
            </style>
            <span class="dot"></span>
            <span class="label">${label}</span>
        `;
    }
}
customElements.define('kh-status-dot', KhStatusDot);
