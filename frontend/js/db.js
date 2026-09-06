const DB_NAME = 'KnowledgeHubDB';
const DB_VERSION = 2;

const STORES = {
    files: 'files',
    knowledgeBases: 'knowledgeBases',
    knowledgeGraph: 'knowledgeGraph',
    chatHistory: 'chatHistory',
    wikiPages: 'wikiPages',
    appState: 'appState',
    settings: 'settings',
    kbDocuments: 'kbDocuments',
    kbChunks: 'kbChunks',
};

let _db = null;

function openDB() {
    if (_db) return Promise.resolve(_db);
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(STORES.files)) {
                db.createObjectStore(STORES.files, { keyPath: 'path' });
            }
            if (!db.objectStoreNames.contains(STORES.knowledgeBases)) {
                db.createObjectStore(STORES.knowledgeBases, { keyPath: 'kb_id' });
            }
            if (!db.objectStoreNames.contains(STORES.knowledgeGraph)) {
                const kgStore = db.createObjectStore(STORES.knowledgeGraph, { keyPath: 'doc_key' });
                kgStore.createIndex('doc_key', 'doc_key', { unique: true });
            }
            if (!db.objectStoreNames.contains(STORES.chatHistory)) {
                const chatStore = db.createObjectStore(STORES.chatHistory, { keyPath: 'id', autoIncrement: true });
                chatStore.createIndex('module', 'module', { unique: false });
                chatStore.createIndex('timestamp', 'timestamp', { unique: false });
            }
            if (!db.objectStoreNames.contains(STORES.wikiPages)) {
                db.createObjectStore(STORES.wikiPages, { keyPath: 'page_id' });
            }
            if (!db.objectStoreNames.contains(STORES.appState)) {
                db.createObjectStore(STORES.appState, { keyPath: 'key' });
            }
            if (!db.objectStoreNames.contains(STORES.settings)) {
                db.createObjectStore(STORES.settings, { keyPath: 'key' });
            }
            if (!db.objectStoreNames.contains(STORES.kbDocuments)) {
                const kbDocStore = db.createObjectStore(STORES.kbDocuments, { keyPath: 'doc_id' });
                kbDocStore.createIndex('kb_id', 'kb_id', { unique: false });
            }
            if (!db.objectStoreNames.contains(STORES.kbChunks)) {
                const kbChunkStore = db.createObjectStore(STORES.kbChunks, { keyPath: 'chunk_id' });
                kbChunkStore.createIndex('kb_id', 'kb_id', { unique: false });
                kbChunkStore.createIndex('doc_id', 'doc_id', { unique: false });
            }
        };
        request.onsuccess = (event) => {
            _db = event.target.result;
            resolve(_db);
        };
        request.onerror = (event) => {
            console.error('IndexedDB open error:', event.target.error);
            reject(event.target.error);
        };
    });
}

async function dbPut(storeName, data) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        const request = store.put(data);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function dbGet(storeName, key) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const request = store.get(key);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function dbGetAll(storeName) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function dbDelete(storeName, key) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        const request = store.delete(key);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

async function dbClear(storeName) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        const request = store.clear();
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

async function dbGetByIndex(storeName, indexName, value) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const index = store.index(indexName);
        const request = index.getAll(value);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

