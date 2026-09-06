let _embeddingPipeline = null;
let _embeddingLoading = false;
let _embeddingLoaded = false;
let _embeddingDimension = 384;
let _embeddingModelName = 'Xenova/all-MiniLM-L6-v2';
let _embeddingLoadPromise = null;
let _embeddingProgressCallback = null;
let _fetchInterceptInstalled = false;

// O9: Web Worker 支持
let _worker = null;
let _useWorker = false;
let _workerEmbedCallbacks = {};
let _workerEmbedId = 0;

// O9: 懒加载标记，首次使用时才初始化
let _lazyInitRequested = false;

function _installFetchInterceptor() {
    if (_fetchInterceptInstalled) return;
    _fetchInterceptInstalled = true;

    const originalFetch = window.fetch;
    const HF_ORIGINS = [
        'https://huggingface.co/',
        'https://cdn-lfs.huggingface.co/',
        'https://cdn-lfs-us-1.huggingface.co/',
    ];

    window.fetch = function (input, init) {
        let url;
        if (typeof input === 'string') {
            url = input;
        } else if (input instanceof URL) {
            url = input.href;
        } else if (input instanceof Request) {
            url = input.url;
        } else {
            return originalFetch.call(this, input, init);
        }

        for (const origin of HF_ORIGINS) {
            if (url.startsWith(origin)) {
                const path = url.substring(origin.length);
                const proxyUrl = '/hf-proxy/' + path;
                console.log('[HF-Proxy] ' + url + ' -> ' + proxyUrl);
                const newInit = { ...init };
                if (newInit.signal) {
                    delete newInit.signal;
                }
                if (newInit.headers) {
                    const h = new Headers(newInit.headers);
                    h.delete('Authorization');
                    newInit.headers = h;
                }
                return originalFetch.call(this, proxyUrl, newInit);
            }
        }

        return originalFetch.call(this, input, init);
    };

    console.log('[HF-Proxy] fetch 拦截器已安装，HuggingFace 请求将重定向到本地代理');
}

// O9: Web Worker 初始化
function _initWorker() {
    try {
        _worker = new Worker('js/modules/embedding-worker.js');
        _worker.onmessage = function (e) {
            const { type, data } = e.data;
            switch (type) {
                case 'progress':
                    if (_embeddingProgressCallback) {
                        _embeddingProgressCallback(data);
                    }
                    break;
                case 'initialized':
                    _embeddingLoaded = true;
                    _embeddingLoading = false;
                    _embeddingDimension = data.dimension;
                    if (_embeddingProgressCallback) {
                        _embeddingProgressCallback({ status: 'initialized', progress: 100 });
                    }
                    console.log(`Web Worker 嵌入模型加载成功: ${data.modelName}, dim=${data.dimension}`);
                    break;
                case 'error':
                    _embeddingLoading = false;
                    _embeddingLoaded = false;
                    if (_embeddingProgressCallback) {
                        _embeddingProgressCallback({ status: 'error', error: data.error, progress: 0 });
                    }
                    console.error('Web Worker 嵌入模型加载失败:', data.error);
                    break;
                case 'embed_result':
                    const cb = _workerEmbedCallbacks[data.id];
                    if (cb) {
                        cb.resolve(data.embedding);
                        delete _workerEmbedCallbacks[data.id];
                    }
                    break;
                case 'embed_error':
                    const cbErr = _workerEmbedCallbacks[data.id];
                    if (cbErr) {
                        cbErr.reject(new Error(data.error));
                        delete _workerEmbedCallbacks[data.id];
                    }
                    break;
                case 'embed_batch_result':
                    const batchCb = _workerEmbedCallbacks[data.id];
                    if (batchCb) {
                        batchCb.resolve(data.embeddings);
                        delete _workerEmbedCallbacks[data.id];
                    }
                    break;
                case 'embed_batch_error':
                    const batchCbErr = _workerEmbedCallbacks[data.id];
                    if (batchCbErr) {
                        batchCbErr.reject(new Error(data.error));
                        delete _workerEmbedCallbacks[data.id];
                    }
                    break;
            }
        };
        _worker.onerror = function (err) {
            console.warn('Web Worker 错误，回退到主线程:', err);
            _useWorker = false;
            _worker = null;
        };
        _useWorker = true;
        console.log('嵌入模型 Web Worker 已创建');
    } catch (e) {
        console.warn('Web Worker 创建失败，回退到主线程:', e);
        _useWorker = false;
    }
}

