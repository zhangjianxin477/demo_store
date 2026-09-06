"""
文档记忆锚点提取服务

核心思路：
1. LLM 像人一样阅读每篇文章，提炼"记忆锚点"（核心主题、关键论点、重要实体、方法结论等）
2. 通过锚点的语义相似度和关联度，将相关文档用线条连接
3. 形成脉络清晰的文档关系图谱
"""

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MemoryAnchor:
    """文档的记忆锚点"""
    anchor_id: str = ""
    doc_id: str = ""
    doc_title: str = ""
    # 锚点类型：core_topic / key_argument / key_entity / method_conclusion / data_finding
    anchor_type: str = "core_topic"
    # 锚点内容（简短描述）
    content: str = ""
    # 锚点的语义向量（用于相似度计算）
    embedding: Optional[List[float]] = None
    # 相关关键词
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "anchor_id": self.anchor_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "anchor_type": self.anchor_type,
            "content": self.content,
            "keywords": self.keywords,
        }


@dataclass
class DocumentProfile:
    """文档画像：一篇文章的完整记忆锚点集合"""
    doc_id: str = ""
    doc_title: str = ""
    filename: str = ""
    content_length: int = 0
    anchors: List[MemoryAnchor] = field(default_factory=list)
    # 文档摘要
    summary: str = ""
    # 文档主题标签
    topic_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "filename": self.filename,
            "content_length": self.content_length,
            "summary": self.summary,
            "topic_tags": self.topic_tags,
            "anchors": [a.to_dict() for a in self.anchors],
        }


@dataclass
class DocumentRelation:
    """文档间关系"""
    source_doc_id: str = ""
    target_doc_id: str = ""
    # 关系类型：thematic_similarity / shared_entities / causal_link / complementary / contradicts
    relation_type: str = "thematic_similarity"
    # 关系强度 0~1
    weight: float = 0.5
    # 关系原因说明
    reason: str = ""
    # 支撑此关系的锚点对
    anchor_pairs: List[Tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "source_doc_id": self.source_doc_id,
            "target_doc_id": self.target_doc_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "reason": self.reason,
        }


