import os
import json
import re
import logging
import asyncio
from typing import List, Optional, Dict, Any, Set, Tuple

from app.core.config import settings
from app.kg.store import kg_store, KGNode, KGEdge

logger = logging.getLogger(__name__)

RELATION_STYLES = {
    "related_to": {"style": "dashed", "color": "#6b7280"},
    "part_of": {"style": "dashed", "color": "#06b6d4"},
    "located_in": {"style": "solid", "color": "#10b981"},
    "works_for": {"style": "solid", "color": "#6366f1"},
    "created_by": {"style": "dashed", "color": "#f59e0b"},
    "depends_on": {"style": "dashed", "color": "#ef4444"},
    "supports": {"style": "solid", "color": "#10b981"},
    "used_by": {"style": "dashed", "color": "#8b5cf6"},
    "belongs_to": {"style": "solid", "color": "#06b6d4"},
    "contains": {"style": "solid", "color": "#6366f1"},
    "influences": {"style": "dashed", "color": "#ec4899"},
    "derived_from": {"style": "dashed", "color": "#f59e0b"},
    "derives_from": {"style": "dashed", "color": "#f59e0b"},
    "implements": {"style": "solid", "color": "#10b981"},
    "precedes": {"style": "dashed", "color": "#8b5cf6"},
    "contradicts": {"style": "dashed", "color": "#ef4444"},
    "defined_by": {"style": "solid", "color": "#3b82f6"},
    "includes": {"style": "solid", "color": "#6366f1"},
    "applies_to": {"style": "solid", "color": "#10b981"},
    "punishes": {"style": "solid", "color": "#ef4444"},
    "requires": {"style": "solid", "color": "#f59e0b"},
    "causes": {"style": "solid", "color": "#ec4899"},
    "competes_with": {"style": "dashed", "color": "#ef4444"},
    "restricts": {"style": "dashed", "color": "#8b5cf6"},
    "constitutes": {"style": "solid", "color": "#10b981"},
    "bears": {"style": "solid", "color": "#6366f1"},
    "protects": {"style": "solid", "color": "#06b6d4"},
    "rules": {"style": "solid", "color": "#6366f1"},
    "conquered": {"style": "solid", "color": "#ef4444"},
    "allied_with": {"style": "dashed", "color": "#8b5cf6"},
    "created": {"style": "solid", "color": "#f59e0b"},
    "co_occurs_with": {"style": "dotted", "color": "#9ca3af"},
    "not_constitutes": {"style": "dashed", "color": "#ef4444"},
    "has_attribute": {"style": "solid", "color": "#8b5cf6"},
    "has_penalty": {"style": "solid", "color": "#ef4444"},
    "has_standard": {"style": "solid", "color": "#3b82f6"},
    "has_subject": {"style": "solid", "color": "#6366f1"},
    "has_element": {"style": "solid", "color": "#f59e0b"},
    "excludes": {"style": "dashed", "color": "#ef4444"},
    "classifies": {"style": "solid", "color": "#06b6d4"},
    "has_type": {"style": "solid", "color": "#8b5cf6"},
    "distinguishes": {"style": "dashed", "color": "#10b981"},
    "occurred_at": {"style": "solid", "color": "#10b981"},
    "started_at": {"style": "solid", "color": "#10b981"},
    "ended_at": {"style": "solid", "color": "#10b981"},
    "lasted": {"style": "dashed", "color": "#8b5cf6"},
    "adjacent_to": {"style": "dashed", "color": "#06b6d4"},
    "results_from": {"style": "dashed", "color": "#f59e0b"},
    "triggers": {"style": "solid", "color": "#ec4899"},
    "facilitates": {"style": "solid", "color": "#10b981"},
    "prevents": {"style": "dashed", "color": "#ef4444"},
    "condition_of": {"style": "dashed", "color": "#8b5cf6"},
    "purpose_of": {"style": "dashed", "color": "#f59e0b"},
    "means_of": {"style": "dashed", "color": "#6366f1"},
    "composed_of": {"style": "solid", "color": "#6366f1"},
    "member_of": {"style": "dashed", "color": "#06b6d4"},
    "connected_to": {"style": "dashed", "color": "#8b5cf6"},
    "attached_to": {"style": "dashed", "color": "#8b5cf6"},
    "similar_to": {"style": "dashed", "color": "#06b6d4"},
    "superior_to": {"style": "solid", "color": "#10b981"},
    "inferior_to": {"style": "dashed", "color": "#ef4444"},
    "opposes": {"style": "dashed", "color": "#ef4444"},
    "compatible_with": {"style": "dashed", "color": "#10b981"},
    "conflicts_with": {"style": "dashed", "color": "#ef4444"},
    "has_feature": {"style": "solid", "color": "#8b5cf6"},
    "has_capability": {"style": "solid", "color": "#8b5cf6"},
    "manifests_as": {"style": "dashed", "color": "#ec4899"},
    "type_of": {"style": "solid", "color": "#06b6d4"},
    "measured_as": {"style": "dashed", "color": "#3b82f6"},
    "identified_by": {"style": "dashed", "color": "#3b82f6"},
    "executes": {"style": "solid", "color": "#6366f1"},
    "uses": {"style": "solid", "color": "#f59e0b"},
    "produces": {"style": "solid", "color": "#10b981"},
    "changes": {"style": "dashed", "color": "#ec4899"},
    "inputs": {"style": "dashed", "color": "#8b5cf6"},
    "outputs": {"style": "dashed", "color": "#10b981"},
    "controls": {"style": "solid", "color": "#6366f1"},
    "relative_of": {"style": "solid", "color": "#f59e0b"},
    "friend_of": {"style": "dashed", "color": "#10b981"},
    "colleague_of": {"style": "dashed", "color": "#6366f1"},
    "classmate_of": {"style": "dashed", "color": "#8b5cf6"},
    "neighbor_of": {"style": "dashed", "color": "#06b6d4"},
    "subordinate_of": {"style": "solid", "color": "#6366f1"},
    "cooperates_with": {"style": "dashed", "color": "#10b981"},
    "trusts": {"style": "dashed", "color": "#8b5cf6"},
    "recommends": {"style": "dashed", "color": "#f59e0b"},
    "employed_by": {"style": "solid", "color": "#6366f1"},
    "managed_by": {"style": "solid", "color": "#6366f1"},
    "owned_by": {"style": "solid", "color": "#f59e0b"},
    "authorized_by": {"style": "dashed", "color": "#3b82f6"},
    "responsible_for": {"style": "solid", "color": "#6366f1"},
    "invests_in": {"style": "dashed", "color": "#f59e0b"},
    "acquired_by": {"style": "dashed", "color": "#ef4444"},
    "succeeded_by": {"style": "dashed", "color": "#8b5cf6"},
    "parallel_to": {"style": "dashed", "color": "#06b6d4"},
    "interrupted_by": {"style": "dashed", "color": "#ef4444"},
    "resumed_by": {"style": "dashed", "color": "#10b981"},
    "phase_of": {"style": "dashed", "color": "#06b6d4"},
    "refers_to": {"style": "dashed", "color": "#6b7280"},
    "defines": {"style": "solid", "color": "#3b82f6"},
    "explains": {"style": "dashed", "color": "#3b82f6"},
    "translates_to": {"style": "dashed", "color": "#8b5cf6"},
    "cites": {"style": "dashed", "color": "#f59e0b"},
    "source_of": {"style": "dashed", "color": "#f59e0b"},
    "confirms": {"style": "solid", "color": "#10b981"},
    "questions": {"style": "dashed", "color": "#ef4444"},
    "evaluates": {"style": "dashed", "color": "#ec4899"},
    "prefers": {"style": "dashed", "color": "#8b5cf6"},
    "dislikes": {"style": "dashed", "color": "#ef4444"},
    "importance_of": {"style": "dashed", "color": "#f59e0b"},
    "confidence_of": {"style": "dashed", "color": "#3b82f6"},
    "transforms_to": {"style": "dashed", "color": "#ec4899"},
    "represents": {"style": "dashed", "color": "#6b7280"},
    "corresponds_to": {"style": "dashed", "color": "#06b6d4"},
    "maps_to": {"style": "dashed", "color": "#06b6d4"},
    "prescribes": {"style": "solid", "color": "#6366f1"},
    "prohibits": {"style": "solid", "color": "#ef4444"},
    "permits": {"style": "solid", "color": "#10b981"},
    "constrains": {"style": "dashed", "color": "#8b5cf6"},
    "grows_into": {"style": "solid", "color": "#10b981"},
    "declines_to": {"style": "dashed", "color": "#ef4444"},
    "splits_into": {"style": "dashed", "color": "#ec4899"},
    "merges_into": {"style": "dashed", "color": "#6366f1"},
    "migrates_to": {"style": "dashed", "color": "#06b6d4"},
    "evolves_into": {"style": "dashed", "color": "#10b981"},
    "designed_by": {"style": "solid", "color": "#f59e0b"},
    "awarded": {"style": "solid", "color": "#f59e0b"},
    "awarded_by": {"style": "dashed", "color": "#f59e0b"},
    "completed_in": {"style": "solid", "color": "#3b82f6"},
    "covers": {"style": "solid", "color": "#6366f1"},
    "made_of": {"style": "solid", "color": "#8b5cf6"},
    "style_of": {"style": "dashed", "color": "#ec4899"},
    "structure_of": {"style": "solid", "color": "#6366f1"},
    "listed_as": {"style": "solid", "color": "#f59e0b"},
    "preceded_by": {"style": "dashed", "color": "#8b5cf6"},
}

