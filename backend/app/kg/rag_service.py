import logging
import asyncio
from typing import List, Optional, Dict, Any

from app.core.config import settings
from app.kg.store import kg_store, KGNode, KGEdge

logger = logging.getLogger(__name__)


class KGRAGService:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，KG-RAG 将使用模拟模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("KG-RAG GLM 服务初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("KG-RAG GLM 服务初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"GLM 初始化失败: {e}")
            self._glm = None

    async def query(self, question: str) -> Dict[str, Any]:
        linked_entities = self._link_entities(question)
        paths = self._find_paths(linked_entities)
        context = self._build_context(linked_entities, paths)
        has_graph = kg_store.node_count > 0

        if not linked_entities and not paths:
            answer = self._mock_answer(question, linked_entities)
        else:
            answer = await self._generate_answer(question, linked_entities, paths, context)

        return {
            "answer": answer,
            "entities": [e.to_dict() for e in linked_entities],
            "paths": paths,
            "metadata": {
                "entity_count": len(linked_entities),
                "path_count": len(paths),
                "has_graph": has_graph,
            },
        }

    def _link_entities(self, question: str) -> List[KGNode]:
        all_nodes = kg_store.get_all_nodes()
        linked = []
        question_lower = question.lower()

        for node in all_nodes:
            if node.label.lower() in question_lower:
                linked.append(node)
            elif node.properties:
                for v in node.properties.values():
                    if isinstance(v, str) and v.lower() in question_lower:
                        linked.append(node)
                        break

        if not linked:
            for node in all_nodes:
                for char in node.label:
                    if len(char) > 1 and char in question:
                        linked.append(node)
                        break
                if len(linked) >= 5:
                    break

        return linked[:10]

    def _find_paths(self, entities: List[KGNode]) -> List[Dict]:
        paths = []
        if len(entities) < 2:
            return paths

        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                nodes, edges = kg_store.get_subgraph(entities[i].id, depth=2)
                if entities[j].id in [n.id for n in nodes]:
                    path_edges = [e for e in edges if e.source == entities[i].id or e.target == entities[i].id]
                    if path_edges:
                        paths.append({
                            "start": entities[i].label,
                            "end": entities[j].label,
                            "edges": [
                                {
                                    "source": e.source,
                                    "target": e.target,
                                    "relation": e.relation_type,
                                    "weight": e.weight,
                                }
                                for e in path_edges[:3]
                            ],
                        })

        return paths[:5]

    def _build_context(self, entities: List[KGNode], paths: List[Dict]) -> str:
        parts = []
        if entities:
            parts.append("相关实体:")
            for e in entities[:10]:
                parts.append(f"- {e.label} ({e.node_type})")

        if paths:
            parts.append("\n实体关系:")
            for p in paths:
                parts.append(f"- {p['start']} → {p['end']}")

        return "\n".join(parts)

    async def _generate_answer(self, question: str, entities: List[KGNode], paths: List[Dict], context: str) -> str:
        if not self._glm:
            return self._mock_answer(question, entities)

        has_graph_data = bool(entities) or bool(paths)

        if has_graph_data:
            system_prompt = """你是一个基于知识图谱的智能知识助手。请基于提供的知识图谱实体和关系信息回答问题。
回答要求简洁、准确，引用具体的实体和关系。如果信息不足，请明确说明。"""
        else:
            system_prompt = """你是一个智能问答助手。用户提出了一个知识图谱中没有相关实体的问题，请你基于自身知识直接回答。
回答要简洁、准确、友好。回答完毕后，在末尾添加一行提示：
"[提示] 以上回答基于通用知识，如需查询知识图谱中的专业内容，请先上传文档构建知识图谱后提问。"
"""

        entity_text = "\n".join([f"- {e.label} ({e.node_type})" for e in entities[:10]]) if entities else "无相关实体"
        path_text = "\n".join([f"- {p['start']} → {p['end']}" for p in paths[:5]]) if paths else "无相关路径"

        if has_graph_data:
            user_message = f"""用户问题：{question}

知识图谱实体：
{entity_text}

实体关系路径：
{path_text}

扩展上下文：
{context}

请基于以上信息回答问题。"""
        else:
            user_message = f"用户问题：{question}\n\n请直接回答："

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=2048,
                    temperature=0.7,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=60)
            content = response.choices[0].message.content
            if content:
                return content.strip()
            return self._mock_answer(question, entities)
        except Exception as e:
            logger.error(f"GLM 生成回答失败: {e}")
            return self._mock_answer(question, entities)

    def _mock_answer(self, question: str, entities: List[KGNode]) -> str:
        if not entities:
            return "当前图谱没有命中相关节点。我会继续尝试使用文件资源和知识库检索；如果仍没有结果，请先上传/创建 Markdown 文件，或在知识库中完成分块索引。"

        answer = f"关于「{question}」的查询结果：\n\n相关实体：\n"
        for e in entities[:5]:
            answer += f"- {e.label} ({e.node_type})\n"
        return answer

    async def rewrite_query(self, question: str) -> str:
        if not self._glm:
            return question

        system_prompt = (
            "你是知识图谱查询改写引擎，将用户输入改写为适合实体检索的精确查询。\n"
            "## 改写原则\n"
            "1. 语义不变：严格保持原意，不添加未暗示的信息\n"
            "2. 实体聚焦：提取核心实体和关系，去除口语化修饰\n"
            "3. 精确表达：使用规范术语替换口语（如\"老人院\"→\"老年人照料设施\"）\n"
            "4. 关系显化：将隐含关系转为显式表达\n"
            "5. 消歧纠错：修正错别字和歧义表述\n"
            "## 策略示例\n"
            "- \"防火门要多宽\"→\"防火门最小净宽度\"\n"
            "- \"老人院消防要求\"→\"老年人照料设施 消防设计要求\"\n"
            "- \"建规5.5.13\"→\"建筑设计防火规范 5.5.13 疏散楼梯\"\n"
            "## 输出\n"
            "只返回改写后的查询，不要解释、不要编号。"
        )

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"改写：{question}"},
                    ],
                    max_tokens=256,
                    temperature=0.2,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
            rewritten = response.choices[0].message.content.strip()
            rewritten = rewritten.strip('"\'""''《》【】').strip()
            if rewritten.startswith(('1.', '2.', '1、', '一、')):
                import re
                rewritten = re.sub(r'^[\d一二三四五六七八九十]+[.、]\s*', '', rewritten)
            return rewritten or question
        except Exception as e:
            logger.error(f"KG查询改写失败: {e}")
            return question


kg_rag_service = KGRAGService()
