import os
import json
import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)

BUILTIN_TEMPLATES = [
    {
        "template_id": "builtin-meeting",
        "name": "会议纪要",
        "description": "记录会议讨论内容和决议",
        "category": "工作",
        "content": """# 会议纪要

## 基本信息
- **会议主题**：
- **会议时间**：{date}
- **会议地点**：
- **主持人**：
- **参会人员**：

## 议题一

### 讨论内容


### 决议


## 议题二

### 讨论内容


### 决议


## 待办事项
| 序号 | 事项 | 负责人 | 截止日期 | 状态 |
|------|------|--------|----------|------|
| 1    |      |        |          | 待办 |
| 2    |      |        |          | 待办 |

## 下次会议安排
- **时间**：
- **议题**：
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-tech-doc",
        "name": "技术文档",
        "description": "技术方案和设计文档模板",
        "category": "技术",
        "content": """# {title}

## 概述


## 背景与目标


## 技术方案

### 架构设计


### 核心模块


### 数据模型


## 接口定义

### 接口一
- **路径**：
- **方法**：
- **请求参数**：

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
|        |      |      |      |

- **响应示例**：
```json
{}
```

## 非功能性需求
- 性能：
- 安全性：
- 可扩展性：

## 风险与应对


## 里程碑
| 阶段 | 时间 | 交付物 |
|------|------|--------|
|      |      |        |
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-project-plan",
        "name": "项目计划",
        "description": "项目规划与进度跟踪",
        "category": "管理",
        "content": """# {title}

## 项目概述


## 项目目标
1. 
2. 
3. 

## 团队成员
| 角色 | 姓名 | 职责 |
|------|------|------|
|      |      |      |

## 工作分解

### 阶段一：需求分析
- 时间：
- 交付物：
- 负责人：

### 阶段二：设计开发
- 时间：
- 交付物：
- 负责人：

### 阶段三：测试上线
- 时间：
- 交付物：
- 负责人：

## 风险管理
| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
|      |      |      |          |

## 进度跟踪
- [ ] 需求确认
- [ ] 方案评审
- [ ] 开发完成
- [ ] 测试通过
- [ ] 正式上线
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-weekly",
        "name": "周报",
        "description": "每周工作总结与计划",
        "category": "工作",
        "content": """# 周报 - {date}

## 本周完成
1. 
2. 
3. 

## 本周问题
1. 
2. 

## 下周计划
1. 
2. 
3. 

## 需要协助
- 
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-api-doc",
        "name": "API文档",
        "description": "RESTful API接口文档",
        "category": "技术",
        "content": """# {title} API文档

## 基本信息
- **Base URL**：
- **认证方式**：Bearer Token
- **版本**：v1

## 接口列表

### 1. 接口名称

**请求**
- URL: `GET /api/v1/resource`
- Method: `GET`

**请求头**
| Header | 值 | 说明 |
|--------|------|------|
| Authorization | Bearer {token} | 认证令牌 |

**请求参数**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
|      |      |      |      |

**响应**
```json
{
  "code": 200,
  "message": "success",
  "data": {}
}
```

**错误码**
| 错误码 | 说明 |
|--------|------|
| 400    | 参数错误 |
| 401    | 未授权 |
| 404    | 资源不存在 |

## 数据模型

### ModelName
| 字段 | 类型 | 说明 |
|------|------|------|
| id   | string | 唯一标识 |
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-study-note",
        "name": "学习笔记",
        "description": "知识学习与整理笔记",
        "category": "学习",
        "content": """# {title}

## 学习目标


## 核心概念


## 详细笔记

### 1. 


### 2. 


## 关键要点
- 
- 
- 

## 疑问与思考
1. 
2. 

## 参考资料
1. 
2. 
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-reading",
        "name": "读书笔记",
        "description": "书籍阅读记录与思考",
        "category": "学习",
        "content": """# 《{title}》读书笔记

## 书籍信息
- **书名**：
- **作者**：
- **出版社**：
- **阅读时间**：{date}

## 一句话总结


## 核心观点
1. 
2. 
3. 

## 精彩摘录
> 

> 

## 个人思考


## 行动计划
- [ ] 
- [ ] 

## 推荐指数
⭐⭐⭐⭐⭐
""",
        "is_builtin": True,
    },
    {
        "template_id": "builtin-decision",
        "name": "决策记录",
        "description": "重要决策的记录与追踪",
        "category": "管理",
        "content": """# 决策记录：{title}

## 决策背景


## 决策选项

### 选项A
- 描述：
- 优点：
- 缺点：

### 选项B
- 描述：
- 优点：
- 缺点：

## 决策结果
- **选择**：
- **原因**：

## 影响范围


## 后续行动
| 行动 | 负责人 | 截止日期 |
|------|--------|----------|
|      |        |          |

## 复盘
- 日期：
- 结果：
- 经验教训：
""",
        "is_builtin": True,
    },
]