FIXED_PHRASES = [
    "尚不", "不构成", "不足以", "不属于", "不同于", "不等同", "不限于",
    "非营利", "非官方", "非正式", "非传统", "非标准",
    "轻微伤", "轻伤", "重伤", "死亡",
    "非法占有", "非法占有目的", "虚构事实", "隐瞒真相",
    "直接故意", "间接故意", "刑事责任能力", "刑事责任",
    "民事责任", "行政责任", "连带责任", "连带保证", "一般保证",
    "侵犯财产罪", "侵犯公民人身权利", "民主权利罪",
    "治安管理处罚", "行政处罚", "刑事处罚",
    "司法保护利率", "贷款市场报价利率",
    "人格权编", "侵权责任编", "合同编",
    "名誉权", "肖像权", "隐私权", "姓名权", "荣誉权",
    "出借人", "借款人", "担保人", "被害人", "行为人", "侵权人",
    "犯罪主体", "犯罪客体", "主观要件", "客观要件",
    "停止侵害", "排除妨碍", "消除影响", "恢复名誉", "赔礼道歉", "赔偿损失",
    "精神损害赔偿",
    "解构主义", "后现代主义", "现代主义", "结构主义", "功能主义", "极简主义",
    "高技派", "粗野主义", "新古典主义", "装饰艺术", "哥特式", "巴洛克式",
    "洛可可式", "文艺复兴式", "包豪斯", "新陈代谢派",
    "钢筋混凝土", "预应力混凝土", "碳纤维", "钛合金", "不锈钢", "铝合金",
    "钢化玻璃", "夹层玻璃", "中空玻璃", "低辐射玻璃", "ETFE膜", "PTFE膜",
    "空间钢桁架", "巨型框架", "伸臂桁架", "核心筒", "剪力墙",
    "玻璃幕墙", "石材幕墙", "金属幕墙", "双层幕墙", "单元式幕墙",
    "普利兹克建筑奖", "鲁班奖", "梁思成建筑奖", "阿迦汗建筑奖",
    "国家优质工程奖", "中国建筑工程鲁班奖",
    "联合国教科文组织", "世界遗产", "非物质文化遗产",
    "人工智能", "机器学习", "深度学习", "自然语言处理", "计算机视觉",
    "区块链", "物联网", "云计算", "大数据", "量子计算",
    "可持续发展", "碳中和", "碳达峰", "绿色建筑", "生态建筑",
    "智慧城市", "数字孪生", "建筑信息模型",
    "工业革命", "信息技术革命", "数字化转型",
    "相对论", "量子力学", "进化论", "热力学", "电磁学",
    "基因工程", "干细胞", "蛋白质组学", "免疫疗法",
    "丝绸之路", "文艺复兴", "启蒙运动", "五四运动",
    "改革开放", "全球化", "城市化进程",
    "股份有限公司", "有限责任公司", "合伙企业", "个人独资企业",
    "上市公司", "跨国公司", "国有企业", "民营企业", "合资企业",
    "研究所", "研究院", "设计院", "实验室", "研发中心",
    "国家重点实验室", "工程研究中心", "技术创新中心",
    "世界卫生组织", "世界贸易组织", "国际货币基金组织",
    "国际奥委会", "国际足联", "国际篮联",
    "亚太经合组织", "二十国集团", "七国集团", "金砖国家",
    "上海合作组织", "东南亚国家联盟", "非洲联盟",
]

NEGATION_PHRASES = [
    "尚不构成", "不构成", "不足以构成", "不属于", "不承担",
    "不受", "不适用", "无效", "不得", "不能", "不会", "并非",
    "不同于", "不等同于", "不等于", "不是", "不算",
    "不包含", "不包括", "不涉及",
    "不认可", "不承认", "不支持", "不赞成",
    "未构成", "未达到", "未满足", "未通过", "未获",
    "无法构成", "无法达到", "无法满足",
    "非属于", "非构成", "非等同于",
    "禁止", "严禁", "不准", "不许",
]

ENTITY_BLACKLIST = {
    "尚", "不", "的", "了", "是", "在", "有", "和", "与", "或",
    "及", "等", "为", "被", "将", "把", "从", "向", "对", "按",
    "以", "于", "到", "由", "其", "此", "该", "中", "上", "下",
    "可", "能", "会", "需", "须", "应", "当", "也", "又", "且",
    "但", "而", "则", "却", "虽", "若", "如", "因", "故", "即",
    "还", "更", "最", "很", "已", "曾", "正", "着",
    "不构成", "不属于", "不足以", "尚不", "针对", "其中",
    "属于", "构成", "承担", "保护", "适用", "处罚", "要求",
    "关联", "关系", "规则", "体系",
}

GENERALIZATION_MAP = {
    "公安机关": "公安机关",
    "人民法院": "人民法院",
    "人民检察院": "人民检察院",
    "最高人民法院": "最高人民法院",
    "最高人民检察院": "最高人民检察院",
    "国务院": "国务院",
    "全国人民代表大会": "全国人民代表大会",
    "全国政协": "全国政协",
    "中央军委": "中央军委",
    "国家发改委": "国家发改委",
    "住房和城乡建设部": "住房和城乡建设部",
    "自然资源部": "自然资源部",
    "生态环境部": "生态环境部",
    "科学技术部": "科学技术部",
    "教育部": "教育部",
    "工业和信息化部": "工业和信息化部",
    "联合国": "联合国",
    "联合国教科文组织": "联合国教科文组织",
    "世界卫生组织": "世界卫生组织",
    "世界银行": "世界银行",
    "国际货币基金组织": "国际货币基金组织",
    "世界贸易组织": "世界贸易组织",
    "国际奥委会": "国际奥委会",
    "中国科学院": "中国科学院",
    "中国工程院": "中国工程院",
    "中国社会科学院": "中国社会科学院",
    "国家自然科学基金委员会": "国家自然科学基金委员会",
    "清华大学": "清华大学",
    "北京大学": "北京大学",
    "同济大学": "同济大学",
    "东南大学": "东南大学",
    "中国建筑集团": "中国建筑集团",
    "中国中铁": "中国中铁",
    "中国铁建": "中国铁建",
    "中国交建": "中国交建",
    "中国电建": "中国电建",
    "华为": "华为",
    "阿里巴巴": "阿里巴巴",
    "腾讯": "腾讯",
    "百度": "百度",
    "字节跳动": "字节跳动",
    "中国建筑学会": "中国建筑学会",
    "中国土木工程学会": "中国土木工程学会",
    "中国城市规划学会": "中国城市规划学会",
    "国际建筑师协会": "国际建筑师协会",
    "美国建筑师协会": "美国建筑师协会",
    "英国皇家建筑师学会": "英国皇家建筑师学会",
}

ENTITY_SUFFIXES = {
    "罪": "crime",
    "案": "event",
    "法": "law",
    "编": "term",
    "章": "term",
    "条": "term",
    "款": "term",
    "项": "term",
    "权": "concept",
    "责任": "concept",
    "义务": "concept",
    "标准": "term",
    "要件": "concept",
    "行为": "concept",
    "制度": "concept",
    "程序": "concept",
    "主体": "concept",
    "客体": "concept",
    "情形": "concept",
    "情节": "concept",
    "处罚": "concept",
    "赔偿": "concept",
    "主义": "concept",
    "风格": "concept",
    "流派": "concept",
    "理论": "concept",
    "原理": "concept",
    "定律": "concept",
    "效应": "concept",
    "模型": "concept",
    "算法": "concept",
    "技术": "concept",
    "工艺": "concept",
    "方法": "concept",
    "体系": "concept",
    "系统": "concept",
    "结构": "concept",
    "材料": "material",
    "合金": "material",
    "混凝土": "material",
    "玻璃": "material",
    "钢材": "material",
    "奖项": "award",
    "奖": "award",
    "勋章": "award",
    "荣誉": "award",
    "称号": "award",
    "战争": "event",
    "运动": "event",
    "革命": "event",
    "起义": "event",
    "改革": "event",
    "会议": "event",
    "条约": "document",
    "公约": "document",
    "协议": "document",
    "宣言": "document",
    "宪章": "document",
    "规范": "document",
    "规程": "document",
    "准则": "document",
    "公司": "organization",
    "集团": "organization",
    "事务所": "organization",
    "研究所": "organization",
    "研究院": "organization",
    "大学": "organization",
    "学院": "organization",
    "协会": "organization",
    "学会": "organization",
    "基金会": "organization",
    "博物馆": "organization",
    "美术馆": "organization",
    "医院": "organization",
    "银行": "organization",
    "建筑": "work",
    "大厦": "work",
    "大楼": "work",
    "中心": "work",
    "广场": "work",
    "公园": "work",
    "桥": "work",
    "塔": "work",
    "馆": "work",
    "宫": "work",
    "殿": "work",
    "庙": "work",
    "寺": "work",
    "陵": "work",
    "园": "work",
    "楼": "work",
    "堂": "work",
    "院落": "work",
}

LEGAL_ENTITY_SUFFIXES = ENTITY_SUFFIXES


