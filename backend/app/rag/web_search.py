import asyncio
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus

from app.core.config import settings

logger = logging.getLogger(__name__)


class WebSearchService:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            return
        try:
            from openai import OpenAI
            self._glm = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        except Exception as e:
            logger.error(f"WebSearch LLM 初始化失败: {e}")
            self._glm = None

    async def search(self, query: str, max_results: int = 3) -> List[Dict[str, Any]]:
        try:
            results = await self._search_duckduckgo(query, max_results)
            if results:
                return results
        except Exception as e:
            logger.warning(f"DuckDuckGo 搜索失败，尝试 LLM 回退: {e}")

        if self._glm:
            try:
                return await self._search_with_llm(query, max_results)
            except Exception as e:
                logger.error(f"LLM 联网搜索失败: {e}")

        return self._search_mock(query, max_results)

    async def _search_duckduckgo(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        try:
            from duckduckgo_search import DDGS

            def _do_search():
                ddgs = DDGS()
                results = list(ddgs.text(query, max_results=max_results))
                return results

            raw_results = await asyncio.wait_for(asyncio.to_thread(_do_search), timeout=20)

            results = []
            for r in raw_results:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("link", "")),
                    "snippet": r.get("body", r.get("snippet", "")),
                    "source": r.get("source", ""),
                })

            logger.info(f"DuckDuckGo 搜索 '{query}' 返回 {len(results)} 条结果")
            return results
        except ImportError:
            logger.warning("duckduckgo_search 未安装，跳过 DuckDuckGo 搜索")
            return []
        except Exception as e:
            logger.error(f"DuckDuckGo 搜索异常: {e}")
            return []

    async def _search_with_llm(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        system_prompt = f"""你是一个联网搜索助手。用户提出了一个问题，你需要基于自身知识返回相关的信息。
请返回{max_results}个最相关的结果，格式为JSON数组：
```json
[
  {{"title": "标题", "url": "https://example.com/page", "snippet": "摘要内容，包含与查询相关的关键信息", "source": "来源"}}
]
```
确保返回的内容是真实、准确的。只返回JSON数组，不要其他内容。"""

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"搜索查询：{query}"},
                    ],
                    max_tokens=1024,
                    temperature=0.3,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
            raw = response.choices[0].message.content.strip()

            import json
            import re
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw)
            if json_match:
                return json.loads(json_match.group(1))

            brace_start = raw.find('[')
            if brace_start != -1:
                depth = 0
                for i in range(brace_start, len(raw)):
                    if raw[i] == '[':
                        depth += 1
                    elif raw[i] == ']':
                        depth -= 1
                        if depth == 0:
                            return json.loads(raw[brace_start:i + 1])

            return self._search_mock(query, max_results)
        except Exception as e:
            logger.error(f"LLM 搜索解析失败: {e}")
            return self._search_mock(query, max_results)

    def _search_mock(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        return [
            {
                "title": f"关于「{query}」的相关信息",
                "url": f"https://www.example.com/search?q={quote_plus(query)}",
                "snippet": f"这是关于「{query}」的搜索结果摘要。联网搜索功能需要配置API密钥才能获取真实搜索结果。",
                "source": "示例搜索",
            }
        ][:max_results]


web_search_service = WebSearchService()
