import asyncio
import logging
from typing import List, Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class RAGEvaluator:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，评估将使用规则回退模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("RAG评估 GLM 服务初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("RAG评估 GLM 服务初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"RAG评估 GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"RAG评估 GLM 初始化失败: {e}")
            self._glm = None

    async def evaluate(
        self,
        question: str,
        answer: str,
        contexts: List[str],
        ground_truth: Optional[str] = None,
    ) -> Dict[str, Any]:
        results = {}

        results["context_precision"] = await self._eval_context_precision(question, contexts)
        results["context_recall"] = await self._eval_context_recall(question, contexts, ground_truth)
        results["faithfulness"] = await self._eval_faithfulness(answer, contexts)
        results["answer_relevancy"] = await self._eval_answer_relevancy(question, answer)

        if ground_truth:
            results["answer_correctness"] = await self._eval_answer_correctness(answer, ground_truth)
            results["semantic_similarity"] = self._eval_semantic_similarity(answer, ground_truth)
        else:
            results["answer_correctness"] = None
            results["semantic_similarity"] = None

        valid_scores = [v for v in results.values() if v is not None and isinstance(v, (int, float))]
        results["overall_score"] = round(sum(valid_scores) / len(valid_scores), 3) if valid_scores else 0.0

        return results

    async def _eval_context_precision(self, question: str, contexts: List[str]) -> float:
        if not contexts or not self._glm:
            return self._rule_context_precision(question, contexts)

        numbered = "\n".join([f"[{i+1}] {c[:500]}" for i, c in enumerate(contexts)])
        system_prompt = """你是RAG系统检索质量评估专家。评估检索结果与问题的相关性排序质量（Context Precision）。

规则：
- 逐段判断每个上下文片段是否与问题相关
- 靠前的相关片段权重更高
- 无关内容越多分数越低
- 返回0到1之间的分数，1表示全部相关且排序完美

只返回一个0到1之间的数字，不要其他内容。"""

        user_message = f"问题：{question}\n\n检索上下文：\n{numbered}\n\n请评估上下文精度（0-1）："

        try:
            score = await self._call_llm_for_score(system_prompt, user_message)
            return score
        except Exception as e:
            logger.error(f"Context Precision评估失败: {e}")
            return self._rule_context_precision(question, contexts)

    async def _eval_context_recall(self, question: str, contexts: List[str], ground_truth: Optional[str] = None) -> float:
        if not self._glm:
            return self._rule_context_recall(question, contexts)

        if not ground_truth:
            return 0.5

        combined = "\n".join([c[:400] for c in contexts])
        system_prompt = """你是RAG系统检索质量评估专家。评估检索是否覆盖回答所需的全部关键信息（Context Recall）。

规则：
- 判断检索上下文是否包含了标准答案中的所有关键信息
- 缺少关键信息则低分
- 返回0到1之间的分数

只返回一个0到1之间的数字，不要其他内容。"""

        user_message = f"问题：{question}\n\n标准答案：{ground_truth}\n\n检索上下文：\n{combined}\n\n请评估上下文召回率（0-1）："

        try:
            score = await self._call_llm_for_score(system_prompt, user_message)
            return score
        except Exception as e:
            logger.error(f"Context Recall评估失败: {e}")
            return self._rule_context_recall(question, contexts)

    async def _eval_faithfulness(self, answer: str, contexts: List[str]) -> float:
        if not answer or not contexts or not self._glm:
            return self._rule_faithfulness(answer, contexts)

        combined = "\n".join([c[:400] for c in contexts])
        system_prompt = """你是RAG系统生成质量评估专家。评估答案是否严格基于检索上下文，无中生有即"幻觉"（Faithfulness）。

规则：
- 将答案拆成多条事实声明
- 判断每条声明是否能从上下文推出
- 支持数 / 总声明数 = 忠实度
- 编造信息越多分数越低
- 注意：忠实度≠正确性（上下文错但答案忠于上下文，忠实度也高）

只返回一个0到1之间的数字，不要其他内容。"""

        user_message = f"检索上下文：\n{combined}\n\n生成答案：{answer}\n\n请评估忠实度（0-1）："

        try:
            score = await self._call_llm_for_score(system_prompt, user_message)
            return score
        except Exception as e:
            logger.error(f"Faithfulness评估失败: {e}")
            return self._rule_faithfulness(answer, contexts)

    async def _eval_answer_relevancy(self, question: str, answer: str) -> float:
        if not answer or not self._glm:
            return self._rule_answer_relevancy(question, answer)

        system_prompt = """你是RAG系统生成质量评估专家。评估答案是否直接回应问题（Answer Relevancy）。

规则：
- 答案是否切题、直接回应问题
- 通顺但答非所问则低分
- 冗余生成越多分数越低
- 返回0到1之间的分数

只返回一个0到1之间的数字，不要其他内容。"""

        user_message = f"问题：{question}\n\n答案：{answer}\n\n请评估答案相关性（0-1）："

        try:
            score = await self._call_llm_for_score(system_prompt, user_message)
            return score
        except Exception as e:
            logger.error(f"Answer Relevancy评估失败: {e}")
            return self._rule_answer_relevancy(question, answer)

    async def _eval_answer_correctness(self, answer: str, ground_truth: str) -> float:
        if not self._glm:
            return 0.5

        system_prompt = """你是RAG系统评估专家。评估答案与标准答案的事实一致性（Answer Correctness）。

规则：
- 兼顾精确与完整
- 与标准答案越接近分数越高
- 返回0到1之间的分数

只返回一个0到1之间的数字，不要其他内容。"""

        user_message = f"标准答案：{ground_truth}\n\n生成答案：{answer}\n\n请评估答案正确性（0-1）："

        try:
            score = await self._call_llm_for_score(system_prompt, user_message)
            return score
        except Exception as e:
            logger.error(f"Answer Correctness评估失败: {e}")
            return 0.5

    def _eval_semantic_similarity(self, answer: str, ground_truth: str) -> float:
        if not answer or not ground_truth:
            return 0.0

        answer_set = set(answer.lower().split())
        truth_set = set(ground_truth.lower().split())

        if not truth_set:
            return 0.0

        intersection = answer_set & truth_set
        union = answer_set | truth_set
        jaccard = len(intersection) / len(union) if union else 0.0

        return round(min(1.0, jaccard * 2.5), 3)

    async def _call_llm_for_score(self, system_prompt: str, user_message: str) -> float:
        def _call():
            return self._glm.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=64,
                temperature=0.0,
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
        raw = response.choices[0].message.content.strip()

        import re
        numbers = re.findall(r'[0-9]*\.?[0-9]+', raw)
        if numbers:
            score = float(numbers[0])
            return round(max(0.0, min(1.0, score)), 3)
        return 0.5

    def _rule_context_precision(self, question: str, contexts: List[str]) -> float:
        if not contexts:
            return 0.0
        q_words = set(question.lower().split())
        if not q_words:
            return 0.5
        scores = []
        for i, ctx in enumerate(contexts):
            c_words = set(ctx.lower().split())
            overlap = len(q_words & c_words) / max(len(q_words), 1)
            weight = 1.0 / (i + 1)
            scores.append(overlap * weight)
        total_weight = sum(1.0 / (i + 1) for i in range(len(contexts)))
        return round(sum(scores) / total_weight if total_weight > 0 else 0.0, 3)

    def _rule_context_recall(self, question: str, contexts: List[str]) -> float:
        if not contexts:
            return 0.0
        q_words = set(question.lower().split())
        if not q_words:
            return 0.5
        combined = " ".join(contexts).lower()
        found = sum(1 for w in q_words if w in combined)
        return round(found / len(q_words), 3)

    def _rule_faithfulness(self, answer: str, contexts: List[str]) -> float:
        if not answer or not contexts:
            return 0.0
        combined = " ".join(contexts).lower()
        a_words = set(answer.lower().split())
        if not a_words:
            return 0.5
        supported = sum(1 for w in a_words if w in combined)
        return round(supported / len(a_words), 3)

    def _rule_answer_relevancy(self, question: str, answer: str) -> float:
        if not answer or not question:
            return 0.0
        q_words = set(question.lower().split())
        a_words = set(answer.lower().split())
        if not q_words:
            return 0.5
        overlap = len(q_words & a_words)
        return round(min(1.0, overlap / len(q_words) * 1.5), 3)


rag_evaluator = RAGEvaluator()