const localEmbeddingService = {
    get isAvailable() {
        return _embeddingLoaded && (_embeddingPipeline !== null || _useWorker);
    },

    get dimension() {
        return _embeddingDimension;
    },

    get isLoading() {
        return _embeddingLoading;
    },

    setProgressCallback(cb) {
        _embeddingProgressCallback = cb;
    },

    async initialize() {
        if (_embeddingLoaded && (_embeddingPipeline || _useWorker)) return true;
        if (_embeddingLoadPromise) return _embeddingLoadPromise;

        // O9: 优先使用 Web Worker 加载模型
        if (!_worker && !_useWorker) {
            _initWorker();
        }

        _embeddingLoadPromise = this._doInitialize();
        return _embeddingLoadPromise;
    },

    async _doInitialize() {
        _embeddingLoading = true;
        try {
            if (_embeddingProgressCallback) {
                _embeddingProgressCallback({ status: 'loading_library', progress: 0 });
            }

            // O9: 如果 Web Worker 可用，在 Worker 中加载模型
            if (_useWorker && _worker) {
                _installFetchInterceptor();
                _worker.postMessage({ type: 'init', data: {} });
                // Worker 会通过 onmessage 回调更新状态
                return new Promise((resolve) => {
                    const checkInterval = setInterval(() => {
                        if (_embeddingLoaded) {
                            clearInterval(checkInterval);
                            resolve(true);
                        }
                    }, 200);
                    // 超时 120 秒
                    setTimeout(() => {
                        clearInterval(checkInterval);
                        if (!_embeddingLoaded) {
                            _useWorker = false;
                            resolve(false);
                        }
                    }, 120000);
                });
            }

            // 回退到主线程加载
            _installFetchInterceptor();

            const { pipeline, env } = await import(
                'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2'
            );

            env.allowLocalModels = false;
            env.allowRemoteModels = true;
            env.useBrowserCache = true;

            console.log('嵌入模型加载中（通过 fetch 拦截器 + 本地代理）...');

            if (_embeddingProgressCallback) {
                _embeddingProgressCallback({ status: 'loading_model', progress: 10 });
            }

            _embeddingPipeline = await pipeline('feature-extraction', _embeddingModelName, {
                progress_callback: (progress) => {
                    if (_embeddingProgressCallback) {
                        if (progress.status === 'download') {
                            const pct = progress.progress ? Math.round(progress.progress) : 0;
                            _embeddingProgressCallback({
                                status: 'downloading_model',
                                progress: 10 + Math.round(pct * 0.8),
                                file: progress.file,
                            });
                        } else if (progress.status === 'ready') {
                            _embeddingProgressCallback({
                                status: 'model_ready',
                                progress: 95,
                            });
                        }
                    }
                },
            });

            const testResult = await _embeddingPipeline('test', {
                pooling: 'mean',
                normalize: true,
            });
            _embeddingDimension = testResult.dims[testResult.dims.length - 1];

            _embeddingLoaded = true;
            if (_embeddingProgressCallback) {
                _embeddingProgressCallback({ status: 'initialized', progress: 100 });
            }
            console.log(
                `本地嵌入模型加载成功: ${_embeddingModelName}, dim=${_embeddingDimension}`
            );
            return true;
        } catch (e) {
            console.error('本地嵌入模型加载失败:', e);
            _embeddingPipeline = null;
            _embeddingLoaded = false;
            if (_embeddingProgressCallback) {
                _embeddingProgressCallback({ status: 'error', error: e.message, progress: 0 });
            }
            return false;
        } finally {
            _embeddingLoading = false;
            _embeddingLoadPromise = null;
        }
    },

    async embedText(text) {
        if (!text || !text.trim()) return null;

        if (!_embeddingPipeline && !_useWorker) {
            const loaded = await this.initialize();
            if (!loaded) return null;
        }

        // O9: 优先使用 Web Worker
        if (_useWorker && _worker) {
            return new Promise((resolve, reject) => {
                const id = ++_workerEmbedId;
                _workerEmbedCallbacks[id] = { resolve, reject };
                _worker.postMessage({ type: 'embed', data: { text, id } });
            });
        }

        try {
            const result = await _embeddingPipeline(text, {
                pooling: 'mean',
                normalize: true,
            });
            return Array.from(result.data);
        } catch (e) {
            console.error('嵌入向量生成失败:', e);
            return null;
        }
    },

    async embedTexts(texts) {
        if (!texts || texts.length === 0) return [];

        if (!_embeddingPipeline) {
            const loaded = await this.initialize();
            if (!loaded) return texts.map(() => null);
        }

        const results = [];
        for (let i = 0; i < texts.length; i++) {
            const text = texts[i];
            if (!text || !text.trim()) {
                results.push(null);
                continue;
            }
            try {
                const result = await _embeddingPipeline(text, {
                    pooling: 'mean',
                    normalize: true,
                });
                results.push(Array.from(result.data));
            } catch (e) {
                console.error(`嵌入向量生成失败 (chunk ${i}):`, e);
                results.push(null);
            }
        }
        return results;
    },

    cosineSimilarity(a, b) {
        if (!a || !b || a.length !== b.length) return 0;
        let dot = 0;
        let normA = 0;
        let normB = 0;
        for (let i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            normA += a[i] * a[i];
            normB += b[i] * b[i];
        }
        const denom = Math.sqrt(normA) * Math.sqrt(normB);
        return denom > 0 ? dot / denom : 0;
    },

    async vectorSearch(queryEmbedding, chunks, topK = 5) {
        if (!queryEmbedding) return [];

        const scored = [];
        for (const chunk of chunks) {
            if (!chunk.embedding) continue;
            const sim = this.cosineSimilarity(queryEmbedding, chunk.embedding);
            if (sim > 0) {
                scored.push({
                    chunk_id: chunk.chunk_id,
                    doc_id: chunk.doc_id || '',
                    text: chunk.text,
                    content: chunk.text,
                    score: sim,
                    source: chunk.doc_id || '',
                    title: chunk.doc_id || '',
                    metadata: {},
                });
            }
        }

        scored.sort((a, b) => b.score - a.score);
        return scored.slice(0, topK);
    },

    keywordSearch(query, chunks, topK = 5) {
        if (!query || !query.trim()) return [];

        const queryLower = query.toLowerCase();
        const queryWords = queryLower.match(/[\u4e00-\u9fff]+|\w+/g) || [];
        const queryWordSet = new Set(queryWords.filter((w) => w.length >= 2));

        if (queryWordSet.size === 0) return [];

        const queryBigrams = new Set();
        for (const w of queryWords) {
            if (w.length >= 2) {
                for (let j = 0; j < w.length - 1; j++) {
                    queryBigrams.add(w.substring(j, j + 2));
                }
            }
        }

        const scored = [];
        for (const chunk of chunks) {
            const text = (chunk.text || '').toLowerCase();
            const textWords = new Set(
                (text.match(/[\u4e00-\u9fff]+|\w+/g) || []).filter((w) => w.length >= 2)
            );

            const overlap = [...queryWordSet].filter((w) => textWords.has(w)).length;
            const wordScore = overlap / Math.max(queryWordSet.size, 1);

            let bigramScore = 0;
            if (queryBigrams.size > 0) {
                const textBigrams = new Set();
                for (const tw of textWords) {
                    if (tw.length >= 2) {
                        for (let j = 0; j < tw.length - 1; j++) {
                            textBigrams.add(tw.substring(j, j + 2));
                        }
                    }
                }
                const bigramOverlap = [...queryBigrams].filter((b) => textBigrams.has(b)).length;
                bigramScore = bigramOverlap / Math.max(queryBigrams.size, 1);
            }

            let exactBonus = 0;
            for (const qw of queryWordSet) {
                if (text.includes(qw)) exactBonus += 0.1;
            }
            exactBonus = Math.min(exactBonus, 0.3);

            const totalScore = wordScore * 0.5 + bigramScore * 0.3 + exactBonus;
            if (totalScore > 0) {
                scored.push({
                    chunk_id: chunk.chunk_id,
                    doc_id: chunk.doc_id || '',
                    text: chunk.text,
                    content: chunk.text,
                    score: totalScore,
                    source: chunk.doc_id || '',
                    title: chunk.doc_id || '',
                    metadata: {},
                });
            }
        }

        scored.sort((a, b) => b.score - a.score);
        return scored.slice(0, topK);
    },

    hybridSearch(queryEmbedding, query, chunks, topK = 5, vectorWeight = 0.6, keywordWeight = 0.4) {
        const vectorResults = queryEmbedding
            ? this.vectorSearch(queryEmbedding, chunks, topK * 3)
            : [];
        const keywordResults = this.keywordSearch(query, chunks, topK * 3);

        const scoreMap = {};

        const maxVScore =
            vectorResults.length > 0
                ? Math.max(...vectorResults.map((r) => r.score))
                : 1.0;
        const maxKScore =
            keywordResults.length > 0
                ? Math.max(...keywordResults.map((r) => r.score))
                : 1.0;

        for (const r of vectorResults) {
            const normalized = r.score / (maxVScore || 1);
            scoreMap[r.chunk_id] = {
                ...r,
                score: normalized * vectorWeight,
                _source: 'vector',
            };
        }

        for (const r of keywordResults) {
            const normalized = r.score / (maxKScore || 1);
            if (scoreMap[r.chunk_id]) {
                scoreMap[r.chunk_id].score += normalized * keywordWeight;
                scoreMap[r.chunk_id]._source = 'both';
            } else {
                scoreMap[r.chunk_id] = {
                    ...r,
                    score: normalized * keywordWeight,
                    _source: 'keyword',
                };
            }
        }

        const results = Object.values(scoreMap);
        results.sort((a, b) => b.score - a.score);

        return results.slice(0, topK).map((r) => {
            const { _source, ...rest } = r;
            return rest;
        });
    },
};

window.localEmbeddingService = localEmbeddingService;