class AnchorExtractor:
    """文档记忆锚点提取器"""

    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，锚点提取将使用关键词回退模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("AnchorExtractor: GLM 初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("AnchorExtractor: GLM 初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"AnchorExtractor: GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"AnchorExtractor: GLM 初始化失败: {e}")
            self._glm = None

    async def extract_profile(self, doc_id: str, title: str, content: str,
                              filename: str = "") -> DocumentProfile:
        """提取一篇文档的完整画像（摘要 + 记忆锚点）"""
        profile = DocumentProfile(
            doc_id=doc_id,
            doc_title=title,
            filename=filename,
            content_length=len(content),
        )

        if not content or not content.strip():
            return profile

        if self._glm:
            profile = await self._extract_with_llm(profile, content)
        else:
            profile = self._extract_with_keywords(profile, content)

        return profile

    async def _extract_with_llm(self, profile: DocumentProfile, content: str) -> DocumentProfile:
        """使用 LLM 提取文档画像"""
        # 截取内容（避免超长）
        max_chars = 8000
        text = content[:max_chars]
        if len(content) > max_chars:
            text += f"\n\n[...文档共{len(content)}字，已截取前{max_chars}字...]"

        system_prompt = """你是一位深度阅读专家。你的任务是像人一样仔细阅读一篇文章，提炼出"记忆锚点"。

记忆锚点是文章中最具标识性和关联性的信息点，就像人脑记忆中的关键节点。好的记忆锚点应该：
1. 能让人快速理解这篇文章的核心内容
2. 能与其他文章建立有意义的关联
3. 包含具体的概念、方法、结论或数据

请提取以下类型的记忆锚点：
- core_topic: 文章的核心主题（1-2个）
- key_argument: 关键论点或观点（2-4个）
- key_entity: 文中提及的重要实体（人/组织/技术/概念等，3-5个）
- method_conclusion: 方法、流程或结论（1-3个）
- data_finding: 重要数据或发现（1-2个）

同时请提供：
- summary: 一句话摘要（50字以内）
- topic_tags: 3-5个主题标签

请严格按JSON格式返回（不要输出markdown标记）：
{
  "summary": "一句话摘要",
  "topic_tags": ["标签1", "标签2"],
  "anchors": [
    {"anchor_type": "core_topic", "content": "锚点内容描述", "keywords": ["关键词1", "关键词2"]},
    {"anchor_type": "key_argument", "content": "锚点内容描述", "keywords": ["关键词"]}
  ]
}"""

        user_message = f"文档标题：{profile.doc_title}\n\n文档内容：\n{text}"

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=2000,
                    temperature=0.1,
                )

            raw_response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=120)
            raw = raw_response.choices[0].message.content or ""

            # 解析响应
            parsed = self._parse_response(raw)
            profile.summary = parsed.get("summary", "")
            profile.topic_tags = parsed.get("topic_tags", [])

            for i, anchor_data in enumerate(parsed.get("anchors", [])):
                anchor = MemoryAnchor(
                    anchor_id=f"{profile.doc_id}_anchor_{i}",
                    doc_id=profile.doc_id,
                    doc_title=profile.doc_title,
                    anchor_type=anchor_data.get("anchor_type", "core_topic"),
                    content=anchor_data.get("content", ""),
                    keywords=anchor_data.get("keywords", []),
                )
                if anchor.content:
                    profile.anchors.append(anchor)

            logger.info(f"文档 '{profile.doc_title}' 提取了 {len(profile.anchors)} 个记忆锚点")

        except asyncio.TimeoutError:
            logger.warning(f"文档 '{profile.doc_title}' 锚点提取超时，使用关键词回退")
            profile = self._extract_with_keywords(profile, content)
        except Exception as e:
            logger.warning(f"文档 '{profile.doc_title}' 锚点提取失败: {e}，使用关键词回退")
            profile = self._extract_with_keywords(profile, content)

        return profile

    def _extract_with_keywords(self, profile: DocumentProfile, content: str) -> DocumentProfile:
        """关键词回退模式：当 LLM 不可用时，使用简单规则提取锚点"""
        import re

        # 提取标题作为核心主题
        profile.anchors.append(MemoryAnchor(
            anchor_id=f"{profile.doc_id}_anchor_0",
            doc_id=profile.doc_id,
            doc_title=profile.doc_title,
            anchor_type="core_topic",
            content=profile.doc_title,
            keywords=[profile.doc_title],
        ))

        # 按段落分割，取前几个段落的关键句
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        anchor_idx = 1

        for para in paragraphs[:5]:
            # 取每个段落的第一句作为关键论点
            sentences = re.split(r'[。！？\.\!\?]', para)
            first_sentence = sentences[0].strip() if sentences else para[:100]
            if len(first_sentence) > 10:
                profile.anchors.append(MemoryAnchor(
                    anchor_id=f"{profile.doc_id}_anchor_{anchor_idx}",
                    doc_id=profile.doc_id,
                    doc_title=profile.doc_title,
                    anchor_type="key_argument",
                    content=first_sentence[:200],
                    keywords=first_sentence[:50].split(),
                ))
                anchor_idx += 1

            if anchor_idx > 8:
                break

        # 简单摘要
        profile.summary = content[:100].replace('\n', ' ')
        profile.topic_tags = [profile.doc_title]

        return profile

    def _parse_response(self, raw: str) -> Dict:
        """解析 LLM 响应"""
        # 尝试直接解析
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 块
        import re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 尝试修复截断的 JSON
        partial = raw.strip()
        if partial.startswith('{'):
            try:
                # 补全缺失的括号
                open_braces = partial.count('{') - partial.count('}')
                open_brackets = partial.count('[') - partial.count(']')
                partial += ']' * max(0, open_brackets) + '}' * max(0, open_braces)
                return json.loads(partial)
            except json.JSONDecodeError:
                pass

        logger.warning(f"锚点提取响应解析失败: {raw[:200]}")
        return {"summary": "", "topic_tags": [], "anchors": []}


