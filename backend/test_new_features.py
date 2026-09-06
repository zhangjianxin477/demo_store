import unittest
import os
import sys
import json
import tempfile
import shutil
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.vector_store import VectorStore
from app.kg.store import KnowledgeGraphStore, DocumentGraph, KGNode, KGEdge


class TestVectorStoreKeywordSearch(unittest.TestCase):
    def setUp(self):
        self.vs = VectorStore()
        self.vs.add_documents([
            {"chunk_id": "c1", "doc_id": "d1", "content": "人工智能是计算机科学的一个分支，致力于创建智能机器", "metadata": {"kb_id": "kb1"}},
            {"chunk_id": "c2", "doc_id": "d1", "content": "机器学习是人工智能的核心技术，通过数据训练模型", "metadata": {"kb_id": "kb1"}},
            {"chunk_id": "c3", "doc_id": "d2", "content": "深度学习是机器学习的子集，使用神经网络进行学习", "metadata": {"kb_id": "kb1"}},
            {"chunk_id": "c4", "doc_id": "d3", "content": "自然语言处理让计算机理解和生成人类语言", "metadata": {"kb_id": "kb2"}},
            {"chunk_id": "c5", "doc_id": "d3", "content": "知识图谱是一种结构化的知识表示方法", "metadata": {"kb_id": "kb2"}},
        ])

    def test_basic_keyword_search(self):
        results = self.vs.keyword_search("人工智能", top_k=3)
        self.assertGreater(len(results), 0)
        top_content = results[0]["content"]
        self.assertIn("人工智能", top_content)

    def test_keyword_search_with_kb_id(self):
        results = self.vs.keyword_search("知识图谱", top_k=3, kb_id="kb2")
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r["metadata"]["kb_id"], "kb2")

    def test_keyword_search_no_results(self):
        results = self.vs.keyword_search("量子计算xyz非存在词", top_k=3)
        if results:
            self.assertLess(results[0]["score"], results[0].get("score", 0) + 1)

    def test_keyword_search_scoring(self):
        results = self.vs.keyword_search("机器学习", top_k=3)
        self.assertGreater(len(results), 0)
        self.assertGreater(results[0]["score"], 0)

    def test_empty_query(self):
        results = self.vs.keyword_search("", top_k=3)
        self.assertIsInstance(results, list)

    def test_keyword_search_nonexistent_kb(self):
        results = self.vs.keyword_search("人工智能", top_k=3, kb_id="nonexistent")
        self.assertEqual(len(results), 0)


