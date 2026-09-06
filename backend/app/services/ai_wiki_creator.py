import asyncio
import logging
import json
from typing import List, Optional, Dict, Any

from app.core.config import settings
from app.models.wiki import wiki_store, WikiPage

logger = logging.getLogger(__name__)


class AIWikiCreatorService:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，AI创作将使用模拟模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("AI创作 GLM 初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("AI创作 GLM 初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"AI创作 GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"AI创作 GLM 初始化失败: {e}")
            self._glm = None

    async def _call_llm(self, system_prompt: str, user_message: str,
                        max_tokens: int = 4096, temperature: float = 0.7) -> str:
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

    async def create_wiki_page(self, topic: str, space_id: str = "default",
                               author: str = "ai") -> Dict[str, Any]:
        system_prompt = """你是一个Wiki内容生成助手。请根据提供的主题，生成一个完整的Wiki页面内容。

要求：
1. 内容结构清晰，使用Markdown格式
2. 包含标题、概述、详细内容、相关参考等部分
3. 内容准确、专业、有深度
4. 适当使用标题层级、列表、表格等格式

只返回Markdown格式的页面内容，不要添加解释。"""
        user_message = f"请生成关于「{topic}」的Wiki页面内容"
        result = await self._call_llm(system_prompt, user_message)
        if not result:
            return {"success": False, "error": "AI生成失败"}
        page = wiki_store.create_page(
            title=topic, content=result, space_id=space_id,
            page_type="markdown", author=author,
        )
        return {"success": True, "page_id": page.page_id, "title": page.title}

    async def generate_from_notes(self, note_ids: List[str], title: str = "",
                                  space_id: str = "default") -> Dict[str, Any]:
        notes_content = []
        for nid in note_ids:
            page = wiki_store.get_page(nid)
            if page:
                notes_content.append(f"### {page.title}\n\n{page.content[:2000]}")
        if not notes_content:
            return {"success": False, "error": "未找到有效的笔记内容"}
        combined = "\n\n---\n\n".join(notes_content)
        if not title:
            title = "整合文档"
        system_prompt = """你是一个文档整合助手。请将以下零散笔记整合为一个结构化的Wiki文档。

要求：
1. 提取各笔记的核心信息
2. 按逻辑关系组织内容结构
3. 消除重复信息
4. 使用Markdown格式，包含适当的标题层级
5. 添加必要的过渡和衔接

只返回整合后的Markdown文档内容。"""
        result = await self._call_llm(system_prompt, combined, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI生成失败"}
        page = wiki_store.create_page(
            title=title, content=result, space_id=space_id,
            page_type="markdown", author="ai",
        )
        return {"success": True, "page_id": page.page_id, "title": page.title}

    async def complete_content(self, page_id: str, direction: str = "expand") -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:4000]
        direction_map = {
            "expand": "请在现有内容基础上扩展补充，增加更多细节和深度",
            "continue": "请续写内容，保持与上文风格一致",
            "detail": "请为现有内容添加更详细的说明和示例",
        }
        direction_desc = direction_map.get(direction, direction)
        system_prompt = f"""你是一个内容补全助手。请基于以下已有内容进行补全。

{direction_desc}

只返回补全的内容，不要重复已有内容。"""
        result = await self._call_llm(system_prompt, content, max_tokens=2048)
        if not result:
            return {"success": False, "error": "AI补全失败"}
        return {"success": True, "completed_content": result}

    async def reorganize_logic(self, page_id: str) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        content = page.content[:8000]
        system_prompt = """你是一个文档逻辑优化助手。请对以下文档进行逻辑梳理和结构优化：

1. 重新组织内容结构，使逻辑更清晰
2. 调整段落顺序，使行文更流畅
3. 添加必要的过渡语句
4. 确保内容层次分明
5. 保持所有原始信息不丢失

只返回优化后的完整Markdown文档内容。"""
        result = await self._call_llm(system_prompt, content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        return {"success": True, "reorganized_content": result}

    async def generate_template(self, template_type: str, topic: str = "",
                                space_id: str = "default") -> Dict[str, Any]:
        template_prompts = {
            "process": "流程文档模板，包含目的、范围、流程步骤、责任分工、注意事项",
            "standard": "规范文档模板，包含适用范围、术语定义、规范要求、检查标准、违规处理",
            "manual": "操作手册模板，包含简介、环境要求、操作步骤、常见问题、附录",
            "report": "报告模板，包含摘要、背景、方法、结果、结论、建议",
            "meeting": "会议纪要模板，包含会议信息、参会人员、议题、决议、待办事项",
            "project": "项目文档模板，包含项目概述、目标、范围、计划、风险、资源需求",
        }
        template_desc = template_prompts.get(template_type, f"{template_type}类型文档模板")
        system_prompt = f"""你是一个标准化文档模板生成助手。请生成一个{template_desc}。

要求：
1. 使用Markdown格式
2. 包含完整的章节结构
3. 每个章节包含提示性说明文字
4. 适当使用占位符标记需要填写的内容
5. {"主题为：" + topic if topic else "使用通用主题"}

只返回模板的Markdown内容。"""
        user_msg = f"请生成{template_desc}的Markdown模板。" + (f"主题为：{topic}" if topic else "")
        result = await self._call_llm(system_prompt, user_msg, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI生成失败"}
        title = f"{topic or '新建'} - {template_type}模板"
        page = wiki_store.create_page(
            title=title, content=result, space_id=space_id,
            page_type="markdown", author="ai",
        )
        return {"success": True, "page_id": page.page_id, "title": page.title}

    async def qa_to_document(self, qa_content: str, title: str = "",
                             space_id: str = "default") -> Dict[str, Any]:
        if not title:
            title = "问答整理文档"
        system_prompt = """你是一个问答转文档助手。请将以下问答内容整理为结构化的Wiki文档。

要求：
1. 按主题分类组织问答
2. 将问答转化为陈述性知识
3. 添加必要的标题和分类
4. 去除重复内容
5. 使用Markdown格式

只返回整理后的文档内容。"""
        result = await self._call_llm(system_prompt, qa_content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        page = wiki_store.create_page(
            title=title, content=result, space_id=space_id,
            page_type="markdown", author="ai",
        )
        return {"success": True, "page_id": page.page_id, "title": page.title}

    async def archive_conversation(self, conversation_content: str, title: str = "",
                                   space_id: str = "default") -> Dict[str, Any]:
        if not title:
            title = "对话归档"
        system_prompt = """你是一个对话归档助手。请将以下对话内容整理并归档为知识库文档。

要求：
1. 提取对话中的关键信息和知识点
2. 按主题组织内容
3. 去除闲聊和无关内容
4. 将对话内容转化为知识性文档
5. 使用Markdown格式

只返回归档后的文档内容。"""
        result = await self._call_llm(system_prompt, conversation_content, max_tokens=4096)
        if not result:
            return {"success": False, "error": "AI处理失败"}
        page = wiki_store.create_page(
            title=title, content=result, space_id=space_id,
            page_type="markdown", author="ai",
        )
        return {"success": True, "page_id": page.page_id, "title": page.title}


ai_wiki_creator_service = AIWikiCreatorService()