class TripleValidator:
    def __init__(self):
        self._fixed_phrase_set = set(FIXED_PHRASES)
        self._negation_set = set(NEGATION_PHRASES)
        self._blacklist = ENTITY_BLACKLIST
        self._generalization_map = GENERALIZATION_MAP

    def is_valid_entity(self, name: str) -> bool:
        if not name or len(name.strip()) < 2:
            return False
        name = name.strip()
        if name in self._blacklist:
            return False
        if len(name) == 1:
            return False
        for phrase in self._fixed_phrase_set:
            if phrase in name:
                return True
        if re.match(r'^[\u4e00-\u9fff]{1}$', name):
            return False
        return True

    def check_entity_integrity(self, name: str, text: str) -> Tuple[bool, str]:
        if not name or not text:
            return False, name
        name = name.strip()
        for phrase in sorted(self._fixed_phrase_set, key=len, reverse=True):
            if phrase in text and len(name) < len(phrase):
                if name in phrase and name != phrase:
                    return False, phrase
        return True, name

    def check_negation_integrity(self, name: str, text: str) -> Tuple[bool, str]:
        for neg_phrase in self._negation_set:
            if name in neg_phrase and name != neg_phrase:
                idx = text.find(neg_phrase)
                if idx != -1:
                    return False, neg_phrase
        return True, name

    def validate_relation_direction(self, head: str, relation: str, tail: str, text: str) -> Tuple[bool, str, str, str]:
        if relation == "constitutes":
            if "不构成" in text and tail in text:
                context = text[max(0, text.find(tail) - 20):text.find(tail) + len(tail) + 10]
                if "不构成" in context or "尚不构成" in context:
                    return False, head, "not_constitutes", tail
        if relation == "belongs_to":
            if "不属于" in text and tail in text:
                context = text[max(0, text.find(tail) - 20):text.find(tail) + len(tail) + 10]
                if "不属于" in context:
                    return False, head, "excludes", tail
        return True, head, relation, tail

    def prevent_generalization(self, name: str, text: str) -> str:
        for specific, full_name in self._generalization_map.items():
            if specific in text and name in specific and name != specific:
                return full_name
        return name

    def split_multi_entity_relation(self, head: str, relation: str, tail: str) -> List[Tuple[str, str, str]]:
        results = []
        separators = ["、", "，", ",", "和", "与", "及", "或", "以及"]
        tail_parts = [tail]
        for sep in separators:
            new_parts = []
            for part in tail_parts:
                new_parts.extend(part.split(sep))
            tail_parts = new_parts

        tail_parts = [p.strip() for p in tail_parts if p.strip()]
        if len(tail_parts) <= 1:
            return [(head, relation, tail)]

        for part in tail_parts:
            if self.is_valid_entity(part):
                results.append((head, relation, part))
        return results if results else [(head, relation, tail)]

    def repair_truncated_entity(self, name: str, text: str) -> str:
        if not name or not text:
            return name
        name = name.strip()

        if '《' in name:
            book_start = name.find('《')
            prefix = name[:book_start]
            if '》' in name:
                if name.startswith('《') and name.endswith('》'):
                    return name
                if not name.startswith('《'):
                    inner = name[book_start:]
                    for m in re.finditer(r'《([^》]{2,40}?)》', text):
                        if inner in m.group(0) or m.group(0) in inner:
                            name = m.group(1) if not prefix else prefix + m.group(1)
                            return name
            else:
                partial = name[book_start + 1:]
                full_match = re.search(r'《(' + re.escape(partial) + r'[^》]{0,40}?)》', text)
                if full_match:
                    name = full_match.group(1)
                    article_match = re.search(re.escape('《' + full_match.group(1) + '》') + r'\s*(第[一二三四五六七八九十百千零\d]+[条编章节])', text)
                    if article_match:
                        name = name + article_match.group(1)
                    return name

        if '》' in name and '《' not in name:
            for m in re.finditer(r'《([^》]{2,40}?)》', text):
                full_law = m.group(0)
                full_inner = m.group(1)
                if name in full_law or name in full_inner:
                    name = full_inner
                    article_match = re.search(re.escape(full_law) + r'\s*(第[一二三四五六七八九十百千零\d]+[条编章节])', text)
                    if article_match:
                        name = name + article_match.group(1)
                    return name

        truncated_doc_patterns = [
            re.compile(r'《([^》]{2,40}?)》'),
        ]
        for doc_pattern in truncated_doc_patterns:
            candidates = []
            for m in doc_pattern.finditer(text):
                full_inner = m.group(1)
                if name in full_inner or full_inner in name:
                    candidates.append(full_inner)
            if len(candidates) == 1:
                name = candidates[0]
                article_match = re.search(r'《' + re.escape(candidates[0]) + r'》\s*(第[一二三四五六七八九十百千零\d]+[条编章节])', text)
                if article_match:
                    name = candidates[0] + article_match.group(1)
                return name
            elif len(candidates) > 1:
                for c in candidates:
                    if name in c:
                        name = c
                        return name

        for phrase in sorted(self._fixed_phrase_set, key=len, reverse=True):
            if name in phrase and name != phrase and len(name) < len(phrase):
                idx = text.find(phrase)
                if idx != -1:
                    name = phrase
                    return name

        if name.endswith('不'):
            clean = name[:-1]
            if clean and len(clean) >= 2:
                name = clean
                return name

        if name.endswith('非'):
            clean = name[:-1]
            if clean and len(clean) >= 2:
                name = clean
                return name

        if re.match(r'^[\u4e00-\u9fff]{1,3}不$', name):
            clean = name[:-1]
            if clean and len(clean) >= 2:
                name = clean
                return name

        return name

    def repair_negation_relation(self, head: str, relation: str, tail: str, text: str) -> Tuple[str, str, str]:
        if head.endswith('不') and relation in ('constitutes', 'belongs_to', 'applies_to'):
            clean_head = head.rstrip('不')
            if clean_head and len(clean_head) >= 2:
                if relation == 'constitutes':
                    return clean_head, 'not_constitutes', tail
                elif relation == 'belongs_to':
                    return clean_head, 'excludes', tail
                else:
                    return clean_head, 'not_' + relation, tail

        if head.endswith('可') and relation == 'constitutes':
            clean_head = head.rstrip('可')
            if clean_head and len(clean_head) >= 2:
                return clean_head, 'constitutes', tail

        if head.endswith('仅') and relation == 'applies_to':
            clean_head = head.rstrip('仅')
            if clean_head and len(clean_head) >= 2:
                return clean_head, 'applies_to', tail

        negation_patterns = [
            (r'^([\u4e00-\u9fff]{1,6})不$', 'constitutes', 'not_constitutes'),
            (r'^([\u4e00-\u9fff]{1,6})不$', 'belongs_to', 'excludes'),
        ]
        for pattern, old_rel, new_rel in negation_patterns:
            if re.match(pattern, head) and relation == old_rel:
                clean_head = re.sub(r'不$', '', head)
                if clean_head and len(clean_head) >= 2:
                    return clean_head, new_rel, tail

        return head, relation, tail


class ImplicitTripleExtractor:
    def __init__(self):
        self._age_pattern = re.compile(r'年满(\d+)周岁')
        self._enumeration_pattern = re.compile(r'([\u4e00-\u9fff]{2,10}?)[：:]\s*([^。！？\n]+)')
        suffix_keys = sorted(ENTITY_SUFFIXES.keys(), key=len, reverse=True)
        self._suffix_group = '|'.join(re.escape(k) for k in suffix_keys)
        self._parallel_pattern = re.compile(rf'([\u4e00-\u9fff]{{2,8}}(?:{self._suffix_group}))\s*[、，,]\s*')
        self._bracket_parallel = re.compile(r'（([^）]+)）')
        self._law_ref_pattern = re.compile(r'《([^》]{2,40})》')
        self._article_pattern = re.compile(r'第[一二三四五六七八九十百千零\d]+条')

    def extract_age_triples(self, text: str, context_entity: str = "") -> List[Dict]:
        triples = []
        for match in self._age_pattern.finditer(text):
            age = match.group(1)
            age_entity = f"{age}周岁"
            context_start = max(0, match.start() - 30)
            context = text[context_start:match.end() + 20]
            subject = context_entity
            if not subject:
                for kw in ["犯罪主体", "主体", "自然人", "建筑师", "设计师", "工程师", "申请人", "候选人", "参与者"]:
                    idx = context.find(kw)
                    if idx != -1:
                        subject = kw
                        break
            if subject:
                triples.append({
                    "head": subject,
                    "relation": "has_attribute",
                    "tail": age_entity,
                    "entity_type": "concept",
                    "tail_type": "term",
                })
        return triples

    def extract_enumeration_triples(self, text: str) -> List[Dict]:
        triples = []
        for match in self._enumeration_pattern.finditer(text):
            category = match.group(1).strip()
            items_str = match.group(2).strip()
            items = re.split(r'[、，,；;]', items_str)
            items = [item.strip() for item in items if item.strip() and len(item.strip()) >= 2]
            for item in items:
                if len(item) > 15:
                    continue
                triples.append({
                    "head": category,
                    "relation": "includes",
                    "tail": item,
                    "entity_type": "concept",
                    "tail_type": "concept",
                })
        return triples

    def extract_parallel_entities(self, text: str) -> List[Dict]:
        entities = []
        parallel_matches = list(self._parallel_pattern.finditer(text))
        if parallel_matches:
            all_items = []
            for m in parallel_matches:
                item = m.group(1).strip()
                if item:
                    all_items.append(item)
            last_match = parallel_matches[-1]
            remaining = text[last_match.end():last_match.end() + 15]
            last_item_match = re.match(self._parallel_pattern.pattern.replace(r'\s*[、，,]\s*', ''), remaining)
            if last_item_match:
                all_items.append(last_item_match.group(1))

            for item in all_items:
                entity_type = "concept"
                for suffix in sorted(ENTITY_SUFFIXES.keys(), key=len, reverse=True):
                    if item.endswith(suffix):
                        entity_type = ENTITY_SUFFIXES[suffix]
                        break
                entities.append({
                    "name": item,
                    "entity_type": entity_type,
                })

            suffix_type_map = {}
            for item in all_items:
                for suffix in sorted(ENTITY_SUFFIXES.keys(), key=len, reverse=True):
                    if item.endswith(suffix):
                        suffix_type_map[item] = ENTITY_SUFFIXES[suffix]
                        break
                else:
                    suffix_type_map[item] = "concept"

            for i in range(len(all_items) - 1):
                for j in range(i + 1, len(all_items)):
                    if suffix_type_map.get(all_items[i]) == suffix_type_map.get(all_items[j]):
                        entities.append({
                            "parallel_pair": (all_items[i], all_items[j]),
                        })
                        break
        return entities

    def extract_bracket_content(self, text: str) -> List[Dict]:
        triples = []
        for match in self._bracket_parallel.finditer(text):
            content = match.group(1).strip()
            context_start = max(0, match.start() - 20)
            context = text[context_start:match.start()].strip()
            head = ""
            for kw in ["处罚种类", "承担责任方式", "侵权客体", "核心约束", "伤情", "数额",
                        "建筑风格", "结构类型", "材料种类", "奖项类别", "技术指标",
                        "功能分区", "设计理念", "影响因素", "组成部分", "关键参数"]:
                if kw in context or kw in text[max(0, match.start() - 50):match.start()]:
                    head = kw
                    break
            if not head:
                m = re.search(r'([\u4e00-\u9fff]{2,10})[：:]\s*$', context)
                if m:
                    head = m.group(1)
            if head:
                items = re.split(r'[、，,；;]', content)
                for item in items:
                    item = item.strip()
                    if item and 2 <= len(item) <= 15:
                        triples.append({
                            "head": head,
                            "relation": "includes",
                            "tail": item,
                            "entity_type": "concept",
                            "tail_type": "concept",
                        })
        return triples

    def extract_law_reference_triples(self, text: str) -> List[Dict]:
        triples = []
        law_matches = list(self._law_ref_pattern.finditer(text))
        article_matches = list(self._article_pattern.finditer(text))

        for law_match in law_matches:
            law_name = law_match.group(1)
            context_start = max(0, law_match.start() - 40)
            context_end = min(len(text), law_match.end() + 80)
            context = text[context_start:context_end]

            for article_match in article_matches:
                if law_match.start() <= article_match.start() <= law_match.end() + 30:
                    triples.append({
                        "head": law_name,
                        "relation": "includes",
                        "tail": article_match.group(0),
                        "entity_type": "law",
                        "tail_type": "term",
                    })

            for kw in ["依据", "根据", "依照", "适用", "按照"]:
                if kw in context:
                    before_kw = context[:context.find(kw)]
                    subject_match = re.search(rf'([\u4e00-\u9fff]{{2,10}}(?:{self._suffix_group}))', before_kw)
                    if subject_match:
                        entity_type = ENTITY_SUFFIXES.get(subject_match.group(1)[-1], "concept")
                        if any(subject_match.group(1).endswith(s) for s in ["罪", "案"]):
                            entity_type = "crime"
                        elif any(subject_match.group(1).endswith(s) for s in ["法", "条约", "公约"]):
                            entity_type = "law"
                        triples.append({
                            "head": subject_match.group(1),
                            "relation": "defined_by",
                            "tail": law_name,
                            "entity_type": entity_type,
                            "tail_type": "law",
                        })
                    break
        return triples