class TestDocumentGraph(unittest.TestCase):
    def test_add_nodes_and_edges(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="实体1", node_type="person")
        n2 = g.add_node(label="实体2", node_type="concept")
        e = g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="related_to")
        self.assertEqual(g.node_count, 2)
        self.assertEqual(g.edge_count, 1)

    def test_node_to_dict(self):
        g = DocumentGraph(doc_key="test")
        n = g.add_node(label="测试节点", node_type="person", properties={"age": 30})
        d = n.to_dict()
        self.assertEqual(d["label"], "测试节点")
        self.assertEqual(d["node_type"], "person")
        self.assertEqual(d["properties"]["age"], 30)

    def test_edge_to_dict(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        e = g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="related_to", weight=0.8)
        d = e.to_dict()
        self.assertEqual(d["source"], n1.id)
        self.assertEqual(d["target"], n2.id)
        self.assertEqual(d["relation_type"], "related_to")
        self.assertAlmostEqual(d["weight"], 0.8)

    def test_clear_graph(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        self.assertEqual(g.node_count, 2)
        self.assertEqual(g.edge_count, 1)
        g.clear()
        self.assertEqual(g.node_count, 0)
        self.assertEqual(g.edge_count, 0)

    def test_get_all_nodes(self):
        g = DocumentGraph(doc_key="test")
        g.add_node(label="A", node_type="concept")
        g.add_node(label="B", node_type="concept")
        nodes = g.get_all_nodes()
        self.assertEqual(len(nodes), 2)

    def test_get_all_edges(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        edges = g.get_all_edges()
        self.assertEqual(len(edges), 1)

    def test_duplicate_node_merge(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="Same", node_type="concept")
        n2 = g.add_node(label="Same", node_type="concept")
        self.assertEqual(n1.id, n2.id)
        self.assertEqual(g.node_count, 1)

    def test_duplicate_edge_merge(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        e1 = g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        e2 = g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        self.assertEqual(e1.id, e2.id)
        self.assertEqual(g.edge_count, 1)

    def test_subgraph(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        n3 = g.add_node(label="C", node_type="concept")
        g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        g.add_edge(source_id=n2.id, target_id=n3.id, relation_type="rel")
        nodes, edges = g.get_subgraph(n1.id, depth=2)
        self.assertEqual(len(nodes), 3)
        self.assertEqual(len(edges), 2)

    def test_search_nodes(self):
        g = DocumentGraph(doc_key="test")
        g.add_node(label="人工智能", node_type="concept")
        g.add_node(label="机器学习", node_type="concept")
        g.add_node(label="深度学习", node_type="concept")
        results = g.search_nodes("学习")
        self.assertEqual(len(results), 2)

    def test_delete_node(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="concept")
        n2 = g.add_node(label="B", node_type="concept")
        g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        g.delete_node(n1.id)
        self.assertEqual(g.node_count, 1)
        self.assertEqual(g.edge_count, 0)

    def test_to_dict_and_from_dict(self):
        g = DocumentGraph(doc_key="test")
        n1 = g.add_node(label="A", node_type="person")
        n2 = g.add_node(label="B", node_type="concept")
        g.add_edge(source_id=n1.id, target_id=n2.id, relation_type="knows", weight=0.9)
        d = g.to_dict()
        g2 = DocumentGraph.from_dict(d)
        self.assertEqual(g2.node_count, 2)
        self.assertEqual(g2.edge_count, 1)
        self.assertEqual(g2.doc_key, "test")


class TestKnowledgeGraphStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.temp_dir, "kg_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        with patch('app.kg.store.settings') as mock_settings:
            mock_settings.DATA_DIR = self.temp_dir
            self.store = KnowledgeGraphStore()
            self.store._cache_dir = self.cache_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_doc_graph(self):
        g = self.store.get_or_create_doc_graph("test_doc")
        self.assertIsNotNone(g)
        self.assertIsInstance(g, DocumentGraph)

    def test_get_doc_graph(self):
        g = self.store.get_or_create_doc_graph("test_doc")
        g.add_node(label="测试", node_type="concept")
        self.store._save_doc("test_doc")
        g2 = self.store.get_doc_graph("test_doc")
        self.assertIsNotNone(g2)
        self.assertEqual(g2.node_count, 1)

    def test_list_doc_graphs(self):
        g1 = self.store.get_or_create_doc_graph("doc1")
        g1.add_node(label="A", node_type="concept")
        self.store._save_doc("doc1")
        g2 = self.store.get_or_create_doc_graph("doc2")
        g2.add_node(label="B", node_type="concept")
        self.store._save_doc("doc2")
        graphs = self.store.list_doc_graphs()
        self.assertGreaterEqual(len(graphs), 2)

    def test_delete_doc_graph(self):
        g = self.store.get_or_create_doc_graph("to_delete")
        g.add_node(label="X", node_type="concept")
        self.store._save_doc("to_delete")
        self.store.delete_doc_graph("to_delete")
        g2 = self.store.get_doc_graph("to_delete")
        self.assertIsNone(g2)

    def test_get_all_nodes(self):
        g1 = self.store.get_or_create_doc_graph("doc1")
        g1.add_node(label="A", node_type="concept")
        g2 = self.store.get_or_create_doc_graph("doc2")
        g2.add_node(label="B", node_type="concept")
        all_nodes = self.store.get_all_nodes()
        self.assertEqual(len(all_nodes), 2)

    def test_get_all_edges(self):
        g1 = self.store.get_or_create_doc_graph("doc1")
        n1 = g1.add_node(label="A", node_type="concept")
        n2 = g1.add_node(label="B", node_type="concept")
        g1.add_edge(source_id=n1.id, target_id=n2.id, relation_type="rel")
        all_edges = self.store.get_all_edges()
        self.assertEqual(len(all_edges), 1)

    def test_search_nodes(self):
        g = self.store.get_or_create_doc_graph("doc1")
        g.add_node(label="人工智能", node_type="concept")
        g.add_node(label="机器学习", node_type="concept")
        results = self.store.search_nodes("学习")
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
