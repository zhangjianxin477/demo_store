const AutoSave = {
    _interval: null,
    _debounceTimers: {},
    _lastSaveTime: 0,
    _isDirty: false,
    SAVE_INTERVAL: 30000,
    DEBOUNCE_DELAY: 1000,

    init() {
        this.startPeriodicSave();
        window.addEventListener('beforeunload', () => {
            this.saveAll();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') {
                this.saveAll();
            }
        });
        this.restoreState();
            console.log('[AutoSave] 状态恢复完成');
    },

    startPeriodicSave() {
        if (this._interval) clearInterval(this._interval);
        this._interval = setInterval(() => {
            if (this._isDirty && !this.isEditorBusy()) {
                this.saveAll();
            }
        }, this.SAVE_INTERVAL);
    },

    isEditorBusy() {
        try {
            if (typeof isKGReaderEditingActive === 'function' && isKGReaderEditingActive(12000)) {
                return true;
            }
            const active = document.activeElement;
            return !!active && (
                active.id === 'kg-reader-editor'
                || active.id === 'kg-editor-content'
                || active.id === 'rag-editor-content'
            );
        } catch (_) {
            return false;
        }
    },

    markDirty() {
        this._isDirty = true;
    },

    debounceSave(key, fn, delay) {
        if (this._debounceTimers[key]) {
            clearTimeout(this._debounceTimers[key]);
        }
        this._debounceTimers[key] = setTimeout(() => {
            fn();
            delete this._debounceTimers[key];
        }, delay || this.DEBOUNCE_DELAY);
    },

    async saveAll() {
        if (this.isEditorBusy()) return;
        try {
            await this.saveFileState();
            await this.saveKGState();
            await this.saveRAGState();
            await this.saveWikiState();
            await this.saveUIState();
            this._isDirty = false;
            this._lastSaveTime = Date.now();
        } catch (err) {
            console.error('[AutoSave] 保存失败:', err);
        }
    },

    async saveFileState() {
        try {
            if (typeof fileStore === 'undefined') return;

            // 保存文件树结构（不含 handle 和大内容，仅保存元数据）
            const treeMeta = this._serializeFileTree(fileStore.files);
            await LocalDB.saveAppState('fileStore.tree', treeMeta);
            await LocalDB.saveAppState('fileStore.rootDirName', fileStore.rootDirName);
            await LocalDB.saveAppState('fileStore.selectedFile', fileStore.selectedFile || null);

            // Save the currently opened file content to local cache only.
            if (fileStore.openedFile) {
                const maxContentSize = 2 * 1024 * 1024; // 2MB 限制
                const content = fileStore.openedFile.content || '';
                if (content.length < maxContentSize && !fileStore.openedFile.isImage && !fileStore.openedFile.isPdf) {
                    await LocalDB.saveFile({
                        path: fileStore.openedFile.path || fileStore.openedFile.name,
                        name: fileStore.openedFile.name,
                        content: content,
                        isDirectory: false,
                        isDirty: fileStore.openedFile.isDirty || false,
                    });
                }
                await LocalDB.saveAppState('fileStore.openedFilePath', fileStore.openedFile.path);
                await LocalDB.saveAppState('fileStore.openedFileMeta', {
                    name: fileStore.openedFile.name,
                    path: fileStore.openedFile.path,
                    isDirty: fileStore.openedFile.isDirty || false,
                    isConverted: fileStore.openedFile.isConverted || false,
                    isImage: fileStore.openedFile.isImage || false,
                    isPdf: fileStore.openedFile.isPdf || false,
                });
            } else {
                await LocalDB.saveAppState('fileStore.openedFilePath', null);
                await LocalDB.saveAppState('fileStore.openedFileMeta', null);
            }
        } catch (err) {
            console.error('[AutoSave] 文件状态保存失败', err);
        }
    },

    _serializeFileTree(files) {
        return files.map(f => {
            const entry = {
                name: f.name,
                path: f.path,
                isDirectory: f.isDirectory || false,
                isNew: f.isNew || false,
            };
            if (f.isDirectory && f.children && f.children.length) {
                entry.children = this._serializeFileTree(f.children);
            }
            return entry;
        });
    },

    _flattenFiles(files) {
        const result = [];
        for (const f of files) {
            result.push(f);
            if (f.children && f.children.length) {
                result.push(...this._flattenFiles(f.children));
            }
        }
        return result;
    },

    async saveKGState() {
        try {
            if (typeof kgGraphData === 'undefined') return;
            await LocalDB.saveKGGraph(kgCurrentDocKey || '__global__', kgGraphData);
            await LocalDB.saveAppState('kg.currentDocKey', kgCurrentDocKey);
        } catch (err) {
            console.error('[AutoSave] 知识图谱状态保存失败', err);
        }
    },

    async saveRAGState() {
        try {
            if (typeof ragSelectedKB === 'undefined') return;
            await LocalDB.saveAppState('rag.selectedKB', ragSelectedKB);
            await LocalDB.saveAppState('rag.searchMode', typeof ragSearchMode !== 'undefined' ? ragSearchMode : 'hybrid');
            await LocalDB.saveAppState('rag.webSearchEnabled', typeof ragWebSearchEnabled !== 'undefined' ? ragWebSearchEnabled : false);
        } catch (err) {
            console.error('[AutoSave] RAG 状态保存失败', err);
        }
    },

    async saveWikiState() {
        try {
            if (typeof wikiState === 'undefined') return;
            await LocalDB.saveAppState('wiki.currentSpaceId', wikiState.currentSpaceId);
        } catch (err) {
            console.error('[AutoSave] 状态恢复失败', err);
        }
    },

    async saveUIState() {
        try {
            await LocalDB.saveAppState('ui.currentPage', typeof currentPage !== 'undefined' ? currentPage : 'kg');
            await LocalDB.saveAppState('ui.theme', 'light');
            await LocalDB.saveAppState('ui.sidebarCollapsed', typeof AppStore !== 'undefined' ? AppStore.getState('sidebarCollapsed') : false);
        } catch (err) {
            console.error('[AutoSave] 状态恢复失败', err);
        }
    },

    async restoreState() {
        try {
            await this.restoreUIState();
            await this.restoreKGState();
            await this.restoreRAGState();
            console.log('[AutoSave] 状态恢复完成');
        } catch (err) {
            console.error('[AutoSave] 状态恢复失败', err);
        }
    },

    async restoreUIState() {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem('theme', 'light');
        if (typeof AppStore !== 'undefined') {
            AppStore._state.theme = 'light';
        }
        const page = await LocalDB.getAppState('ui.currentPage');
        if (page && typeof navigateTo === 'function') {
            setTimeout(() => navigateTo(page), 100);
        }
    },

    async restoreFileState() {
        try {
            const treeMeta = await LocalDB.getAppState('fileStore.tree');
            if (!treeMeta || !treeMeta.length) return;

            const rootDirName = await LocalDB.getAppState('fileStore.rootDirName');
            if (rootDirName && typeof fileStore !== 'undefined') {
                fileStore.rootDirName = rootDirName;
            }

            // Restore file tree structure from local cache.
            if (typeof fileStore !== 'undefined') {
                fileStore.files = this._deserializeFileTree(treeMeta);
            }

            // 恢复选中文件
            const selectedFile = await LocalDB.getAppState('fileStore.selectedFile');
            if (selectedFile && typeof fileStore !== 'undefined') {
                fileStore.selectedFile = selectedFile;
            }

            // Restore opened file metadata from local cache.
            const openedFilePath = await LocalDB.getAppState('fileStore.openedFilePath');
            const openedFileMeta = await LocalDB.getAppState('fileStore.openedFileMeta');

            if (openedFilePath && typeof fileStore !== 'undefined') {
                // Try to read saved content from IndexedDB.
                const savedFile = await LocalDB.getFile(openedFilePath);
                if (savedFile && savedFile.content) {
                    fileStore.openedFile = {
                        name: savedFile.name,
                        path: savedFile.path,
                        content: savedFile.content,
                        isDirty: savedFile.isDirty || false,
                        isConverted: openedFileMeta?.isConverted || false,
                        isImage: openedFileMeta?.isImage || false,
                        isPdf: openedFileMeta?.isPdf || false,
                        pdfDataUrl: '',
                    };
                } else if (openedFileMeta) {
                    // 鍏冩暟鎹瓨鍦ㄤ絾鍐呭鏈繚瀛橈紙浜岃繘鍒?澶ф枃浠讹級锛屾爣璁伴渶閲嶆柊鍔犺浇
                    fileStore.openedFile = {
                        name: openedFileMeta.name,
                        path: openedFileMeta.path,
                        content: `[文件内容需要重新加载 - 请重新打开此文件]`,
                        isDirty: false,
                        isConverted: openedFileMeta.isConverted || false,
                        isImage: openedFileMeta.isImage || false,
                        isPdf: openedFileMeta.isPdf || false,
                        pdfDataUrl: '',
                        needsReload: true,
                    };
                }
            }

            if (typeof renderFileTree === 'function') renderFileTree();
            if (typeof updateEditorContent === 'function') updateEditorContent();
            if (typeof updateStats === 'function') updateStats();
        } catch (err) {
            console.error('[AutoSave] 文件状态恢复失败', err);
        }
    },

    _deserializeFileTree(treeMeta) {
        return treeMeta.map(f => {
            const entry = {
                name: f.name,
                path: f.path,
                isDirectory: f.isDirectory || false,
                isNew: f.isNew || false,
                // File handles cannot be serialized.
            };
            if (f.isDirectory && f.children && f.children.length) {
                entry.children = this._deserializeFileTree(f.children);
                entry.expanded = false;
            }
            return entry;
        });
    },

    async restoreKGState() {
        const docKey = await LocalDB.getAppState('kg.currentDocKey');
        if (docKey && typeof kgCurrentDocKey !== 'undefined') {
            kgCurrentDocKey = docKey;
        }

        const graphData = await LocalDB.getKGGraph(docKey || '__global__');
        if (graphData && graphData.nodes && graphData.nodes.length > 0 && typeof kgGraphData !== 'undefined') {
            const canvasW = typeof kgCanvas !== 'undefined' && kgCanvas ? kgCanvas.width : 600;
            const canvasH = typeof kgCanvas !== 'undefined' && kgCanvas ? kgCanvas.height : 400;
            kgGraphData = {
                nodes: graphData.nodes.map(n => ({
                    ...n,
                    x: n.x || canvasW / 2 + (Math.random() - 0.5) * 300,
                    y: n.y || canvasH / 2 + (Math.random() - 0.5) * 300,
                    vx: 0,
                    vy: 0,
                })),
                edges: graphData.edges || [],
            };
            if (typeof startKGSimulation === 'function') startKGSimulation();
        }
    },

    async restoreRAGState() {
        const selectedKB = await LocalDB.getAppState('rag.selectedKB');
        if (selectedKB && typeof ragSelectedKB !== 'undefined') {
            ragSelectedKB = selectedKB;
        }
        const searchMode = await LocalDB.getAppState('rag.searchMode');
        if (searchMode && typeof ragSearchMode !== 'undefined') {
            ragSearchMode = searchMode;
        }
        const webSearch = await LocalDB.getAppState('rag.webSearchEnabled');
        if (webSearch !== null && typeof ragWebSearchEnabled !== 'undefined') {
            ragWebSearchEnabled = webSearch;
        }
    },

    getLastSaveTime() {
        return this._lastSaveTime;
    },

    formatLastSaveTime() {
        if (!this._lastSaveTime) return '尚未保存';
        const d = new Date(this._lastSaveTime);
        return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
    },
};

window.AutoSave = AutoSave;
