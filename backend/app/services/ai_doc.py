import asyncio
import logging
import json
from typing import List, Optional, Dict, Any

from app.core.config import settings
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class AIDocService:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，AI文档处理将使用模拟模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("AI文档处理 GLM 初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("AI文档处理 GLM 初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"AI文档处理 GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"AI文档处理 GLM 初始化失败: {e}")
            self._glm = None

    async def _call_llm(self, system_prompt: str, user_message: str,
                        max_tokens: int = 2048, temperature: float = 0.7) -> str:
        if not self._glm:
            return ""
        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=120)
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return ""

    async def summarize_document(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:8000]
        system_prompt = """你是一个专业的文档摘要助手。请对提供的文档内容进行总结，生成：
1. 文档摘要（200字以内）
2. 核心要点（3-5条）
3. 关键词（5-10个）

请以JSON格式返回：
```json
{
  "summary": "文档摘要",
  "key_points": ["要点1", "要点2", "要点3"],
  "keywords": ["关键词1", "关键词2"]
}
```"""
        user_message = f"文档标题：{page.title}\n\n文档内容：\n{content}"
        result = await self._call_llm(system_prompt, user_message)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        try:
            json_match = __import__("re").search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(result)
            return {"success": True, "data": parsed}
        except Exception:
            return {"success": True, "data": {"summary": result, "key_points": [], "keywords": []}}

    async def split_long_document(self, page_id: str, rule: str = "chapter",
                                  chunk_size: int = 3000) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}

        content = page.content
        if rule == "chapter":
            sections = __import__("re").split(r'(?=^#{1,3}\s)', content, flags=__import__("re").MULTILINE)
            sections = [s.strip() for s in sections if s.strip()]
        elif rule == "char_count":
            sections = []
            for i in range(0, len(content), chunk_size):
                sections.append(content[i:i + chunk_size])
        else:
            paragraphs = content.split("\n\n")
            sections = []
            current = ""
            for p in paragraphs:
                if len(current) + len(p) > chunk_size and current:
                    sections.append(current.strip())
                    current = p
                else:
                    current = current + "\n\n" + p if current else p
            if current.strip():
                sections.append(current.strip())

        created_pages = []
        for i, section in enumerate(sections):
            title = f"{page.title} - 第{i + 1}部分"
            new_page = wiki_store.create_page(
                title=title, content=section, space_id=page.space_id,
                page_type=page.page_type, author="ai",
            )
            created_pages.append({"page_id": new_page.page_id, "title": title})

        return {"success": True, "split_count": len(created_pages), "pages": created_pages}

    async def summarize_chapters(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:10000]
        system_prompt = """你是一个文档章节概括助手。请对提供的文档按章节进行概括，为每个章节生成核心内容摘要。

请以JSON格式返回：
```json
{
  "chapters": [
    {"chapter": "章节标题", "summary": "章节摘要"},
    ...
  ]
}
```"""
        user_message = f"文档标题：{page.title}\n\n文档内容：\n{content}"
        result = await self._call_llm(system_prompt, user_message)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        try:
            json_match = __import__("re").search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(result)
            return {"success": True, "data": parsed}
        except Exception:
            return {"success": True, "data": {"chapters": [{"chapter": "全文", "summary": result}]}}

    async def generate_mindmap(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:8000]
        system_prompt = """你是一个思维导图生成助手。基于文档内容生成结构化思维导图数据。

请以JSON格式返回思维导图结构：
```json
{
  "root": "中心主题",
  "children": [
    {
      "label": "分支1",
      "children": [
        {"label": "子分支1.1"},
        {"label": "子分支1.2"}
      ]
    },
    {
      "label": "分支2",
      "children": [...]
    }
  ]
}
```"""
        user_message = f"文档标题：{page.title}\n\n文档内容：\n{content}"
        result = await self._call_llm(system_prompt, user_message)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        try:
            json_match = __import__("re").search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(result)
            return {"success": True, "data": parsed}
        except Exception:
            return {"success": True, "data": {"root": page.title, "children": []}}

    async def rewrite_text(self, page_id: str, style: str = "formal") -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:6000]
        style_map = {
            "formal": "正式、专业的学术/商务风格",
            "casual": "轻松、通俗的日常风格",
            "concise": "简洁、精炼的表达风格",
            "detailed": "详细、丰富的描述风格",
        }
        style_desc = style_map.get(style, style)
        system_prompt = f"""你是一个文本改写助手。请将以下内容改写为{style_desc}。
保持核心意思不变，调整表达方式和语言风格。只返回改写后的内容，不要添加解释。"""
        result = await self._call_llm(system_prompt, content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        return {"success": True, "rewritten_content": result}

    async def polish_text(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:6000]
        system_prompt = """你是一个文本润色助手。请对以下内容进行润色优化：
1. 修正语法错误
2. 优化表达方式
3. 提升文字流畅度
4. 保持原文风格和意思

只返回润色后的内容，不要添加解释。"""
        result = await self._call_llm(system_prompt, content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        return {"success": True, "polished_content": result}

    async def translate_text(self, page_id: str, target_lang: str = "en") -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:6000]
        lang_map = {
            "en": "英文", "zh": "中文", "ja": "日文", "ko": "韩文",
            "fr": "法文", "de": "德文", "es": "西班牙文", "ru": "俄文",
        }
        target = lang_map.get(target_lang, target_lang)
        system_prompt = f"""你是一个专业翻译助手。请将以下内容翻译为{target}。
保持原文的格式和结构，准确传达原文含义。只返回翻译结果，不要添加解释。"""
        result = await self._call_llm(system_prompt, content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        return {"success": True, "translated_content": result, "target_lang": target_lang}

    async def explain_terms(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:8000]
        system_prompt = """你是一个专业术语解释助手。请从以下文档中识别专业术语，并为每个术语提供简洁明了的解释。

请以JSON格式返回：
```json
{
  "terms": [
    {"term": "术语名称", "explanation": "术语解释"},
    ...
  ]
}
```"""
        user_message = f"文档标题：{page.title}\n\n文档内容：\n{content}"
        result = await self._call_llm(system_prompt, user_message)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        try:
            json_match = __import__("re").search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(result)
            return {"success": True, "data": parsed}
        except Exception:
            return {"success": True, "data": {"terms": []}}


ai_doc_service = AIDocService()
