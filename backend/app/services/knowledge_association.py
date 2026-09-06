import os
import json
import uuid
import logging
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.kg.store import kg_store, KGNode, KGEdge

logger = logging.getLogger(__name__)


class KnowledgeAssociationService:
    def __init__(self):
        self._glm = None
        self._association_dir = os.path.join(settings.DATA_DIR, "kg_associations")
        os.makedirs(self._association_dir, exist_ok=True)
        self._aliases: Dict[str, Dict] = {}
        self._term_registry: Dict[str, Dict] = {}
        self._load_data()
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
            except Exception as e:
                logger.error(f"知识关联 GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"知识关联 GLM 初始化失败: {e}")
            self._glm = None

    def _load_data(self):
        aliases_file = os.path.join(self._association_dir, "aliases.json")
        if os.path.exists(aliases_file):
            try:
                with open(aliases_file, "r", encoding="utf-8") as f:
                    self._aliases = json.load(f)
            except Exception as e:
                logger.error(f"别名数据加载失败: {e}")
        terms_file = os.path.join(self._association_dir, "terms.json")
        if os.path.exists(terms_file):
            try:
                with open(terms_file, "r", encoding="utf-8") as f:
                    self._term_registry = json.load(f)
            except Exception as e:
                logger.error(f"术语数据加载失败: {e}")

    def _save_aliases(self):
        aliases_file = os.path.join(self._association_dir, "aliases.json")
        with open(aliases_file, "w", encoding="utf-8") as f:
            json.dump(self._aliases, f, ensure_ascii=False, indent=2)

    def _save_terms(self):
        terms_file = os.path.join(self._association_dir, "terms.json")
        with open(terms_file, "w", encoding="utf-8") as f:
            json.dump(self._term_registry, f, ensure_ascii=False, indent=2)

    def find_similar_pages(self, node_label: str, limit: int = 10) -> List[Dict]:
        all_nodes = kg_store.get_all_nodes()
        similar = []
        target_label = node_label.lower()
        for node in all_nodes:
            if node.label.lower() == target_label:
                continue
            score = self._compute_similarity(node_label, node.label)
            if score > 0.3:
                similar.append({
                    "node_id": node.id,
                    "label": node.label,
                    "node_type": node.node_type,
                    "similarity": round(score, 3),
                })
        similar.sort(key=lambda x: x["similarity"], reverse=True)
        return similar[:limit]

    def _compute_similarity(self, text1: str, text2: str) -> float:
        set1 = set(text1.lower())
        set2 = set(text2.lower())
        if not set1 or not set2:
            return 0.0
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union)

    def find_upstream_downstream(self, node_id: str, depth: int = 3) -> Dict[str, Any]:
        node = kg_store.get_node(node_id)
        if not node:
            return {"upstream": [], "downstream": []}
        upstream = []
        downstream = []
        all_edges = kg_store.get_all_edges()
        visited_up = set()
        visited_down = set()
        current_up = [node_id]
        current_down = [node_id]
        for d in range(depth):
            next_up = []
            next_down = []
            for nid in current_up:
                for edge in all_edges:
                    if edge.target == nid and edge.source not in visited_up:
                        visited_up.add(edge.source)
                        source_node = kg_store.get_node(edge.source)
                        if source_node:
                            upstream.append({
                                "node_id": source_node.id,
                                "label": source_node.label,
                                "relation": edge.relation_type,
                                "depth": d + 1,
                            })
                        next_up.append(edge.source)
            for nid in current_down:
                for edge in all_edges:
                    if edge.source == nid and edge.target not in visited_down:
                        visited_down.add(edge.target)
                        target_node = kg_store.get_node(edge.target)
                        if target_node:
                            downstream.append({
                                "node_id": target_node.id,
                                "label": target_node.label,
                                "relation": edge.relation_type,
                                "depth": d + 1,
                            })
                        next_down.append(edge.target)
            current_up = next_up
            current_down = next_down
        return {"upstream": upstream, "downstream": downstream}

    def register_term(self, term: str, definition: str = "", category: str = "",
                      standard_name: str = "") -> Dict:
        term_id = str(uuid.uuid4())
        entry = {
            "term_id": term_id,
            "term": term,
            "definition": definition,
            "category": category,
            "standard_name": standard_name or term,
            "created_at": datetime.now().isoformat(),
        }
        self._term_registry[term_id] = entry
        self._save_terms()
        return entry

    def list_terms(self, category: Optional[str] = None) -> List[Dict]:
        terms = list(self._term_registry.values())
        if category:
            terms = [t for t in terms if t.get("category") == category]
        return terms

    def update_term(self, term_id: str, term: Optional[str] = None,
                    definition: Optional[str] = None, category: Optional[str] = None,
                    standard_name: Optional[str] = None) -> Optional[Dict]:
        entry = self._term_registry.get(term_id)
        if not entry:
            return None
        if term is not None:
            entry["term"] = term
        if definition is not None:
            entry["definition"] = definition
        if category is not None:
            entry["category"] = category
        if standard_name is not None:
            entry["standard_name"] = standard_name
        self._save_terms()
        return entry

    def delete_term(self, term_id: str) -> bool:
        if term_id not in self._term_registry:
            return False
        del self._term_registry[term_id]
        self._save_terms()
        return True

    def add_alias(self, standard_name: str, alias: str) -> Dict:
        if standard_name not in self._aliases:
            self._aliases[standard_name] = {
                "standard": standard_name,
                "aliases": [],
            }
        if alias not in self._aliases[standard_name]["aliases"]:
            self._aliases[standard_name]["aliases"].append(alias)
        self._save_aliases()
        return self._aliases[standard_name]

    def remove_alias(self, standard_name: str, alias: str) -> bool:
        if standard_name not in self._aliases:
            return False
        if alias in self._aliases[standard_name]["aliases"]:
            self._aliases[standard_name]["aliases"].remove(alias)
            self._save_aliases()
            return True
        return False

    def normalize_entity(self, entity_name: str) -> str:
        for standard, data in self._aliases.items():
            if entity_name == standard:
                return standard
            if entity_name in data.get("aliases", []):
                return standard
        for term_data in self._term_registry.values():
            if entity_name == term_data.get("standard_name"):
                return entity_name
            if entity_name == term_data.get("term"):
                return term_data.get("standard_name", entity_name)
        return entity_name

    def list_aliases(self) -> List[Dict]:
        return list(self._aliases.values())

    async def auto_detect_aliases(self) -> Dict[str, Any]:
        if not self._glm:
            return {"success": False, "error": "LLM未初始化"}
        all_nodes = kg_store.get_all_nodes()
        if not all_nodes:
            return {"success": False, "error": "知识图谱为空"}
        labels = [n.label for n in all_nodes[:100]]
        labels_text = "\n".join(labels)
        system_prompt = """你是一个实体别名检测助手。请从以下实体列表中识别可能是同一概念的不同表达方式。

请以JSON格式返回：
```json
{
  "alias_groups": [
    {
      "standard": "标准名称",
      "aliases": ["别名1", "别名2"]
    }
  ]
}
```
只返回有明确别名关系的组，不确定的不要返回。"""
        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"实体列表：\n{labels_text}"},
                    ],
                    max_tokens=2048,
                    temperature=0.3,
                )
            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=60)
            content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
            import re
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(content)
            groups = parsed.get("alias_groups", [])
            for group in groups:
                standard = group.get("standard", "")
                aliases = group.get("aliases", [])
                if standard:
                    for alias in aliases:
                        self.add_alias(standard, alias)
            return {"success": True, "detected_groups": len(groups)}
        except Exception as e:
            logger.error(f"自动别名检测失败: {e}")
            return {"success": False, "error": str(e)}

    def get_graph_visualization_data(self, doc_key: Optional[str] = None,
                                     center_node_id: Optional[str] = None,
                                     depth: int = 2) -> Dict[str, Any]:
        nodes = []
        edges = []
        if doc_key:
            doc_graph = kg_store.get_doc_graph(doc_key)
            if doc_graph:
                nodes = [n.to_dict() for n in doc_graph.get_all_nodes()]
                edges = [e.to_dict() for e in doc_graph.get_all_edges()]
        elif center_node_id:
            for graph in kg_store._doc_graphs.values():
                sub_nodes, sub_edges = graph.get_subgraph(center_node_id, depth)
                if sub_nodes:
                    nodes = [n.to_dict() for n in sub_nodes]
                    edges = [e.to_dict() for e in sub_edges]
                    break
        else:
            nodes = [n.to_dict() for n in kg_store.get_all_nodes()[:200]]
            edges = [e.to_dict() for e in kg_store.get_all_edges()[:300]]
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        }


knowledge_association_service = KnowledgeAssociationService()