class KGBuilder:
    def __init__(self):
        self._glm = None
        self._validator = TripleValidator()
        self._implicit_extractor = ImplicitTripleExtractor()
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，实体提取将使用正则回退模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("KG GLM 服务初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("KG GLM 服务初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"GLM 初始化失败: {e}")
            self._glm = None

    async def extract_from_text(self, text: str, title: str = "",
                                struct_tags: str = "") -> Dict[str, Any]:
        if not text or not text.strip():
            return {"entities": [], "relations": []}

        if self._glm:
            result = await self._extract_with_llm(text, title, struct_tags)
            result = self._post_validate(result, text)
            regex_result = self._extract_with_regex(text, title)
            result = self._merge_results(result, regex_result)
            result = self._post_validate(result, text)
            result = self._extract_implicit_triples(result, text)
            return result
        else:
            result = self._extract_with_regex(text, title)
            result = self._post_validate(result, text)
            result = self._extract_implicit_triples(result, text)
            return result

    def _post_validate(self, result: Dict, text: str) -> Dict:
        logger.debug(f"_post_validate 输入: entities={len(result.get('entities', []))}, relations={len(result.get('relations', []))}")
        validated_entities = []
        seen_names = set()
        for ent in result.get("entities", []):
            name = ent.get("name", "").strip()
            if not name:
                continue
            is_valid, corrected = self._validator.check_entity_integrity(name, text)
            if not is_valid:
                name = corrected
                ent["name"] = corrected
            is_valid, corrected = self._validator.check_negation_integrity(name, text)
            if not is_valid:
                name = corrected
                ent["name"] = corrected
            original_name = name
            name = self._validator.repair_truncated_entity(name, text)
            if original_name != name:
                logger.debug(f"实体修复: '{original_name}' -> '{name}'")
            ent["name"] = name
            if not self._validator.is_valid_entity(name):
                continue
            if name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            validated_entities.append(ent)

        validated_relations = []
        seen_rel_keys = set()
        for rel in result.get("relations", []):
            head = rel.get("head", "").strip()
            tail = rel.get("tail", "").strip()
            relation = rel.get("relation", "related_to")

            if not head or not tail:
                continue

            head = self._validator.repair_truncated_entity(head, text)
            tail = self._validator.repair_truncated_entity(tail, text)
            orig_head_for_neg = rel.get("head", "").strip()
            orig_tail_for_neg = rel.get("tail", "").strip()
            if orig_head_for_neg != head:
                if orig_head_for_neg.endswith('不') and relation in ('constitutes', 'belongs_to', 'applies_to', 'related_to'):
                    if relation == 'constitutes':
                        relation = 'not_constitutes'
                    elif relation == 'belongs_to':
                        relation = 'excludes'
                    else:
                        relation = 'not_' + relation
                if orig_head_for_neg.endswith('可') and relation == 'constitutes':
                    pass
            head, relation, tail = self._validator.repair_negation_relation(head, relation, tail, text)

            if not self._validator.is_valid_entity(head) or not self._validator.is_valid_entity(tail):
                continue

            is_valid, head, relation, tail = self._validator.validate_relation_direction(
                head, relation, tail, text
            )

            expanded = self._validator.split_multi_entity_relation(head, relation, tail)
            for h, r, t in expanded:
                rel_key = f"{h}|{r}|{t}"
                if rel_key not in seen_rel_keys:
                    seen_rel_keys.add(rel_key)
                    validated_relations.append({
                        "head": h,
                        "relation": r,
                        "tail": t,
                        "weight": rel.get("weight", 1.0),
                        "style": rel.get("style", "solid"),
                    })
                if h.lower() not in seen_names:
                    seen_names.add(h.lower())
                    validated_entities.append({"name": h, "entity_type": "concept", "attributes": {}})
                if t.lower() not in seen_names:
                    seen_names.add(t.lower())
                    validated_entities.append({"name": t, "entity_type": "concept", "attributes": {}})

        return {"entities": validated_entities, "relations": validated_relations}

    def _merge_results(self, llm_result: Dict, regex_result: Dict) -> Dict:
        merged_entities = {}
        for ent in llm_result.get("entities", []):
            key = ent.get("name", "").lower()
            if key and key not in merged_entities:
                merged_entities[key] = ent
        for ent in regex_result.get("entities", []):
            key = ent.get("name", "").lower()
            if key and key not in merged_entities:
                merged_entities[key] = ent

        seen_rel_keys = set()
        merged_relations = []
        for rel in llm_result.get("relations", []):
            key = f"{rel.get('head', '')}|{rel.get('relation', '')}|{rel.get('tail', '')}"
            if key not in seen_rel_keys:
                seen_rel_keys.add(key)
                merged_relations.append(rel)
        for rel in regex_result.get("relations", []):
            key = f"{rel.get('head', '')}|{rel.get('relation', '')}|{rel.get('tail', '')}"
            if key not in seen_rel_keys:
                seen_rel_keys.add(key)
                merged_relations.append(rel)

        return {
            "entities": list(merged_entities.values()),
            "relations": merged_relations,
        }

    def _extract_implicit_triples(self, result: Dict, text: str) -> Dict:
        entities = result.get("entities", [])
        relations = result.get("relations", [])
        seen = {e.get("name", "").lower() for e in entities}
        seen_rel_keys = {f"{r.get('head', '')}|{r.get('relation', '')}|{r.get('tail', '')}" for r in relations}

        entity_map = {e.get("name", ""): e.get("entity_type", "concept") for e in entities}

        crime_entities = [e for e in entities if e.get("entity_type") == "crime"]
        for crime_ent in crime_entities:
            crime_name = crime_ent.get("name", "")
            age_triples = self._implicit_extractor.extract_age_triples(text, crime_name)
            for t in age_triples:
                tail_name = t["tail"]
                if tail_name.lower() not in seen:
                    entities.append({"name": tail_name, "entity_type": t.get("tail_type", "term"), "attributes": {}})
                    seen.add(tail_name.lower())
                head_name = t["head"]
                if head_name.lower() not in seen:
                    entities.append({"name": head_name, "entity_type": t.get("entity_type", "concept"), "attributes": {}})
                    seen.add(head_name.lower())
                rel_key = f"{head_name}|{t['relation']}|{tail_name}"
                if rel_key not in seen_rel_keys:
                    seen_rel_keys.add(rel_key)
                    relations.append({
                        "head": head_name,
                        "relation": t["relation"],
                        "tail": tail_name,
                        "weight": 0.8,
                        "style": "solid",
                    })

        enum_triples = self._implicit_extractor.extract_enumeration_triples(text)
        for t in enum_triples:
            head_name = t["head"]
            tail_name = t["tail"]
            if tail_name.lower() not in seen:
                entities.append({"name": tail_name, "entity_type": t.get("tail_type", "concept"), "attributes": {}})
                seen.add(tail_name.lower())
            if head_name.lower() not in seen:
                entities.append({"name": head_name, "entity_type": t.get("entity_type", "concept"), "attributes": {}})
                seen.add(head_name.lower())
            rel_key = f"{head_name}|{t['relation']}|{tail_name}"
            if rel_key not in seen_rel_keys:
                seen_rel_keys.add(rel_key)
                relations.append({
                    "head": head_name,
                    "relation": t["relation"],
                    "tail": tail_name,
                    "weight": 0.7,
                    "style": "solid",
                })

        bracket_triples = self._implicit_extractor.extract_bracket_content(text)
        for t in bracket_triples:
            head_name = t["head"]
            tail_name = t["tail"]
            if tail_name.lower() not in seen:
                entities.append({"name": tail_name, "entity_type": t.get("tail_type", "concept"), "attributes": {}})
                seen.add(tail_name.lower())
            if head_name.lower() not in seen:
                entities.append({"name": head_name, "entity_type": t.get("entity_type", "concept"), "attributes": {}})
                seen.add(head_name.lower())
            rel_key = f"{head_name}|{t['relation']}|{tail_name}"
            if rel_key not in seen_rel_keys:
                seen_rel_keys.add(rel_key)
                relations.append({
                    "head": head_name,
                    "relation": t["relation"],
                    "tail": tail_name,
                    "weight": 0.7,
                    "style": "solid",
                })

        law_triples = self._implicit_extractor.extract_law_reference_triples(text)
        for t in law_triples:
            head_name = t["head"]
            tail_name = t["tail"]
            if tail_name.lower() not in seen:
                entities.append({"name": tail_name, "entity_type": t.get("tail_type", "term"), "attributes": {}})
                seen.add(tail_name.lower())
            if head_name.lower() not in seen:
                entities.append({"name": head_name, "entity_type": t.get("entity_type", "concept"), "attributes": {}})
                seen.add(head_name.lower())
            rel_key = f"{head_name}|{t['relation']}|{tail_name}"
            if rel_key not in seen_rel_keys:
                seen_rel_keys.add(rel_key)
                relations.append({
                    "head": head_name,
                    "relation": t["relation"],
                    "tail": tail_name,
                    "weight": 0.9,
                    "style": "solid",
                })

        parallel_entities = self._implicit_extractor.extract_parallel_entities(text)
        for item in parallel_entities:
            if "name" in item:
                name = item["name"]
                if name.lower() not in seen:
                    entities.append({"name": name, "entity_type": item.get("entity_type", "concept"), "attributes": {}})
                    seen.add(name.lower())
            elif "parallel_pair" in item:
                e1, e2 = item["parallel_pair"]
                rel_key = f"{e1}|co_occurs_with|{e2}"
                if rel_key not in seen_rel_keys:
                    seen_rel_keys.add(rel_key)
                    relations.append({
                        "head": e1,
                        "relation": "co_occurs_with",
                        "tail": e2,
                        "weight": 0.3,
                        "style": "dashed",
                    })

        return {"entities": entities, "relations": relations}

    async def _extract_with_llm(self, text: str, title: str,
                                struct_tags: str = "") -> Dict[str, Any]:
        tag_instruction = ""
        if struct_tags:
            tag_instruction = f"\n\n文本的结构标签为：{struct_tags}，请结合结构标签进行更精准的抽取。"

        system_prompt = f"""你是知识图谱三元组抽取专家。从文本中精准抽取实体和关系三元组。

【核心规则】
1. 实体完整性：实体必须是文本中的完整表述，严禁截断
2. 逻辑方向正确：关系的头尾方向必须符合语义逻辑
3. 单头单尾规则：每个三元组只能有一个头实体和一个尾实体
4. 实体准确性：禁止泛化，使用文本中的原词
5. 并列实体拆分：并列的实体必须逐条抽取
6. 尽可能多抽取：不要遗漏文本中明确的关系
7. 【输出精简】不要输出attributes字段，不要输出多余空格和换行

实体类型：person/organization/location/time/event/work/concept/material/award/document/term/product/crime/law

关键关系类型：
- 时空: located_in/occurred_at/started_at/ended_at/belongs_to/lasted
- 因果: causes/triggers/facilitates/prevents/results_from
- 组成: includes/composed_of/member_of/part_of/contains
- 比较: similar_to/superior_to/opposes/compatible_with/conflicts_with
- 属性: has_attribute/has_feature/has_capability/type_of/has_type
- 行为: uses/produces/executes/created/designed_by/managed_by
- 社会: cooperates_with/invests_in/works_for/employed_by
- 信息: defines/explains/refers_to/cites
- 规则: prescribes/requires/constrains/prohibits
- 转化: transforms_to/derives_from/evolves_into

请严格按JSON格式返回（不要输出attributes，保持紧凑）：
{{"entities":[{{"name":"实体","entity_type":"类型"}}],"relations":[{{"head":"头","relation":"关系","tail":"尾"}}]}}

注意：
1. 不要输出```json```标记，直接输出JSON
2. 不要输出attributes字段
3. 关系类型优先从上面选择，没有合适的可自创
4. 头实体和尾实体必须在entities列表中
5. 否定关系必须使用not_constitutes/excludes等否定关系类型{tag_instruction}"""

        try:
            chunk_size = 1500
            max_text = 6000
            all_entities = []
            all_relations = []

            text_to_process = text[:max_text]
            chunks = []
            for start in range(0, len(text_to_process), chunk_size):
                chunk = text_to_process[start:start + chunk_size]
                if chunk.strip():
                    chunks.append(chunk)

            async def _process_chunk(idx, chunk):
                user_message = f"文档标题：{title}\n\n文档内容：\n{chunk}"
                try:
                    def _call(msg=user_message):
                        return self._glm.chat.completions.create(
                            model=settings.OPENAI_MODEL,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": msg},
                            ],
                            max_tokens=4000,
                            temperature=0.1,
                        )

                    raw_response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=120)
                    raw = raw_response.choices[0].message.content or ""

                    logger.info(f"KG LLM 原始响应 (分块 {idx}): {raw[:500]}")
                    parsed = self._parse_llm_response(raw)
                    logger.info(f"KG LLM 解析结果 (分块 {idx}): entities={len(parsed.get('entities', []))}, relations={len(parsed.get('relations', []))}")
                    return parsed
                except asyncio.TimeoutError:
                    logger.warning(f"KG 抽取超时，跳过分块 {idx}")
                    return {"entities": [], "relations": []}
                except Exception as e:
                    logger.warning(f"KG 抽取分块 {idx} 失败: {e}")
                    return {"entities": [], "relations": []}

            chunk_results = await asyncio.gather(*[_process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)])

            for parsed in chunk_results:
                all_entities.extend(parsed.get("entities", []))
                all_relations.extend(parsed.get("relations", []))

            if not all_entities and not all_relations:
                logger.warning("LLM 抽取未返回任何结果，使用正则回退")
                return self._extract_with_regex(text, title)

            seen_entities = {}
            for ent in all_entities:
                key = ent.get("name", "").lower()
                if key and key not in seen_entities:
                    seen_entities[key] = ent

            seen_relations = set()
            unique_relations = []
            for rel in all_relations:
                head = rel.get("head", "")
                tail = rel.get("tail", "")
                relation = rel.get("relation", "related_to")
                key = f"{head}|{relation}|{tail}"
                if key not in seen_relations and head and tail:
                    seen_relations.add(key)
                    unique_relations.append(rel)

            return {
                "entities": list(seen_entities.values()),
                "relations": unique_relations,
            }
        except Exception as e:
            logger.error(f"LLM 实体提取失败: {e}")
            return self._extract_with_regex(text, title)

    def _parse_llm_response(self, raw: str) -> Dict:
        try:
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    return self._repair_truncated_json(json_match.group(1))

            brace_start = raw.find('{')
            if brace_start != -1:
                depth = 0
                in_string = False
                escape_next = False
                for i in range(brace_start, len(raw)):
                    c = raw[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if c == '\\':
                        escape_next = True
                        continue
                    if c == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(raw[brace_start:i + 1])
                            except json.JSONDecodeError:
                                return self._repair_truncated_json(raw[brace_start:i + 1])
                if depth > 0:
                    logger.warning(f"LLM 返回的 JSON 被截断 (depth={depth})，尝试修复")
                    return self._repair_truncated_json(raw[brace_start:])
        except Exception as e:
            logger.error(f"解析LLM响应失败: {e}")
        return {"entities": [], "relations": []}

    def _repair_truncated_json(self, partial: str) -> Dict:
        result = {"entities": [], "relations": []}
        try:
            repaired = self._try_brace_completion(partial)
            if repaired:
                try:
                    parsed = json.loads(repaired)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

        try:
            entities_match = re.search(r'"entities"\s*:\s*\[', partial)
            if entities_match:
                arr_start = entities_match.end()
                entities_str = self._extract_array_content(partial, arr_start)
                entity_pattern = re.compile(
                    r'\{\s*"name"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"entity_type"\s*:\s*"((?:[^"\\]|\\.)*)"'
                )
                for m in entity_pattern.finditer(entities_str):
                    name = m.group(1).encode().decode('unicode_escape', errors='ignore')
                    etype = m.group(2).encode().decode('unicode_escape', errors='ignore')
                    result["entities"].append({"name": name, "entity_type": etype, "attributes": {}})

            relations_match = re.search(r'"relations"\s*:\s*\[', partial)
            if relations_match:
                arr_start = relations_match.end()
                relations_str = self._extract_array_content(partial, arr_start)
                rel_pattern = re.compile(
                    r'\{\s*"head"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"relation"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"tail"\s*:\s*"((?:[^"\\]|\\.)*)"'
                )
                for m in rel_pattern.finditer(relations_str):
                    head = m.group(1).encode().decode('unicode_escape', errors='ignore')
                    rel = m.group(2).encode().decode('unicode_escape', errors='ignore')
                    tail = m.group(3).encode().decode('unicode_escape', errors='ignore')
                    result["relations"].append({"head": head, "relation": rel, "tail": tail, "weight": 1.0, "style": "solid"})

            logger.info(f"截断JSON修复结果: entities={len(result['entities'])}, relations={len(result['relations'])}")
        except Exception as e:
            logger.error(f"修复截断JSON失败: {e}")
        return result

    def _extract_array_content(self, text: str, start: int) -> str:
        depth = 1
        i = start
        in_string = False
        escape_next = False
        while i < len(text) and depth > 0:
            c = text[i]
            if escape_next:
                escape_next = False
                i += 1
                continue
            if c == '\\':
                escape_next = True
                i += 1
                continue
            if c == '"':
                in_string = not in_string
            elif not in_string:
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
            i += 1
        if depth == 0:
            return text[start:i - 1]
        return text[start:]

    def _try_brace_completion(self, partial: str) -> Optional[str]:
        depth = 0
        in_string = False
        escape_next = False
        last_comma_pos = -1
        last_open_brace = -1

        for i in range(len(partial)):
            c = partial[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\':
                escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
                last_open_brace = i
            elif c == '}':
                depth -= 1
            elif c == ',':
                last_comma_pos = i
            elif c == '[':
                depth += 1
            elif c == ']':
                depth -= 1

        if depth <= 0:
            return partial

        if in_string:
            partial = partial + '"'

        if partial.rstrip().endswith(','):
            partial = partial.rstrip()[:-1]

        closing = ""
        temp_depth = 0
        in_str = False
        esc = False
        for c in partial:
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if not in_str:
                if c in '{[':
                    temp_depth += 1
                elif c in '}]':
                    temp_depth -= 1

        while temp_depth > 0:
            closing += '}'
            temp_depth -= 1

        return partial + closing

    def _extract_with_regex(self, text: str, title: str) -> Dict[str, Any]:
        entities = []
        relations = []
        seen = set()

        if title:
            clean_title = re.sub(r'[_.]', ' ', os.path.splitext(title)[0]).strip()
            entities.append({"name": clean_title, "entity_type": "document", "attributes": {"source": "title"}})
            seen.add(clean_title.lower())

        person_patterns = [
            r'(?:由)\s*([\u4e00-\u9fff]{2,4}(?:·[\u4e00-\u9fff]{2,8})?)\s*(?:设计|担任|创建|发明|领导|主持|负责)',
            r'(?:[\u4e00-\u9fff]{0,4}(?:籍|裔))[\u4e00-\u9fff]{0,2}(?:建筑师|设计师|艺术家|画家|音乐家|作家|导演|科学家|发明家)\s*([\u4e00-\u9fff]{2,4}(?:·[\u4e00-\u9fff]{2,8})?)(?=\s*[，。、；：！？\n]|设计|担任|创建|发明|领导|主持|负责|$)',
            r'([\u4e00-\u9fff]{2,4}(?:·[\u4e00-\u9fff]{2,8})?)\s*(?:教授|博士|先生|女士|院士|大师|工程师|建筑师|设计师|艺术家|画家|音乐家|作家|诗人|导演|演员|歌手|科学家|发明家|将军|元帅|皇帝|国王|总统|首相|总理|大臣)',
            r'(?:建筑师|设计师|艺术家|画家|音乐家|作家|导演|科学家|发明家)\s*([\u4e00-\u9fff]{2,4})(?=\s*[，。、；：！？\n]|设计|担任|创建|发明|领导|主持|负责|$)',
        ]
        for pattern in person_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.startswith('担任') or name.startswith('负责') or name.startswith('主持'):
                    continue
                if name.lower() not in seen and 2 <= len(name) <= 15:
                    entities.append({"name": name, "entity_type": "person", "attributes": {}})
                    seen.add(name.lower())

        org_patterns = [
            r'([\u4e00-\u9fff]{2,8}(?:公司|集团|事务所|研究所|研究院|大学|学院|协会|学会|基金会|博物馆|美术馆|图书馆|医院|银行|部门|委员会|法院|检察院|政府|部委))',
            r'((?:联合国教科文组织|联合国|世界银行|国际货币基金组织|世界卫生组织|北约|欧盟|东盟|世贸组织|国际奥委会))',
        ]
        for pattern in org_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.startswith('由'):
                    name = name[1:]
                if name.lower() not in seen and 2 <= len(name) <= 25:
                    entities.append({"name": name, "entity_type": "organization", "attributes": {}})
                    seen.add(name.lower())

        location_patterns = [
            r'位于\s*([\u4e00-\u9fff]{2,4}(?:省|市|区|县)[\u4e00-\u9fff]{0,8}(?:省|市|区|县|镇|乡|街|路|道|广场|公园|角))',
            r'((?:[\u4e00-\u9fff]{2,6}(?:省|市|区|县|镇|乡|村|街|路|道|巷|广场|公园|山|河|湖|海|岛|半岛|海峡|湾|高原|平原|盆地|沙漠|草原|森林)))',
            r'((?:北京|上海|广州|深圳|天津|重庆|成都|杭州|南京|武汉|西安|长沙|苏州|郑州|青岛|大连|厦门|合肥|昆明|贵阳|济南|福州|哈尔滨|沈阳|长春|石家庄|太原|兰州|银川|西宁|乌鲁木齐|呼和浩特|南宁|海口|拉萨|香港|澳门|台北))',
            r'((?:中国|美国|英国|法国|德国|日本|韩国|俄罗斯|印度|巴西|澳大利亚|加拿大|意大利|西班牙|墨西哥|埃及|南非|阿根廷|泰国|越南|新加坡|马来西亚|印度尼西亚|菲律宾|新西兰|荷兰|瑞士|瑞典|挪威|丹麦|芬兰|比利时|奥地利|葡萄牙|希腊|土耳其|以色列|伊朗|伊拉克|沙特阿拉伯|阿联酋|丹麦|瑞典))',
        ]
        for pattern in location_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 15:
                    entities.append({"name": name, "entity_type": "location", "attributes": {}})
                    seen.add(name.lower())

        time_patterns = [
            r'((?:公元|公元前)\s*\d{1,4}\s*(?:年|世纪))',
            r'(\d{3,4}\s*(?:年|世纪|年代))',
            r'((?:春秋|战国|秦|汉|三国|晋|南北朝|隋|唐|宋|元|明|清|民国)(?:初期|中期|晚期|末年|初年|年间)?)',
            r'((?:近代|现代|当代|古代|中世纪|文艺复兴|工业革命|一战|二战|冷战)(?:时期|时代)?)',
        ]
        for pattern in time_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 20:
                    entities.append({"name": name, "entity_type": "time", "attributes": {}})
                    seen.add(name.lower())

        event_patterns = [
            r'([\u4e00-\u9fff]{2,10}(?:战争|战役|革命|起义|运动|改革|变法|事件|事变|条约|协议|会议|峰会|展览|博览会|奥运会|世界杯|锦标赛))',
            r'([\u4e00-\u9fff]{2,10}(?:诉讼|纠纷|侵权|犯罪|诈骗|伤害|事故|灾难|地震|洪水|疫情))',
        ]
        for pattern in event_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 15:
                    entities.append({"name": name, "entity_type": "event", "attributes": {}})
                    seen.add(name.lower())

        work_patterns = [
            r'《([^》]{2,40})》',
            r'([\u4e00-\u9fff]{2,12}(?:楼|塔|桥|寺|庙|宫|殿|城|墙|门|亭|阁|院|馆|堂|坊|纪念碑|纪念堂|陵墓|教堂|清真寺|城堡|宫殿|广场|体育场|剧院|博物馆|美术馆|图书馆|车站|机场))',
        ]
        for i, pattern in enumerate(work_patterns):
            for match in re.finditer(pattern, text):
                name = match.group(1) if match.lastindex else match.group(0)
                name = name.strip()
                if name.lower() not in seen and 2 <= len(name) <= 40:
                    etype = "law" if i == 0 else "work"
                    entities.append({"name": name, "entity_type": etype, "attributes": {}})
                    seen.add(name.lower())

        concept_suffixes = sorted(
            [k for k, v in ENTITY_SUFFIXES.items() if v == "concept"],
            key=len, reverse=True
        )
        concept_suffix_group = '|'.join(re.escape(k) for k in concept_suffixes)
        concept_patterns = [
            rf'([\u4e00-\u9fff]{{2,15}}(?:{concept_suffix_group}))',
        ]
        for pattern in concept_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if not self._validator.is_valid_entity(name):
                    continue
                if name.lower() not in seen and 2 <= len(name) <= 20:
                    entities.append({"name": name, "entity_type": "concept", "attributes": {}})
                    seen.add(name.lower())

        material_suffixes = sorted(
            [k for k, v in ENTITY_SUFFIXES.items() if v == "material"],
            key=len, reverse=True
        )
        material_suffix_group = '|'.join(re.escape(k) for k in material_suffixes)
        material_patterns = [
            rf'([\u4e00-\u9fff]{{2,8}}(?:{material_suffix_group}))',
            r'((?:钢筋混凝土|预制混凝土|碳纤维|不锈钢|耐候钢|钛合金|铝合金|玻璃幕墙|清水混凝土))',
        ]
        for pattern in material_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 15:
                    entities.append({"name": name, "entity_type": "material", "attributes": {}})
                    seen.add(name.lower())

        award_patterns = [
            r'([\u4e00-\u9fff]{2,12}(?:奖|勋章|奖章|荣誉|称号|桂冠|锦标))',
            r'((?:诺贝尔|普利策|奥斯卡|格莱美|金棕榈|金狮|金熊|普利兹克|菲尔兹|图灵)[\u4e00-\u9fff]{0,6}奖)',
        ]
        for pattern in award_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 20:
                    entities.append({"name": name, "entity_type": "award", "attributes": {}})
                    seen.add(name.lower())

        term_patterns = [
            r'((?:第[一二三四五六七八九十百千零\d]+[条编章节款项]))',
            r'([\u4e00-\u9fff]{2,10}(?:率|系数|指数|参数|指标|阈值|标准|规范|规程|准则))',
            r'((?:轻微伤|轻伤|重伤))',
        ]
        for pattern in term_patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name.lower() not in seen and 2 <= len(name) <= 20:
                    entities.append({"name": name, "entity_type": "term", "attributes": {}})
                    seen.add(name.lower())

        relation_patterns = [
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:位于|地处|坐落)\s*(?:于\s*)?([\u4e00-\u9fff]{2,15})', 'located_in'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:属于|隶属|归属)\s*(?:于\s*)?([\u4e00-\u9fff]{2,15})', 'belongs_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:依据|根据|依照)\s*([\u4e00-\u9fff《》第条编章\d]{2,30})', 'defined_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:包含|包括|含有)\s*(?:了\s*)?([\u4e00-\u9fff]{2,15})', 'includes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:适用于|适用)\s*([\u4e00-\u9fff]{2,15})', 'applies_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:处罚|惩处|惩治)\s*([\u4e00-\u9fff]{2,15})', 'punishes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:要求|需要|必须具备|须具备)\s*([\u4e00-\u9fff]{2,15})', 'requires'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:导致|引起|造成)\s*([\u4e00-\u9fff]{2,15})', 'causes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:衍生自|源自|来源于)\s*([\u4e00-\u9fff]{2,15})', 'derives_from'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:竞合|冲突)\s*([\u4e00-\u9fff]{2,15})', 'competes_with'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:限制|约束)\s*([\u4e00-\u9fff]{2,15})', 'restricts'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:构成)\s*([\u4e00-\u9fff]{2,15})', 'constitutes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:不构成|尚不构成)\s*([\u4e00-\u9fff]{2,15})', 'not_constitutes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:承担)\s*([\u4e00-\u9fff]{2,15})', 'bears'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:保护)\s*([\u4e00-\u9fff]{2,15})', 'protects'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:分为|分作)\s*([\u4e00-\u9fff、]{2,30})', 'has_type'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:区分|区别于|不同于)\s*([\u4e00-\u9fff]{2,15})', 'distinguishes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:设计|设计者)\s*(?:了\s*)?([\u4e00-\u9fff]{2,20})', 'designed'),
            (r'([\u4e00-\u9fff]{2,20}?)\s*(?:由)\s*([\u4e00-\u9fff]{2,20}?(?:事务所|公司|研究院|研究所|大学|学院|机构)?[\u4e00-\u9fff]{0,4}?)\s*(?:设计|建造|创建|发明|发现|担任|主持)', 'designed_by'),
            (r'([\u4e00-\u9fff]{2,20}?)\s*(?:由)\s*([\u4e00-\u9fff]{2,15}?(?:事务所|公司|研究院|研究所|大学|学院))\s*(?:的\s*)?([\u4e00-\u9fff]{2,8}?)\s*(?:担任|主持|负责)', 'designed_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:建造|建设|修建|兴建)\s*(?:了\s*)?([\u4e00-\u9fff]{2,20})', 'created'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:使用|采用|运用)\s*(?:了\s*)?([\u4e00-\u9fff]{2,15})', 'uses'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:由)\s*([\u4e00-\u9fff]{2,15}?)\s*(?:组成|构成)', 'composed_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:代表|象征)\s*([\u4e00-\u9fff]{2,15})', 'represents'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:是|为)\s*([\u4e00-\u9fff]{2,15}?(?:代表之作|代表作|代表|象征))', 'represents'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:影响|作用于)\s*([\u4e00-\u9fff]{2,15})', 'influences'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:支持|支撑)\s*([\u4e00-\u9fff]{2,15})', 'supports'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:依赖|取决于)\s*([\u4e00-\u9fff]{2,15})', 'depends_on'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:实现|实行)\s*([\u4e00-\u9fff]{2,15})', 'implements'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:先于)\s*([\u4e00-\u9fff]{2,15})', 'precedes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:与)\s*([\u4e00-\u9fff]{2,15}?)\s*(?:矛盾|对立|冲突)', 'contradicts'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:工作于|就职于|任职于)\s*([\u4e00-\u9fff]{2,15})', 'works_for'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:受雇于|被聘于)\s*([\u4e00-\u9fff]{2,15})', 'employed_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:管理|管辖|领导)\s*([\u4e00-\u9fff]{2,15})', 'managed_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:拥有|所有)\s*([\u4e00-\u9fff]{2,15})', 'owned_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:负责|主管)\s*([\u4e00-\u9fff]{2,15})', 'responsible_for'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:投资|注资)\s*([\u4e00-\u9fff]{2,15})', 'invests_in'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:合作|协作)\s*([\u4e00-\u9fff]{2,15})', 'cooperates_with'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:战胜|征服|击败)\s*([\u4e00-\u9fff]{2,15})', 'conquered'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:结盟|联盟)\s*([\u4e00-\u9fff]{2,15})', 'allied_with'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:规定|规范)\s*([\u4e00-\u9fff]{2,15})', 'prescribes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:禁止|严禁|不许)\s*([\u4e00-\u9fff]{2,15})', 'prohibits'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:允许|准许|许可)\s*([\u4e00-\u9fff]{2,15})', 'permits'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:建成于|竣工于|完成于)\s*([\u4e00-\u9fff\d]{2,15})', 'completed_in'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:由)\s*([\u4e00-\u9fff]{2,15}?)\s*(?:材料|材质)', 'made_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:获得|荣获|斩获)\s*([\u4e00-\u9fff\d]{2,15}.*?(?:奖|勋章|奖章|荣誉|称号))', 'awarded'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:覆盖|涵盖)\s*([\u4e00-\u9fff]{2,15})', 'covers'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:风格|样式)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'style_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:转化为|变为|演变为)\s*([\u4e00-\u9fff]{2,15})', 'transforms_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:进化为|演化为)\s*([\u4e00-\u9fff]{2,15})', 'evolves_into'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:分裂为)\s*([\u4e00-\u9fff]{2,15})', 'splits_into'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:合并为)\s*([\u4e00-\u9fff]{2,15})', 'merges_into'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:迁移到|迁至)\s*([\u4e00-\u9fff]{2,15})', 'migrates_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:引用|援引)\s*([\u4e00-\u9fff]{2,15})', 'cites'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:定义|界定)\s*([\u4e00-\u9fff]{2,15})', 'defines'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:解释|阐释|说明)\s*([\u4e00-\u9fff]{2,15})', 'explains'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:阻止|防止)\s*([\u4e00-\u9fff]{2,15})', 'prevents'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:促成|促进|推动)\s*([\u4e00-\u9fff]{2,15})', 'facilitates'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:引发|触发)\s*([\u4e00-\u9fff]{2,15})', 'triggers'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:排除)\s*([\u4e00-\u9fff]{2,15})', 'excludes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:分类|归类)\s*([\u4e00-\u9fff]{2,15})', 'classifies'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:控制|掌控)\s*([\u4e00-\u9fff]{2,15})', 'controls'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:产生|产出)\s*([\u4e00-\u9fff]{2,15})', 'produces'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:改变|转变)\s*([\u4e00-\u9fff]{2,15})', 'changes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:连接|相连|连通)\s*([\u4e00-\u9fff]{2,15})', 'connected_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:邻近|毗邻|靠近)\s*([\u4e00-\u9fff]{2,15})', 'adjacent_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:相似于|类似于)\s*([\u4e00-\u9fff]{2,15})', 'similar_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:优于|胜过)\s*([\u4e00-\u9fff]{2,15})', 'superior_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:对立|反对)\s*([\u4e00-\u9fff]{2,15})', 'opposes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:兼容|相容)\s*([\u4e00-\u9fff]{2,15})', 'compatible_with'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:冲突|矛盾)\s*([\u4e00-\u9fff]{2,15})', 'conflicts_with'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:被列为|列入)\s*([\u4e00-\u9fff]{2,15})', 'listed_as'),
            (r'([\u4e00-\u9fff]{2,20}?)\s*(?:于\s*\d{4}\s*年?\s*)?(?:被)\s*([\u4e00-\u9fff]{2,15}?(?:组织|机构|委员会|教科文组织))\s*(?:列为|列入)', 'listed_as'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:结构|构造)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'structure_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:起始于|始于|开端于)\s*([\u4e00-\u9fff\d]{2,15})', 'started_at'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:终止于|终于|结束于)\s*([\u4e00-\u9fff\d]{2,15})', 'ended_at'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:持续|延续了)\s*([\u4e00-\u9fff\d]{2,15})', 'lasted'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:发生于|发生在)\s*([\u4e00-\u9fff]{2,15})', 'occurred_at'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:表现为|体现为)\s*([\u4e00-\u9fff]{2,15})', 'manifests_as'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:类型|类别)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'type_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:度量|测量|衡量)\s*(?:为|是)\s*([\u4e00-\u9fff\d]{2,15})', 'measured_as'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:执行|实行|实施)\s*([\u4e00-\u9fff]{2,15})', 'executes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:输入|接收)\s*([\u4e00-\u9fff]{2,15})', 'inputs'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:输出|产出)\s*([\u4e00-\u9fff]{2,15})', 'outputs'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:证实|确认)\s*([\u4e00-\u9fff]{2,15})', 'confirms'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:质疑|怀疑)\s*([\u4e00-\u9fff]{2,15})', 'questions'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:评价|评估)\s*([\u4e00-\u9fff]{2,15})', 'evaluates'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:偏好|倾向于)\s*([\u4e00-\u9fff]{2,15})', 'prefers'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:对应|相应于)\s*([\u4e00-\u9fff]{2,15})', 'corresponds_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:映射)\s*([\u4e00-\u9fff]{2,15})', 'maps_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:生长为|成长为)\s*([\u4e00-\u9fff]{2,15})', 'grows_into'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:衰退为|衰落为)\s*([\u4e00-\u9fff]{2,15})', 'declines_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:指代|指称)\s*([\u4e00-\u9fff]{2,15})', 'refers_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:翻译为|译为)\s*([\u4e00-\u9fff]{2,15})', 'translates_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:来源于|来自)\s*([\u4e00-\u9fff]{2,15})', 'source_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:约束|制约)\s*([\u4e00-\u9fff]{2,15})', 'constrains'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:成员|一员)\s*(?:属于|在)\s*([\u4e00-\u9fff]{2,15})', 'member_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:依附|附着|附属)\s*(?:于\s*)?([\u4e00-\u9fff]{2,15})', 'attached_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:劣于|不及)\s*([\u4e00-\u9fff]{2,15})', 'inferior_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:后继于|继任于)\s*([\u4e00-\u9fff]{2,15})', 'succeeded_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:并行于|并驾齐驱)\s*([\u4e00-\u9fff]{2,15})', 'parallel_to'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:阶段|环节)\s*(?:属于|在)\s*([\u4e00-\u9fff]{2,15})', 'phase_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:授权|委托)\s*([\u4e00-\u9fff]{2,15})', 'authorized_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:被收购|被并购)\s*([\u4e00-\u9fff]{2,15})', 'acquired_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:被中断|被打断)\s*([\u4e00-\u9fff]{2,15})', 'interrupted_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:恢复|恢复于)\s*([\u4e00-\u9fff]{2,15})', 'resumed_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:信任|信赖)\s*([\u4e00-\u9fff]{2,15})', 'trusts'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:推荐|举荐)\s*([\u4e00-\u9fff]{2,15})', 'recommends'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:不喜欢|厌恶)\s*([\u4e00-\u9fff]{2,15})', 'dislikes'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:重要性|重要程度)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'importance_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:置信度|可信度)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'confidence_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:具有|拥有)\s*(?:了\s*)?([\u4e00-\u9fff]{2,15}(?:特征|特点|属性|功能|能力))', 'has_feature'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:具备)\s*([\u4e00-\u9fff]{2,15}(?:能力|功能))', 'has_capability'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:标识|标记)\s*(?:为|是)\s*([\u4e00-\u9fff]{2,15})', 'identified_by'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:亲属|亲戚)\s*([\u4e00-\u9fff]{2,15})', 'relative_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:同事|同僚)\s*([\u4e00-\u9fff]{2,15})', 'colleague_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:同学|校友)\s*([\u4e00-\u9fff]{2,15})', 'classmate_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:邻居|相邻)\s*([\u4e00-\u9fff]{2,15})', 'neighbor_of'),
            (r'([\u4e00-\u9fff]{2,15}?)\s*(?:下属|部下)\s*([\u4e00-\u9fff]{2,15})', 'subordinate_of'),
        ]

        seen_rel_keys = set()

        for pattern, rel_type in relation_patterns:
            for match in re.finditer(pattern, text):
                groups = match.groups()
                head = groups[0].strip()
                tail = groups[-1].strip()

                if len(groups) == 3 and groups[1]:
                    middle = groups[1].strip()
                    if middle and self._validator.is_valid_entity(middle):
                        if middle.lower() not in seen:
                            entities.append({"name": middle, "entity_type": "organization", "attributes": {}})
                            seen.add(middle.lower())
                        if head and self._validator.is_valid_entity(head):
                            rel_key_mid = f"{head}|works_for|{middle}"
                            if rel_key_mid not in seen_rel_keys:
                                seen_rel_keys.add(rel_key_mid)
                                relations.append({
                                    "head": head,
                                    "relation": "works_for",
                                    "tail": middle,
                                    "weight": 0.8,
                                    "style": "solid",
                                })
                    tail = middle + "的" + tail if middle else tail

                if not head or not tail or head == tail:
                    continue
                if not self._validator.is_valid_entity(head) or not self._validator.is_valid_entity(tail):
                    continue
                if len(head) < 2 or len(tail) < 2 or len(head) > 20 or len(tail) > 30:
                    continue

                expanded = self._validator.split_multi_entity_relation(head, rel_type, tail)
                for h, r, t in expanded:
                    if h.lower() not in seen:
                        entities.append({"name": h, "entity_type": "concept", "attributes": {}})
                        seen.add(h.lower())
                    if t.lower() not in seen:
                        entities.append({"name": t, "entity_type": "concept", "attributes": {}})
                        seen.add(t.lower())

                    rel_key = f"{h}|{r}|{t}"
                    if rel_key not in seen_rel_keys:
                        seen_rel_keys.add(rel_key)
                        relations.append({
                            "head": h,
                            "relation": r,
                            "tail": t,
                            "weight": 0.8,
                            "style": "solid",
                        })

        sentence_endings = re.split(r'[。！？\n]', text)
        entity_names_list = [e["name"] for e in entities]
        important_types = {'law', 'crime', 'organization', 'document', 'person', 'work', 'location', 'event'}
        important_names = {e["name"] for e in entities if e.get("entity_type") in important_types}
        for sentence in sentence_endings:
            found_in_sentence = []
            for name in sorted(entity_names_list, key=len, reverse=True):
                if name in sentence:
                    found_in_sentence.append(name)

            co_occur_count = 0
            for i in range(len(found_in_sentence) - 1):
                head = found_in_sentence[i]
                tail = found_in_sentence[i + 1]
                if head not in important_names and tail not in important_names:
                    continue
                if co_occur_count >= 2:
                    break
                rel_key = f"{head}|co_occurs_with|{tail}"
                if rel_key not in seen_rel_keys:
                    seen_rel_keys.add(rel_key)
                    relations.append({
                        "head": head,
                        "relation": "co_occurs_with",
                        "tail": tail,
                        "weight": 0.2,
                        "style": "dashed",
                    })
                    co_occur_count += 1

        return {"entities": entities, "relations": relations}

    async def build_from_text(self, text: str, title: str = "",
                              struct_tags: str = "", doc_key: str = "") -> Dict[str, Any]:
        extraction = await self.extract_from_text(text, title, struct_tags)

        if doc_key:
            doc_graph = kg_store.get_or_create_doc_graph(doc_key)
            doc_graph.clear()
        else:
            doc_graph = None

        entity_map = {}
        target_graph = doc_graph or kg_store.get_or_create_doc_graph("__global__")
        for ent_data in extraction.get("entities", []):
            name = ent_data.get("name", "")
            entity_type = ent_data.get("entity_type", "concept")
            attrs = ent_data.get("attributes", {})
            if name:
                node = target_graph.add_node(label=name, node_type=entity_type, properties=attrs)
                entity_map[name] = node

        for rel_data in extraction.get("relations", []):
            head_name = rel_data.get("head", rel_data.get("source", ""))
            tail_name = rel_data.get("tail", rel_data.get("target", ""))
            rel_type = rel_data.get("relation", rel_data.get("relation_type", "related_to"))
            weight = rel_data.get("weight", 1.0)
            style = rel_data.get("style", "solid")

            head_node = entity_map.get(head_name)
            tail_node = entity_map.get(tail_name)

            if not head_node:
                head_node = target_graph.add_node(label=head_name, node_type="concept")
                entity_map[head_name] = head_node
            if not tail_node:
                tail_node = target_graph.add_node(label=tail_name, node_type="concept")
                entity_map[tail_name] = tail_node

            rel_style = RELATION_STYLES.get(rel_type, {"style": style, "color": "#6b7280"})
            edge_props = {
                "style": rel_style.get("style", style),
                "color": rel_style.get("color", "#6b7280"),
            }

            target_graph.add_edge(
                source_id=head_node.id,
                target_id=tail_node.id,
                relation_type=rel_type,
                weight=weight,
                properties=edge_props,
            )

        if doc_key:
            kg_store._save_doc(doc_key)
        else:
            kg_store._save_doc("__global__")

        self._enhance_graph_density(target_graph, entity_map, text)

        if doc_key:
            kg_store._save_doc(doc_key)
        else:
            kg_store._save_doc("__global__")

        return {
            "entity_count": len(extraction.get("entities", [])),
            "relation_count": len(extraction.get("relations", [])),
            "total_nodes": target_graph.node_count,
            "total_edges": target_graph.edge_count,
        }

    def _enhance_graph_density(self, graph, entity_map: Dict, text: str):
        nodes = graph.get_all_nodes()
        edges = graph.get_all_edges()

        connected_ids = set()
        for edge in edges:
            connected_ids.add(edge.source)
            connected_ids.add(edge.target)

        isolated_nodes = [n for n in nodes if n.id not in connected_ids]
        name_to_node = {n.label: n for n in nodes}
        seen_edge_keys = set()
        for e in edges:
            seen_edge_keys.add(f"{e.source}|{e.relation_type}|{e.target}")

        type_groups = {}
        for node in nodes:
            t = node.node_type
            if t not in type_groups:
                type_groups[t] = []
            type_groups[t].append(node)

        type_representatives = {}
        for t, group in type_groups.items():
            type_representatives[t] = group[0]

        for iso_node in isolated_nodes:
            nt = iso_node.node_type
            if nt in type_representatives and type_representatives[nt].id != iso_node.id:
                rep = type_representatives[nt]
                rel_type = "has_type" if nt != "concept" else "type_of"
                edge_key = f"{iso_node.id}|{rel_type}|{rep.id}"
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    graph.add_edge(
                        source_id=iso_node.id,
                        target_id=rep.id,
                        relation_type=rel_type,
                        weight=0.5,
                        properties={"style": "dashed", "color": "#8b5cf6"},
                    )

        attr_patterns = [
            (r'([\u4e00-\u9fff]{2,10}(?:技术|能力|功能|特性|特征|属性|优势|特点))\s*(?:包括|包含|具有|拥有|具备)\s*([\u4e00-\u9fff]{2,15})', 'has_feature'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:是|为|属于)\s*([\u4e00-\u9fff]{2,10}(?:技术|模式|方法|体系|系统|平台|框架|架构))', 'type_of'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:的)\s*([\u4e00-\u9fff]{2,8}(?:核心|关键|重要|主要|基础|基本)(?:技术|能力|功能|特征|要素|组成部分))', 'has_attribute'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:由|由其)\s*([\u4e00-\u9fff]{2,10}(?:组成|构成|包含))', 'composed_of'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:分为|划分|分成)\s*([\u4e00-\u9fff、]{2,30})', 'includes'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:的)\s*(?:主要|核心|重要)?(?:应用|用途|作用|目的)\s*(?:是|包括|为)\s*([\u4e00-\u9fff]{2,15})', 'has_capability'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:依赖|依托|基于|借助)\s*([\u4e00-\u9fff]{2,10})', 'depends_on'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:推动|促进|带动|引领)\s*([\u4e00-\u9fff]{2,10}(?:发展|增长|进步|创新))', 'facilitates'),
            (r'([\u4e00-\u9fff]{2,10})\s*(?:提供|支撑|赋能|服务)\s*([\u4e00-\u9fff]{2,10})', 'supports'),
        ]

        for pattern, rel_type in attr_patterns:
            for match in re.finditer(pattern, text):
                head_name = match.group(1).strip()
                tail_text = match.group(2).strip()

                head_node = name_to_node.get(head_name)
                if not head_node:
                    continue

                tail_parts = re.split(r'[、，,和与及以及]', tail_text)
                for part in tail_parts:
                    part = part.strip()
                    if len(part) < 2:
                        continue
                    tail_node = name_to_node.get(part)
                    if not tail_node:
                        tail_node = graph.add_node(label=part, node_type="concept")
                        name_to_node[part] = tail_node

                    edge_key = f"{head_node.id}|{rel_type}|{tail_node.id}"
                    if edge_key not in seen_edge_keys:
                        seen_edge_keys.add(edge_key)
                        graph.add_edge(
                            source_id=head_node.id,
                            target_id=tail_node.id,
                            relation_type=rel_type,
                            weight=0.7,
                            properties={"style": "solid", "color": "#8b5cf6"},
                        )

        for iso_node in isolated_nodes:
            name = iso_node.label
            for other_node in nodes:
                if other_node.id == iso_node.id:
                    continue
                if name in other_node.label or other_node.label in name:
                    if len(name) >= 2 and len(other_node.label) >= 2:
                        if len(name) < len(other_node.label):
                            rel_type = "part_of"
                            edge_key = f"{iso_node.id}|{rel_type}|{other_node.id}"
                        else:
                            rel_type = "includes"
                            edge_key = f"{other_node.id}|{rel_type}|{iso_node.id}"
                        if edge_key not in seen_edge_keys:
                            seen_edge_keys.add(edge_key)
                            if len(name) < len(other_node.label):
                                graph.add_edge(
                                    source_id=iso_node.id,
                                    target_id=other_node.id,
                                    relation_type=rel_type,
                                    weight=0.4,
                                    properties={"style": "dashed", "color": "#f59e0b"},
                                )
                            else:
                                graph.add_edge(
                                    source_id=other_node.id,
                                    target_id=iso_node.id,
                                    relation_type=rel_type,
                                    weight=0.4,
                                    properties={"style": "dashed", "color": "#f59e0b"},
                                )
                        break

        sentences = re.split(r'[。！？\n]', text)
        entity_names = sorted([n.label for n in nodes], key=len, reverse=True)
        co_occur_added = 0
        for sent in sentences:
            if co_occur_added >= 200:
                break
            found = []
            for name in entity_names:
                if name in sent:
                    found.append(name)
            for i in range(len(found)):
                for j in range(i + 1, min(i + 3, len(found))):
                    h_node = name_to_node.get(found[i])
                    t_node = name_to_node.get(found[j])
                    if h_node and t_node:
                        edge_key = f"{h_node.id}|co_occurs_with|{t_node.id}"
                        rev_key = f"{t_node.id}|co_occurs_with|{h_node.id}"
                        if edge_key not in seen_edge_keys and rev_key not in seen_edge_keys:
                            seen_edge_keys.add(edge_key)
                            graph.add_edge(
                                source_id=h_node.id,
                                target_id=t_node.id,
                                relation_type="co_occurs_with",
                                weight=0.3,
                                properties={"style": "dashed", "color": "#94a3b8"},
                            )
                            co_occur_added += 1

        logger.info(f"图谱密度增强: 处理孤立节点 {len(isolated_nodes)} 个，提取属性/层级/共现关系，新增共现 {co_occur_added} 条")


kg_builder = KGBuilder()