class WikiTemplateService:
    def __init__(self):
        self._template_dir = os.path.join(settings.DATA_DIR, "wiki_templates")
        os.makedirs(self._template_dir, exist_ok=True)
        self._custom_templates_file = os.path.join(self._template_dir, "custom_templates.json")
        self._custom_templates: Dict[str, Dict] = {}
        self._load_custom_templates()

    def _load_custom_templates(self):
        if os.path.exists(self._custom_templates_file):
            try:
                with open(self._custom_templates_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._custom_templates = data.get("templates", {})
                logger.info(f"自定义模板加载完成: {len(self._custom_templates)} 个")
            except Exception as e:
                logger.error(f"自定义模板加载失败: {e}")

    def _save_custom_templates(self):
        data = {"templates": self._custom_templates}
        with open(self._custom_templates_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list_templates(self, category: Optional[str] = None) -> List[Dict]:
        templates = []
        for t in BUILTIN_TEMPLATES:
            if category and t.get("category") != category:
                continue
            templates.append(t)
        for t in self._custom_templates.values():
            if category and t.get("category") != category:
                continue
            templates.append(t)
        return templates

    def get_template(self, template_id: str) -> Optional[Dict]:
        for t in BUILTIN_TEMPLATES:
            if t["template_id"] == template_id:
                return t.copy()
        custom = self._custom_templates.get(template_id)
        if custom:
            return custom.copy()
        return None

    def create_template(self, name: str, content: str, description: str = "",
                        category: str = "自定义") -> Dict[str, Any]:
        template_id = f"custom-{uuid.uuid4().hex[:8]}"
        template = {
            "template_id": template_id,
            "name": name,
            "description": description,
            "category": category,
            "content": content,
            "is_builtin": False,
            "created_at": datetime.now().isoformat(),
        }
        self._custom_templates[template_id] = template
        self._save_custom_templates()
        return template

    def update_template(self, template_id: str, name: Optional[str] = None,
                        content: Optional[str] = None, description: Optional[str] = None,
                        category: Optional[str] = None) -> Optional[Dict]:
        template = self._custom_templates.get(template_id)
        if not template:
            return None
        if name is not None:
            template["name"] = name
        if content is not None:
            template["content"] = content
        if description is not None:
            template["description"] = description
        if category is not None:
            template["category"] = category
        template["updated_at"] = datetime.now().isoformat()
        self._save_custom_templates()
        return template

    def delete_template(self, template_id: str) -> bool:
        if template_id not in self._custom_templates:
            return False
        del self._custom_templates[template_id]
        self._save_custom_templates()
        return True

    def create_page_from_template(self, template_id: str, title: str,
                                  space_id: str = "default",
                                  author: str = "system",
                                  variables: Optional[Dict] = None) -> Optional[Dict]:
        template = self.get_template(template_id)
        if not template:
            return None

        content = template["content"]
        now = datetime.now()
        default_vars = {
            "title": title,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "author": author,
        }
        if variables:
            default_vars.update(variables)

        for key, value in default_vars.items():
            content = content.replace(f"{{{key}}}", str(value))

        page = wiki_store.create_page(
            title=title,
            content=content,
            space_id=space_id,
            page_type="markdown",
            author=author,
            tags=[template.get("category", "")],
        )

        return {
            "success": True,
            "page_id": page.page_id,
            "title": page.title,
            "template_id": template_id,
            "template_name": template["name"],
        }

    def list_categories(self) -> List[str]:
        categories = set()
        for t in BUILTIN_TEMPLATES:
            categories.add(t.get("category", "其他"))
        for t in self._custom_templates.values():
            categories.add(t.get("category", "其他"))
        return sorted(categories)


wiki_template_service = WikiTemplateService()