class DocumentRelationBuilder:
    """文档间关系构建器：基于记忆锚点的相似度和关联度"""

    # 关系类型定义
    RELATION_TYPES = {
        "thematic_similarity": {
            "label": "主题相似",
            "style": "solid",
            "color": "#3b82f6",
            "description": "两篇文档讨论相似的主题",
        },
        "shared_entities": {
            "label": "共享实体",
            "style": "dashed",
            "color": "#f59e0b",
            "description": "两篇文档涉及相同的关键实体",
        },
        "causal_link": {
            "label": "因果关联",
            "style": "solid",
            "color": "#ef4444",
            "description": "一篇文档的内容是另一篇的原因或结果",
        },
        "complementary": {
            "label": "互补关系",
            "style": "dashed",
            "color": "#10b981",
            "description": "两篇文档从不同角度讨论同一话题",
        },
        "contradicts": {
            "label": "矛盾对立",
            "style": "dotted",
            "color": "#dc2626",
            "description": "两篇文档的观点相互矛盾",
        },
    }

    def __init__(self):
        self._glm = None
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
            except Exception:
                self._glm = None
        except Exception:
            self._glm = None

    async def build_relations(self, profiles: List[DocumentProfile],
                              use_embedding: bool = True) -> List[DocumentRelation]:
        """构建文档间关系"""
        relations = []

        # 第一层：基于关键词和主题标签的关联
        tag_relations = self._build_tag_based_relations(profiles)
        relations.extend(tag_relations)

        # 第二层：基于锚点内容相似度的关联
        anchor_relations = self._build_anchor_similarity_relations(profiles)
        relations.extend(anchor_relations)

        # 第三层：基于 embedding 的语义关联（如果可用）
        if use_embedding:
            embedding_relations = await self._build_embedding_relations(profiles)
            relations.extend(embedding_relations)

        # 第四层：LLM 深度关联分析（对候选对进行二次判断）
        if self._glm and len(profiles) <= 30:
            llm_relations = await self._build_llm_relations(profiles, relations)
            relations.extend(llm_relations)

        # 去重和合并
        relations = self._merge_relations(relations)

        return relations

    def _build_tag_based_relations(self, profiles: List[DocumentProfile]) -> List[DocumentRelation]:
        """基于主题标签的关联"""
        relations = []
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                p1, p2 = profiles[i], profiles[j]
                # 计算标签重叠度
                tags1 = set(p1.topic_tags)
                tags2 = set(p2.topic_tags)
                common_tags = tags1 & tags2
                if not common_tags:
                    continue

                # Jaccard 相似度
                union_tags = tags1 | tags2
                similarity = len(common_tags) / len(union_tags) if union_tags else 0

                if similarity >= 0.2:  # 至少 20% 标签重叠
                    relations.append(DocumentRelation(
                        source_doc_id=p1.doc_id,
                        target_doc_id=p2.doc_id,
                        relation_type="thematic_similarity",
                        weight=min(similarity * 2, 1.0),
                        reason=f"共享主题标签: {', '.join(list(common_tags)[:5])}",
                    ))

        return relations

    def _build_anchor_similarity_relations(self, profiles: List[DocumentProfile]) -> List[DocumentRelation]:
        """基于锚点关键词重叠的关联"""
        relations = []

        # 收集所有锚点的关键词
        doc_keywords = {}
        for p in profiles:
            keywords = set()
            for anchor in p.anchors:
                for kw in anchor.keywords:
                    keywords.add(kw.lower())
            doc_keywords[p.doc_id] = keywords

        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                p1, p2 = profiles[i], profiles[j]
                kw1 = doc_keywords.get(p1.doc_id, set())
                kw2 = doc_keywords.get(p2.doc_id, set())

                common = kw1 & kw2
                if not common:
                    continue

                # 关键词重叠度
                union = kw1 | kw2
                similarity = len(common) / len(union) if union else 0

                if similarity >= 0.15 or len(common) >= 3:
                    # 判断关系类型
                    rel_type = "shared_entities" if len(common) >= 3 else "thematic_similarity"
                    relations.append(DocumentRelation(
                        source_doc_id=p1.doc_id,
                        target_doc_id=p2.doc_id,
                        relation_type=rel_type,
                        weight=min(similarity * 1.5 + 0.2, 1.0),
                        reason=f"共享关键词: {', '.join(list(common)[:8])}",
                    ))

        return relations

    async def _build_embedding_relations(self, profiles: List[DocumentProfile]) -> List[DocumentRelation]:
        """基于 embedding 的语义关联"""
        relations = []

        try:
            from app.rag.embedding_service import embedding_service

            # 为每个文档的摘要+锚点内容生成 embedding
            doc_texts = {}
            for p in profiles:
                text_parts = [p.summary]
                for anchor in p.anchors:
                    text_parts.append(anchor.content)
                doc_texts[p.doc_id] = " ".join(text_parts)

            # 批量生成 embedding
            doc_ids = list(doc_texts.keys())
            texts = [doc_texts[did] for did in doc_ids]

            embeddings = embedding_service.embed(texts)

            # 计算两两相似度
            import numpy as np
            emb_matrix = np.array(embeddings)
            # 归一化
            norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1
            emb_matrix = emb_matrix / norms

            similarity_matrix = emb_matrix @ emb_matrix.T

            for i in range(len(doc_ids)):
                for j in range(i + 1, len(doc_ids)):
                    sim = float(similarity_matrix[i, j])
                    if sim >= 0.6:  # 语义相似度阈值
                        relations.append(DocumentRelation(
                            source_doc_id=doc_ids[i],
                            target_doc_id=doc_ids[j],
                            relation_type="thematic_similarity",
                            weight=min(sim, 1.0),
                            reason=f"语义相似度: {sim:.2f}",
                        ))

        except Exception as e:
            logger.warning(f"Embedding 关联构建失败: {e}")

        return relations

    async def _build_llm_relations(self, profiles: List[DocumentProfile],
                                   existing_relations: List[DocumentRelation]) -> List[DocumentRelation]:
        """使用 LLM 对文档对进行深度关联分析"""
        relations = []

        # 已有关系的文档对（避免重复分析）
        existing_pairs = set()
        for r in existing_relations:
            pair = tuple(sorted([r.source_doc_id, r.target_doc_id]))
            existing_pairs.add(pair)

        # 只对没有关系的文档对进行 LLM 分析（限制数量）
        candidate_pairs = []
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                pair = tuple(sorted([profiles[i].doc_id, profiles[j].doc_id]))
                if pair not in existing_pairs:
                    candidate_pairs.append((profiles[i], profiles[j]))

        # 限制最多分析 20 对
        candidate_pairs = candidate_pairs[:20]

        if not candidate_pairs:
            return relations

        system_prompt = """你是文档关联分析专家。请分析两篇文档之间是否存在关联关系。

关系类型：
- thematic_similarity: 主题相似（讨论相似的话题）
- shared_entities: 共享实体（涉及相同的人/组织/技术/概念）
- causal_link: 因果关联（一篇是另一篇的原因/结果/前提）
- complementary: 互补关系（从不同角度讨论同一话题）
- contradicts: 矛盾对立（观点相互矛盾）

如果两篇文档没有明显关联，返回 null。

请按JSON格式返回（不要输出markdown标记）：
{"relation_type": "类型", "weight": 0.8, "reason": "关联原因说明"}
或返回：null"""

        for p1, p2 in candidate_pairs:
            # 构建精简的文档摘要
            summary1 = f"标题：{p1.doc_title}\n摘要：{p1.summary}\n锚点：{'; '.join(a.content[:60] for a in p1.anchors[:5])}"
            summary2 = f"标题：{p2.doc_title}\n摘要：{p2.summary}\n锚点：{'; '.join(a.content[:60] for a in p2.anchors[:5])}"

            user_message = f"文档A：\n{summary1}\n\n文档B：\n{summary2}"

            try:
                def _call(msg=user_message):
                    return self._glm.chat.completions.create(
                        model=settings.OPENAI_MODEL,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": msg},
                        ],
                        max_tokens=200,
                        temperature=0.1,
                    )

                raw_response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
                raw = raw_response.choices[0].message.content or ""

                import re
                json_match = re.search(r'\{[\s\S]*\}', raw)
                if json_match:
                    result = json.loads(json_match.group())
                    if result and result.get("relation_type"):
                        relations.append(DocumentRelation(
                            source_doc_id=p1.doc_id,
                            target_doc_id=p2.doc_id,
                            relation_type=result["relation_type"],
                            weight=float(result.get("weight", 0.5)),
                            reason=result.get("reason", ""),
                        ))

            except Exception as e:
                logger.debug(f"LLM 文档关联分析失败 ({p1.doc_title} - {p2.doc_title}): {e}")
                continue

        return relations

    def _merge_relations(self, relations: List[DocumentRelation]) -> List[DocumentRelation]:
        """合并同一文档对的多个关系"""
        pair_map: Dict[tuple, DocumentRelation] = {}

        for r in relations:
            pair = tuple(sorted([r.source_doc_id, r.target_doc_id]))
            if pair in pair_map:
                existing = pair_map[pair]
                # 保留权重更高的关系
                if r.weight > existing.weight:
                    # 合并原因
                    if r.reason and r.reason != existing.reason:
                        existing.reason = f"{existing.reason}; {r.reason}"
                    existing.weight = r.weight
                    existing.relation_type = r.relation_type
                elif r.reason and r.reason != existing.reason:
                    existing.reason = f"{existing.reason}; {r.reason}"
            else:
                pair_map[pair] = DocumentRelation(
                    source_doc_id=r.source_doc_id,
                    target_doc_id=r.target_doc_id,
                    relation_type=r.relation_type,
                    weight=r.weight,
                    reason=r.reason,
                )

        return list(pair_map.values())


# 全局实例
anchor_extractor = AnchorExtractor()
relation_builder = DocumentRelationBuilder()
