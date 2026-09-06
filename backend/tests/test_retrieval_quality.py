from app.rag.bm25_search import BM25, HybridSearchEngine
from app.rag.vector_store import VectorStore


def test_bm25_filters_multiple_knowledge_bases():
    bm25 = BM25()
    bm25.add_documents([
        {"chunk_id": "a", "doc_id": "d1", "content": "Milvus 向量检索", "metadata": {"kb_id": "kb-a"}},
        {"chunk_id": "b", "doc_id": "d2", "content": "Milvus 向量检索", "metadata": {"kb_id": "kb-b"}},
        {"chunk_id": "c", "doc_id": "d3", "content": "Milvus 向量检索", "metadata": {"kb_id": "kb-c"}},
    ])
    result = bm25.search("Milvus", top_k=10, kb_ids=["kb-a", "kb-c"])
    assert {item["chunk_id"] for item in result} == {"a", "c"}


def test_hybrid_uses_rank_fusion_instead_of_raw_score_scale():
    engine = HybridSearchEngine()
    results = engine._merge_results(
        [{"chunk_id": "vector", "doc_id": "d1", "content": "向量命中", "score": 0.99, "metadata": {}}],
        [{"chunk_id": "keyword", "doc_id": "d2", "content": "关键词命中", "score": 999.0, "metadata": {}}],
        top_k=2,
    )
    assert {item["chunk_id"] for item in results} == {"vector", "keyword"}
    assert all("retrieval_sources" in item for item in results)


def test_index_model_status_detects_mixed_embedding_spaces():
    store = VectorStore.__new__(VectorStore)
    store._metadata = [
        {"embedding_model": "model-a"},
        {"embedding_model": "model-b"},
    ]
    assert store.index_model_status("model-a")["consistent"] is False