const LocalDB = {
    STORES,
    openDB,
    put: dbPut,
    get: dbGet,
    getAll: dbGetAll,
    delete: dbDelete,
    clear: dbClear,
    getByIndex: dbGetByIndex,

    async saveFile(fileData) {
        return dbPut(STORES.files, {
            path: fileData.path || fileData.name,
            name: fileData.name,
            content: fileData.content,
            isDirectory: fileData.isDirectory || false,
            children: fileData.children || [],
            isNew: fileData.isNew || false,
            isDirty: fileData.isDirty || false,
            updatedAt: Date.now(),
        });
    },

    async getFile(path) {
        return dbGet(STORES.files, path);
    },

    async getAllFiles() {
        return dbGetAll(STORES.files);
    },

    async deleteFile(path) {
        return dbDelete(STORES.files, path);
    },

    async saveKnowledgeBase(kbData) {
        return dbPut(STORES.knowledgeBases, {
            ...kbData,
            updatedAt: Date.now(),
        });
    },

    async getKnowledgeBase(kbId) {
        return dbGet(STORES.knowledgeBases, kbId);
    },

    async getAllKnowledgeBases() {
        return dbGetAll(STORES.knowledgeBases);
    },

    async deleteKnowledgeBase(kbId) {
        return dbDelete(STORES.knowledgeBases, kbId);
    },

    async saveKGGraph(docKey, graphData) {
        return dbPut(STORES.knowledgeGraph, {
            doc_key: docKey || '__global__',
            nodes: graphData.nodes || [],
            edges: graphData.edges || [],
            updatedAt: Date.now(),
        });
    },

    async getKGGraph(docKey) {
        return dbGet(STORES.knowledgeGraph, docKey || '__global__');
    },

    async getAllKGGraphs() {
        return dbGetAll(STORES.knowledgeGraph);
    },

    async deleteKGGraph(docKey) {
        return dbDelete(STORES.knowledgeGraph, docKey || '__global__');
    },

    async saveChatMessage(module, message) {
        return dbPut(STORES.chatHistory, {
            id: message.id || undefined,
            module,
            role: message.role,
            content: message.content,
            timestamp: message.timestamp || Date.now(),
            extra: message.extra || {},
        });
    },

    async getChatHistory(module) {
        return dbGetByIndex(STORES.chatHistory, 'module', module);
    },

    async clearChatHistory(module) {
        if (module) {
            const messages = await dbGetByIndex(STORES.chatHistory, 'module', module);
            const db = await openDB();
            const tx = db.transaction(STORES.chatHistory, 'readwrite');
            const store = tx.objectStore(STORES.chatHistory);
            for (const msg of messages) {
                store.delete(msg.id);
            }
            return new Promise((resolve, reject) => {
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
        }
        return dbClear(STORES.chatHistory);
    },

    async saveAppState(key, value) {
        return dbPut(STORES.appState, { key, value, updatedAt: Date.now() });
    },

    async getAppState(key) {
        const result = await dbGet(STORES.appState, key);
        return result ? result.value : null;
    },

    async saveWikiPage(pageData) {
        return dbPut(STORES.wikiPages, {
            ...pageData,
            updatedAt: Date.now(),
        });
    },

    async getWikiPage(pageId) {
        return dbGet(STORES.wikiPages, pageId);
    },

    async getAllWikiPages() {
        return dbGetAll(STORES.wikiPages);
    },

    async deleteWikiPage(pageId) {
        return dbDelete(STORES.wikiPages, pageId);
    },

    async saveKBDocument(docData) {
        return dbPut(STORES.kbDocuments, { ...docData, updatedAt: Date.now() });
    },

    async getKBDocuments(kbId) {
        return dbGetByIndex(STORES.kbDocuments, 'kb_id', kbId);
    },

    async deleteKBDocument(docId) {
        return dbDelete(STORES.kbDocuments, docId);
    },

    async deleteKBDocumentsByKB(kbId) {
        const docs = await dbGetByIndex(STORES.kbDocuments, 'kb_id', kbId);
        for (const doc of docs) {
            await dbDelete(STORES.kbDocuments, doc.doc_id);
        }
    },

    async saveKBChunk(chunkData) {
        return dbPut(STORES.kbChunks, { ...chunkData, updatedAt: Date.now() });
    },

    async getKBChunks(kbId) {
        return dbGetByIndex(STORES.kbChunks, 'kb_id', kbId);
    },

    async getKBChunksByDoc(docId) {
        return dbGetByIndex(STORES.kbChunks, 'doc_id', docId);
    },

    async deleteKBChunksByDoc(docId) {
        const chunks = await dbGetByIndex(STORES.kbChunks, 'doc_id', docId);
        for (const chunk of chunks) {
            await dbDelete(STORES.kbChunks, chunk.chunk_id);
        }
    },

    async deleteKBChunksByKB(kbId) {
        const chunks = await dbGetByIndex(STORES.kbChunks, 'kb_id', kbId);
        for (const chunk of chunks) {
            await dbDelete(STORES.kbChunks, chunk.chunk_id);
        }
    },

    async getAllKBChunks() {
        return dbGetAll(STORES.kbChunks);
    },

    async getKBChunksWithEmbeddings(kbId) {
        const chunks = kbId
            ? await dbGetByIndex(STORES.kbChunks, 'kb_id', kbId)
            : await dbGetAll(STORES.kbChunks);
        return chunks.filter((c) => c.embedding && c.embedding.length > 0);
    },

    async getKBChunksWithoutEmbeddings(kbId) {
        const chunks = kbId
            ? await dbGetByIndex(STORES.kbChunks, 'kb_id', kbId)
            : await dbGetAll(STORES.kbChunks);
        return chunks.filter((c) => !c.embedding || c.embedding.length === 0);
    },

    async updateKBChunkEmbedding(chunkId, embedding) {
        const chunk = await dbGet(STORES.kbChunks, chunkId);
        if (!chunk) return;
        chunk.embedding = embedding;
        chunk.updatedAt = Date.now();
        return dbPut(STORES.kbChunks, chunk);
    },

    async batchUpdateKBChunkEmbeddings(updates) {
        for (const { chunkId, embedding } of updates) {
            const chunk = await dbGet(STORES.kbChunks, chunkId);
            if (!chunk) continue;
            chunk.embedding = embedding;
            chunk.updatedAt = Date.now();
            await dbPut(STORES.kbChunks, chunk);
        }
    },

    async exportAll() {
        const result = {};
        for (const [name, storeName] of Object.entries(STORES)) {
            result[name] = await dbGetAll(storeName);
        }
        return result;
    },

    async importAll(data) {
        for (const [name, storeName] of Object.entries(STORES)) {
            if (data[name] && Array.isArray(data[name])) {
                await dbClear(storeName);
                for (const item of data[name]) {
                    await dbPut(storeName, item);
                }
            }
        }
    },
};

window.LocalDB = LocalDB;
