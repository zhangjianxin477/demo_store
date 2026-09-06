import os
import json
import re
import uuid
import hashlib
import logging
import sqlite3
import threading
from typing import List, Optional, Dict, Any, Set, Tuple
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


class KGNode:
    def __init__(self, id: str, label: str, node_type: str, properties: Optional[Dict] = None):
        self.id = id
        self.label = label
        self.node_type = node_type
        self.properties = properties or {}

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type,
            "properties": self.properties,
        }


class KGEdge:
    def __init__(self, id: str, source: str, target: str, relation_type: str, weight: float = 1.0, properties: Optional[Dict] = None):
        self.id = id
        self.source = source
        self.target = target
        self.relation_type = relation_type
        self.weight = weight
        self.properties = properties or {}

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "properties": self.properties,
        }


class DocumentGraph:
    def __init__(self, doc_key: str):
        self.doc_key = doc_key
        self._nodes: Dict[str, KGNode] = {}
        self._edges: Dict[str, KGEdge] = {}
        self._adjacency: Dict[str, List[str]] = {}

    def add_node(self, label: str, node_type: str, properties: Optional[Dict] = None) -> KGNode:
        for node in self._nodes.values():
            if node.label == label and node.node_type == node_type:
                if properties:
                    node.properties.update(properties)
                return node

        node = KGNode(id=str(uuid.uuid4()), label=label, node_type=node_type, properties=properties)
        self._nodes[node.id] = node
        return node

    def add_edge(self, source_id: str, target_id: str, relation_type: str, weight: float = 1.0, properties: Optional[Dict] = None) -> Optional[KGEdge]:
        if source_id not in self._nodes or target_id not in self._nodes:
            return None

        for edge in self._edges.values():
            if edge.source == source_id and edge.target == target_id and edge.relation_type == relation_type:
                edge.weight = max(edge.weight, weight)
                if properties:
                    edge.properties.update(properties)
                return edge

        edge = KGEdge(id=str(uuid.uuid4()), source=source_id, target=target_id,
                      relation_type=relation_type, weight=weight, properties=properties)
        self._edges[edge.id] = edge
        self._rebuild_adjacency()
        return edge

    def get_all_nodes(self) -> List[KGNode]:
        return list(self._nodes.values())

    def get_all_edges(self) -> List[KGEdge]:
        return list(self._edges.values())

    def get_node(self, node_id: str) -> Optional[KGNode]:
        return self._nodes.get(node_id)

    def get_subgraph(self, center_id: str, depth: int = 2) -> Tuple[List[KGNode], List[KGEdge]]:
        visited_nodes = {center_id}
        visited_edges = set()
        frontier = {center_id}

        for _ in range(depth):
            if not frontier:
                break
            new_frontier = set()
            for nid in frontier:
                for edge in self._edges.values():
                    if edge.source == nid and edge.target not in visited_nodes:
                        visited_nodes.add(edge.target)
                        new_frontier.add(edge.target)
                        visited_edges.add(edge.id)
                    elif edge.target == nid and edge.source not in visited_nodes:
                        visited_nodes.add(edge.source)
                        new_frontier.add(edge.source)
                        visited_edges.add(edge.id)
                    elif edge.source == nid or edge.target == nid:
                        visited_edges.add(edge.id)
            frontier = new_frontier

        nodes = [self._nodes[nid] for nid in visited_nodes if nid in self._nodes]
        edges = [self._edges[eid] for eid in visited_edges if eid in self._edges]
        return nodes, edges

    def search_nodes(self, query: str, limit: int = 20) -> List[KGNode]:
        results = []
        query_lower = query.lower()
        for node in self._nodes.values():
            if query_lower in node.label.lower():
                results.append(node)
        return results[:limit]

    def delete_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        edges_to_remove = [eid for eid, e in self._edges.items() if e.source == node_id or e.target == node_id]
        for eid in edges_to_remove:
            del self._edges[eid]
        del self._nodes[node_id]
        self._rebuild_adjacency()
        return True

    def clear(self):
        self._nodes.clear()
        self._edges.clear()
        self._adjacency.clear()

    def _rebuild_adjacency(self):
        self._adjacency = {}
        for edge in self._edges.values():
            if edge.source not in self._adjacency:
                self._adjacency[edge.source] = []
            self._adjacency[edge.source].append(edge.id)

    def to_dict(self) -> Dict:
        return {
            "doc_key": self.doc_key,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'DocumentGraph':
        graph = cls(doc_key=data.get("doc_key", ""))
        for n in data.get("nodes", []):
            node = KGNode(id=n["id"], label=n["label"], node_type=n["node_type"], properties=n.get("properties", {}))
            graph._nodes[node.id] = node
        for e in data.get("edges", []):
            edge = KGEdge(id=e["id"], source=e["source"], target=e["target"],
                          relation_type=e["relation_type"], weight=e.get("weight", 1.0),
                          properties=e.get("properties", {}))
            graph._edges[edge.id] = edge
        graph._rebuild_adjacency()
        return graph

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)


