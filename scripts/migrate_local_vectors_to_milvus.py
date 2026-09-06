"""一次性把本地 vectors.npz/metadata.json 迁移到 Zilliz Cloud。

不会删除或修改本地文件；重复执行时使用 upsert 覆盖同名 chunk_id。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import settings  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402


def main() -> int:
    if not settings.use_milvus:
        raise SystemExit("未配置 VECTOR_BACKEND=milvus、MILVUS_URI 或 MILVUS_TOKEN")
    meta_path = Path(settings.VECTOR_STORE_DIR) / "metadata.json"
    vec_path = Path(settings.VECTOR_STORE_DIR) / "vectors.npz"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    texts = meta.get("texts", [])
    doc_ids = meta.get("doc_ids", [])
    chunk_ids = meta.get("chunk_ids", [])
    metadata = meta.get("metadata", [])
    vectors = np.load(vec_path, allow_pickle=True)
    n = min(len(texts), len(chunk_ids), len(metadata), len(vectors.files))
    if n == 0:
        print("本地没有可迁移的向量")
        return 0
    dim = int(np.asarray(vectors[vectors.files[0]]).reshape(-1).shape[0])
    if dim != settings.EMBEDDING_DIMENSION:
        raise SystemExit(f"维度不匹配: 本地={dim}, 配置={settings.EMBEDDING_DIMENSION}")

    client = MilvusClient(uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN, timeout=30)
    client.load_collection(collection_name=settings.MILVUS_COLLECTION)
    batch_size = 50
    migrated = 0
    seen_ids = set()
    for start in range(0, n, batch_size):
        rows = []
        for i in range(start, min(start + batch_size, n)):
            m = dict(metadata[i] or {})
            chunk_id = str(chunk_ids[i])[:128]
            if not chunk_id or chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            rows.append({
                "chunk_id": chunk_id,
                "doc_id": str(doc_ids[i] if i < len(doc_ids) else "")[:256],
                "kb_id": str(m.get("kb_id", ""))[:256],
                "content": str(texts[i])[:65535],
                "metadata": m,
                "vector": np.asarray(vectors[vectors.files[i]], dtype=np.float32).reshape(-1).tolist(),
            })
        if not rows:
            continue
        client.upsert(collection_name=settings.MILVUS_COLLECTION, data=rows)
        migrated += len(rows)
        print(f"已迁移 {migrated}/{n}")
    print(f"迁移完成: {migrated} 条，collection={settings.MILVUS_COLLECTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
