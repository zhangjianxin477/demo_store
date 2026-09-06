const AppStore = {
    _state: {
        theme: localStorage.getItem('theme') || 'light',
        currentPage: 'kg',
        sidebarCollapsed: false,
    },

    _moduleStates: {
        file: {
            rootDirHandle: null,
            rootDirName: null,
            files: [],
            openedFile: null,
            selectedFile: null,
        },
        rag: {
            initialized: false,
            searchMode: 'hybrid',
            currentChunks: [],
            selectedKB: '',
            webSearchEnabled: false,
            kbList: [],
        },
        kg: {
            graphData: { nodes: [], edges: [] },
            currentDocKey: null,
            selectedNode: null,
            hoverNode: null,
            highlightedNodeIds: new Set(),
            highlightedEdgeIds: new Set(),
            aggregationEnabled: true,
        },
        wiki: {
            currentSpaceId: 'default',
            currentParentId: '',
            currentPage: null,
            spaces: [],
            tags: [],
            pageTree: [],
            versions: [],
            comments: [],
            searchResults: [],
            annotations: [],
        },
    },

    _listeners: new Map(),

    getState(path) {
        const parts = path.split('.');
        let current = this._state;
        for (const part of parts) {
            if (current[part] === undefined) {
                current = this._moduleStates;
                for (const p of parts) {
                    if (current[p] === undefined) return undefined;
                    current = current[p];
                }
                return current;
            }
            current = current[part];
        }
        return current;
    },

    setState(path, value) {
        const parts = path.split('.');
        let current = this._state;
        for (let i = 0; i < parts.length - 1; i++) {
            if (current[parts[i]] === undefined) {
                current = this._moduleStates;
                for (let j = 0; j < i; j++) {
                    current = current[parts[j]];
                }
                break;
            }
            current = current[parts[i]];
        }

        const key = parts[parts.length - 1];
        const oldValue = current[key];
        current[key] = value;

        if (oldValue !== value) {
            this._notify(path, value, oldValue);
        }
    },

    updateModule(moduleName, updates) {
        const state = this._moduleStates[moduleName];
        if (!state) return;
        for (const [key, value] of Object.entries(updates)) {
            const oldValue = state[key];
            state[key] = value;
            if (oldValue !== value) {
                this._notify(`${moduleName}.${key}`, value, oldValue);
            }
        }
    },

    getModule(moduleName) {
        return this._moduleStates[moduleName];
    },

    on(path, callback) {
        if (!this._listeners.has(path)) {
            this._listeners.set(path, []);
        }
        this._listeners.get(path).push(callback);
        return () => {
            const listeners = this._listeners.get(path);
            if (listeners) {
                const idx = listeners.indexOf(callback);
                if (idx >= 0) listeners.splice(idx, 1);
            }
        };
    },

    _notify(path, newValue, oldValue) {
        for (const [listenerPath, callbacks] of this._listeners.entries()) {
            if (path === listenerPath || path.startsWith(listenerPath + '.') || listenerPath.startsWith(path + '.')) {
                for (const cb of callbacks) {
                    try {
                        cb(newValue, oldValue, path);
                    } catch (e) {
                        console.error('Store listener error:', e);
                    }
                }
            }
        }
    },

    get theme() { return this._state.theme; },
    set theme(val) {
        const nextTheme = val === 'dark' ? 'light' : (val || 'light');
        this._state.theme = nextTheme;
        localStorage.setItem('theme', nextTheme);
        document.documentElement.removeAttribute('data-theme');
        this._notify('theme', nextTheme);
    },

    get currentPage() { return this._state.currentPage; },
    set currentPage(val) {
        this._state.currentPage = val;
        this._notify('currentPage', val);
    },
};

window.AppStore = AppStore;
