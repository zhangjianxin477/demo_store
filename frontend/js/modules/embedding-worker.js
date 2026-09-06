/**
 * 嵌入模型 Web Worker - O9 优化
 * 在独立线程中加载和运行嵌入模型，避免阻塞主线程
 */

let pipeline = null;
let modelName = 'Xenova/all-MiniLM-L6-v2';
let dimension = 384;

self.onmessage = async function (e) {
    const { type, data } = e.data;

    switch (type) {
        case 'init':
            try {
                self.postMessage({ type: 'progress', data: { status: 'loading_library', progress: 0 } });

                const transformers = await import(
                    'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2'
                );
                const { pipeline: createPipeline, env } = transformers;

                env.allowLocalModels = false;
                env.allowRemoteModels = true;
                env.useBrowserCache = true;

                self.postMessage({ type: 'progress', data: { status: 'loading_model', progress: 10 } });

                pipeline = await createPipeline('feature-extraction', modelName, {
                    progress_callback: (progress) => {
                        if (progress.status === 'download') {
                            const pct = progress.progress ? Math.round(progress.progress) : 0;
                            self.postMessage({
                                type: 'progress',
                                data: { status: 'downloading_model', progress: 10 + Math.round(pct * 0.8), file: progress.file }
                            });
                        } else if (progress.status === 'ready') {
                            self.postMessage({ type: 'progress', data: { status: 'model_ready', progress: 95 } });
                        }
                    },
                });

                // 测试获取维度
                const testResult = await pipeline('test', { pooling: 'mean', normalize: true });
                dimension = testResult.dims[testResult.dims.length - 1];

                self.postMessage({ type: 'initialized', data: { dimension, modelName } });
            } catch (err) {
                self.postMessage({ type: 'error', data: { error: err.message } });
            }
            break;

        case 'embed':
            if (!pipeline) {
                self.postMessage({ type: 'embed_error', data: { error: '模型未初始化', id: data.id } });
                return;
            }
            try {
                const result = await pipeline(data.text, { pooling: 'mean', normalize: true });
                const embedding = Array.from(result.data);
                self.postMessage({ type: 'embed_result', data: { embedding, id: data.id } });
            } catch (err) {
                self.postMessage({ type: 'embed_error', data: { error: err.message, id: data.id } });
            }
            break;

        case 'embed_batch':
            if (!pipeline) {
                self.postMessage({ type: 'embed_batch_error', data: { error: '模型未初始化', id: data.id } });
                return;
            }
            try {
                const results = [];
                for (const text of data.texts) {
                    if (!text || !text.trim()) {
                        results.push(null);
                        continue;
                    }
                    const result = await pipeline(text, { pooling: 'mean', normalize: true });
                    results.push(Array.from(result.data));
                }
                self.postMessage({ type: 'embed_batch_result', data: { embeddings: results, id: data.id } });
            } catch (err) {
                self.postMessage({ type: 'embed_batch_error', data: { error: err.message, id: data.id } });
            }
            break;
    }
};