class KnowledgeGraphStore:
    def __init__(self):
        self._doc_graphs: Dict[str, DocumentGraph] = {}
        self._lock = threading.RLock()
        self._cache_dir = os.path.join(settings.DATA_DIR, "kg_cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._db_path = os.path.join(settings.DATA_DIR, "knowledge_graph.db")
        self._local = threading.local()
        self._init_db()
        # O6: 懒加载模式，启动时不加载全部图谱到内存
        self._lazy_load_enabled = True
        self._max_cached_graphs = 50
        self._load_all()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY,
                doc_key TEXT NOT NULL,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                UNIQUE(id, doc_key)
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_doc_key ON kg_nodes(doc_key);
            CREATE INDEX IF NOT EXISTS idx_nodes_label ON kg_nodes(label);

            CREATE TABLE IF NOT EXISTS kg_edges (
                id TEXT PRIMARY KEY,
                doc_key TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                properties TEXT DEFAULT '{}',
                FOREIGN KEY (source) REFERENCES kg_nodes(id),
                FOREIGN KEY (target) REFERENCES kg_nodes(id)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_doc_key ON kg_edges(doc_key);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON kg_edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON kg_edges(target);
            CREATE INDEX IF NOT EXISTS idx_edges_relation ON kg_edges(relation_type);

            CREATE TABLE IF NOT EXISTS kg_doc_graphs (
                doc_key TEXT PRIMARY KEY,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info("知识图谱 SQLite 数据库初始化完成")

    def _doc_key_to_filename(self, doc_key: str) -> str:
        safe_key = re.sub(r'[^\w\u4e00-\u9fff]', '_', doc_key)
        if len(safe_key) > 80:
            hash_suffix = hashlib.md5(doc_key.encode('utf-8')).hexdigest()[:8]
            safe_key = safe_key[:72] + '_' + hash_suffix
        return safe_key + '.json'

    def _load_all(self):
        with self._lock:
            if self._lazy_load_enabled:
                # O6: 懒加载模式，只加载文档列表，不加载全部图谱数据
                self._load_doc_keys_from_sqlite()
                if not self._doc_graphs:
                    self._load_from_json_files()
            else:
                self._load_from_sqlite()
                if not self._doc_graphs:
                    self._load_from_json_files()

    def _load_doc_keys_from_sqlite(self):
        """O6: 只加载文档键列表，不加载完整图谱数据到内存。"""
        try:
            conn = self._get_conn()
            cursor = conn.execute("SELECT doc_key FROM kg_doc_graphs")
            for row in cursor.fetchall():
                doc_key = row[0]
                # 创建空图谱占位，实际数据在访问时按需加载
                self._doc_graphs[doc_key] = None
            logger.info(f"懒加载模式: 已加载 {len(self._doc_graphs)} 个文档图谱键")
        except Exception as e:
            logger.error(f"加载文档键列表失败: {e}")

    def _ensure_graph_loaded(self, doc_key: str) -> Optional[DocumentGraph]:
        """O6: 按需加载图谱数据，避免启动时全量加载。"""
        if doc_key not in self._doc_graphs:
            return None
        graph = self._doc_graphs[doc_key]
        if graph is not None:
            return graph
        # 从 SQLite 按需加载
        try:
            conn = self._get_conn()
            graph = DocumentGraph(doc_key=doc_key)

            cursor = conn.execute(
                "SELECT id, label, node_type, properties FROM kg_nodes WHERE doc_key = ?",
                (doc_key,)
            )
            for row in cursor.fetchall():
                node = KGNode(
                    id=row[0], label=row[1], node_type=row[2],
                    properties=json.loads(row[3])
                )
                graph._nodes[node.id] = node

            cursor = conn.execute(
                "SELECT id, source, target, relation_type, weight, properties FROM kg_edges WHERE doc_key = ?",
                (doc_key,)
            )
            for row in cursor.fetchall():
                edge = KGEdge(
                    id=row[0], source=row[1], target=row[2],
                    relation_type=row[3], weight=row[4],
                    properties=json.loads(row[5])
                )
                graph._edges[edge.id] = edge

            graph._rebuild_adjacency()
            self._doc_graphs[doc_key] = graph

            # LRU 淘汰：如果缓存超出限制，移除最早未使用的
            self._evict_if_needed()
            logger.debug(f"按需加载图谱: {doc_key} ({graph.node_count} 节点, {graph.edge_count} 边)")
            return graph
        except Exception as e:
            logger.error(f"按需加载图谱失败 (doc_key={doc_key}): {e}")
            return None

    def _evict_if_needed(self):
        """O6: 当缓存图谱数超出限制时，淘汰最早未使用的。"""
        if len(self._doc_graphs) <= self._max_cached_graphs:
            return
        # 保留非 None 的图谱，淘汰最早的
        none_keys = [k for k, v in self._doc_graphs.items() if v is None]
        loaded_keys = [k for k, v in self._doc_graphs.items() if v is not None]
        excess = len(loaded_keys) - self._max_cached_graphs
        if excess > 0:
            for k in loaded_keys[:excess]:
                self._doc_graphs[k] = None  # 释放内存

    def _loaded_graphs(self) -> List[DocumentGraph]:
        graphs: List[DocumentGraph] = []
        for doc_key in list(self._doc_graphs.keys()):
            graph = self._doc_graphs.get(doc_key)
            if graph is None:
                graph = self._ensure_graph_loaded(doc_key)
            if graph is not None:
                graphs.append(graph)
        return graphs

    def _count_sqlite_rows(self, table: str) -> int:
        try:
            conn = self._get_conn()
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0
        except Exception as e:
            logger.warning(f"SQLite count failed for {table}: {e}")
            return 0

    def _load_from_sqlite(self):
        try:
            conn = self._get_conn()
            cursor = conn.execute("SELECT doc_key FROM kg_doc_graphs")
            doc_keys = [row[0] for row in cursor.fetchall()]

            for doc_key in doc_keys:
                graph = DocumentGraph(doc_key=doc_key)

                cursor = conn.execute(
                    "SELECT id, label, node_type, properties FROM kg_nodes WHERE doc_key = ?",
                    (doc_key,)
                )
                for row in cursor.fetchall():
                    node = KGNode(
                        id=row[0], label=row[1], node_type=row[2],
                        properties=json.loads(row[3])
                    )
                    graph._nodes[node.id] = node

                cursor = conn.execute(
                    "SELECT id, source, target, relation_type, weight, properties FROM kg_edges WHERE doc_key = ?",
                    (doc_key,)
                )
                for row in cursor.fetchall():
                    edge = KGEdge(
                        id=row[0], source=row[1], target=row[2],
                        relation_type=row[3], weight=row[4],
                        properties=json.loads(row[5])
                    )
                    graph._edges[edge.id] = edge

                graph._rebuild_adjacency()
                self._doc_graphs[doc_key] = graph

            total_nodes = sum(g.node_count for g in self._doc_graphs.values())
            total_edges = sum(g.edge_count for g in self._doc_graphs.values())
            logger.info(f"从 SQLite 加载知识图谱: {len(self._doc_graphs)} 个文档图谱, {total_nodes} 节点, {total_edges} 边")
        except Exception as e:
            logger.error(f"从 SQLite 加载知识图谱失败: {e}")

    def _load_from_json_files(self):
        if not os.path.isdir(self._cache_dir):
            return
        for fname in os.listdir(self._cache_dir):
            if fname.endswith('.json'):
                fpath = os.path.join(self._cache_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    doc_graph = DocumentGraph.from_dict(data)
                    self._doc_graphs[doc_graph.doc_key] = doc_graph
                except Exception as e:
                    logger.error(f"加载图谱缓存 {fname} 失败: {e}")

        if self._doc_graphs:
            self._migrate_json_to_sqlite()
            total_nodes = sum(g.node_count for g in self._doc_graphs.values())
            total_edges = sum(g.edge_count for g in self._doc_graphs.values())
            logger.info(f"从 JSON 迁移到 SQLite: {len(self._doc_graphs)} 个文档图谱, {total_nodes} 节点, {total_edges} 边")

    def _migrate_json_to_sqlite(self):
        try:
            conn = self._get_conn()
            for doc_key, graph in self._doc_graphs.items():
                self._save_doc_to_sqlite(conn, doc_key, graph)
            conn.commit()
            logger.info(f"已将 {len(self._doc_graphs)} 个文档图谱从 JSON 迁移到 SQLite")
        except Exception as e:
            logger.error(f"JSON 到 SQLite 迁移失败: {e}")

    def _save_doc_to_sqlite(self, conn: sqlite3.Connection, doc_key: str, graph: DocumentGraph):
        conn.execute(
            "INSERT OR REPLACE INTO kg_doc_graphs (doc_key, updated_at) VALUES (?, datetime('now'))",
            (doc_key,)
        )

        conn.execute("DELETE FROM kg_nodes WHERE doc_key = ?", (doc_key,))
        conn.execute("DELETE FROM kg_edges WHERE doc_key = ?", (doc_key,))

        for node in graph._nodes.values():
            conn.execute(
                "INSERT INTO kg_nodes (id, doc_key, label, node_type, properties) VALUES (?, ?, ?, ?, ?)",
                (node.id, doc_key, node.label, node.node_type, json.dumps(node.properties, ensure_ascii=False))
            )

        for edge in graph._edges.values():
            conn.execute(
                "INSERT INTO kg_edges (id, doc_key, source, target, relation_type, weight, properties) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (edge.id, doc_key, edge.source, edge.target, edge.relation_type,
                 edge.weight, json.dumps(edge.properties, ensure_ascii=False))
            )

    def _save_doc(self, doc_key: str):
        doc_graph = self._doc_graphs.get(doc_key)
        if not doc_graph:
            return
        try:
            conn = self._get_conn()
            self._save_doc_to_sqlite(conn, doc_key, doc_graph)
            conn.commit()
        except Exception as e:
            logger.error(f"保存图谱到 SQLite 失败 (doc_key={doc_key}): {e}")
            fname = self._doc_key_to_filename(doc_key)
            fpath = os.path.join(self._cache_dir, fname)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(doc_graph.to_dict(), f, ensure_ascii=False, indent=2)
                logger.info(f"已回退到 JSON 保存: {fname}")
            except Exception as je:
                logger.error(f"JSON 回退保存也失败: {je}")

    def get_or_create_doc_graph(self, doc_key: str) -> DocumentGraph:
        with self._lock:
            if doc_key not in self._doc_graphs:
                self._doc_graphs[doc_key] = DocumentGraph(doc_key=doc_key)
            elif self._doc_graphs[doc_key] is None:
                # O6: 按需加载
                self._ensure_graph_loaded(doc_key)
                if self._doc_graphs[doc_key] is None:
                    self._doc_graphs[doc_key] = DocumentGraph(doc_key=doc_key)
            return self._doc_graphs[doc_key]

    def add_node(self, label: str, node_type: str, properties: Optional[Dict] = None) -> KGNode:
        with self._lock:
            global_graph = self.get_or_create_doc_graph("__global__")
            node = global_graph.add_node(label=label, node_type=node_type, properties=properties)
            self._save_doc("__global__")
            return node

    def add_edge(self, source_id: str, target_id: str, relation_type: str,
                 weight: float = 1.0, properties: Optional[Dict] = None) -> Optional[KGEdge]:
        with self._lock:
            global_graph = self.get_or_create_doc_graph("__global__")
            edge = global_graph.add_edge(source_id=source_id, target_id=target_id,
                                         relation_type=relation_type, weight=weight, properties=properties)
            if edge:
                self._save_doc("__global__")
            return edge

    def get_subgraph(self, center_id: str, depth: int = 2) -> Tuple[List[KGNode], List[KGEdge]]:
        with self._lock:
            for graph in self._loaded_graphs():
                node = graph.get_node(center_id)
                if node:
                    return graph.get_subgraph(center_id, depth)
            return [], []

    def get_doc_graph(self, doc_key: str) -> Optional[DocumentGraph]:
        with self._lock:
            if doc_key not in self._doc_graphs:
                # 只为实际存在的文档创建懒加载占位。此前无条件写入
                # ``None`` 会让 ``_ensure_graph_loaded`` 构造一个空图，
                # 导致刚删除的文档再次被当作“存在”。
                try:
                    row = self._get_conn().execute(
                        "SELECT 1 FROM kg_doc_graphs WHERE doc_key = ? LIMIT 1",
                        (doc_key,),
                    ).fetchone()
                except Exception as exc:
                    logger.warning(f"查询文档图谱是否存在失败 (doc_key={doc_key}): {exc}")
                    row = None
                if row is None:
                    return None
                self._doc_graphs[doc_key] = None
                self._ensure_graph_loaded(doc_key)
            elif self._doc_graphs[doc_key] is None:
                self._ensure_graph_loaded(doc_key)
            return self._doc_graphs.get(doc_key)

    def get_nodes_paginated(self, doc_key: str = "", page: int = 1, page_size: int = 50,
                            node_type: str = "") -> Dict[str, Any]:
        """O6: 分页查询节点，减少内存占用。

        Args:
            doc_key: 文档键，为空则查全部
            page: 页码（从1开始）
            page_size: 每页大小
            node_type: 节点类型过滤

        Returns:
            {"nodes": [...], "total": int, "page": int, "page_size": int}
        """
        try:
            conn = self._get_conn()
            conditions = []
            params = []
            if doc_key:
                conditions.append("doc_key = ?")
                params.append(doc_key)
            if node_type:
                conditions.append("node_type = ?")
                params.append(node_type)

            where = " WHERE " + " AND ".join(conditions) if conditions else ""

            # 总数
            count_sql = f"SELECT COUNT(*) FROM kg_nodes{where}"
            total = conn.execute(count_sql, params).fetchone()[0]

            # 分页查询
            offset = (page - 1) * page_size
            query_sql = f"SELECT id, label, node_type, properties FROM kg_nodes{where} ORDER BY label LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            cursor = conn.execute(query_sql, params)

            nodes = []
            for row in cursor.fetchall():
                nodes.append(KGNode(
                    id=row[0], label=row[1], node_type=row[2],
                    properties=json.loads(row[3])
                ).to_dict())

            return {
                "nodes": nodes,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
            }
        except Exception as e:
            logger.error(f"分页查询节点失败: {e}")
            return {"nodes": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    def get_edges_paginated(self, doc_key: str = "", page: int = 1, page_size: int = 50,
                            relation_type: str = "") -> Dict[str, Any]:
        """O6: 分页查询边，减少内存占用。"""
        try:
            conn = self._get_conn()
            conditions = []
            params = []
            if doc_key:
                conditions.append("doc_key = ?")
                params.append(doc_key)
            if relation_type:
                conditions.append("relation_type = ?")
                params.append(relation_type)

            where = " WHERE " + " AND ".join(conditions) if conditions else ""

            total = conn.execute(f"SELECT COUNT(*) FROM kg_edges{where}", params).fetchone()[0]

            offset = (page - 1) * page_size
            query_sql = f"SELECT id, source, target, relation_type, weight, properties FROM kg_edges{where} ORDER BY relation_type LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            cursor = conn.execute(query_sql, params)

            edges = []
            for row in cursor.fetchall():
                edges.append(KGEdge(
                    id=row[0], source=row[1], target=row[2],
                    relation_type=row[3], weight=row[4],
                    properties=json.loads(row[5])
                ).to_dict())

            return {
                "edges": edges,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
            }
        except Exception as e:
            logger.error(f"分页查询边失败: {e}")
            return {"edges": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    def delete_doc_graph(self, doc_key: str) -> bool:
        with self._lock:
            if doc_key not in self._doc_graphs:
                return False
            del self._doc_graphs[doc_key]
            try:
                conn = self._get_conn()
                conn.execute("DELETE FROM kg_nodes WHERE doc_key = ?", (doc_key,))
                conn.execute("DELETE FROM kg_edges WHERE doc_key = ?", (doc_key,))
                conn.execute("DELETE FROM kg_doc_graphs WHERE doc_key = ?", (doc_key,))
                conn.commit()
            except Exception as e:
                logger.error(f"从 SQLite 删除图谱失败: {e}")
            fname = self._doc_key_to_filename(doc_key)
            fpath = os.path.join(self._cache_dir, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
            return True

    def list_doc_graphs(self) -> List[Dict]:
        with self._lock:
            result = []
            for key in list(self._doc_graphs.keys()):
                graph = self._doc_graphs.get(key)
                if graph is None:
                    graph = self._ensure_graph_loaded(key)
                if graph is None:
                    result.append({
                        "doc_key": key,
                        "node_count": 0,
                        "edge_count": 0,
                    })
                    continue
                result.append({
                    "doc_key": key,
                    "node_count": getattr(graph, 'node_count', 0) or 0,
                    "edge_count": getattr(graph, 'edge_count', 0) or 0,
                })
            return result

    def get_all_nodes(self) -> List[KGNode]:
        with self._lock:
            all_nodes = []
            for graph in self._loaded_graphs():
                all_nodes.extend(graph.get_all_nodes())
            return all_nodes

    def get_all_edges(self) -> List[KGEdge]:
        with self._lock:
            all_edges = []
            for graph in self._loaded_graphs():
                all_edges.extend(graph.get_all_edges())
            return all_edges

    def get_node(self, node_id: str) -> Optional[KGNode]:
        with self._lock:
            for graph in self._loaded_graphs():
                node = graph.get_node(node_id)
                if node:
                    return node
            return None

    def search_nodes(self, query: str, limit: int = 20) -> List[KGNode]:
        with self._lock:
            results = []
            query_lower = query.lower()
            for graph in self._loaded_graphs():
                for node in graph.get_all_nodes():
                    if query_lower in node.label.lower():
                        results.append(node)
            return results[:limit]

    def search_nodes_sql(self, query: str, limit: int = 20) -> List[KGNode]:
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT id, label, node_type, properties FROM kg_nodes WHERE label LIKE ? LIMIT ?",
                (f"%{query}%", limit)
            )
            results = []
            for row in cursor.fetchall():
                results.append(KGNode(
                    id=row[0], label=row[1], node_type=row[2],
                    properties=json.loads(row[3])
                ))
            return results
        except Exception as e:
            logger.error(f"SQLite 节点搜索失败: {e}")
            return self.search_nodes(query, limit)

    @property
    def node_count(self) -> int:
        with self._lock:
            db_count = self._count_sqlite_rows("kg_nodes")
            if db_count:
                return db_count
            return sum(g.node_count for g in self._doc_graphs.values() if g is not None)

    @property
    def edge_count(self) -> int:
        with self._lock:
            db_count = self._count_sqlite_rows("kg_edges")
            if db_count:
                return db_count
            return sum(g.edge_count for g in self._doc_graphs.values() if g is not None)


kg_store = KnowledgeGraphStore()
