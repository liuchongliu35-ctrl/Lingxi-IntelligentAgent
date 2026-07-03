#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务复杂度分析器
实现七维度加权评分模型与启发式规则相结合的混合策略
"""

from typing import Dict, Any, List, Optional
import re
from dataclasses import dataclass


@dataclass
class StructuredTask:
    """结构化任务对象"""
    original_input: str
    cleaned_input: str
    intent: str
    entities: List[str]
    parameters: Dict[str, Any]
    complexity_score: float = 0.0
    complexity_level: str = "unknown"
    execution_strategy: str = "unknown"


class ComplexityAnalyzer:
    """任务复杂度分析器"""
    
    def __init__(self, model_manager=None):
        """初始化复杂度分析器
        
        Args:
            model_manager: 模型管理器（用于意图识别等）
        """
        self.model_manager = model_manager
        
        # 启发式规则配置
        self.ambiguity_keywords = [
            "帮我分析一下", "帮我看看", "帮我想想",
            "分析一下", "看看", "想想",
            "怎么样", "如何", "怎样"
        ]
        
        self.high_risk_keywords = [
            "合同", "法律", "法规", "条款",
            "医疗", "诊断", "治疗",
            "金融", "投资", "股票", "基金",
            "密码", "账号", "验证码"
        ]
        
        self.simple_task_patterns = [
            r"^计算\s*[0-9].*$",
            r"^[0-9].*\s*[+\-*/%]\s*[0-9].*$",
            r"^现在几点",
            r"^今天星期",
            r"^今天日期",
            r"^翻译\s*[\u4e00-\u9fa5a-zA-Z]+"
        ]
        
        # 七维度权重配置
        self.dimension_weights = {
            "uncertainty": 3.0,      # 不确定性与模糊度
            "steps": 2.0,            # 步骤数量与依赖性
            "risk": 1.8,             # 领域专业性与风险
            "tools": 1.5,            # 工具需求与组合
            "information": 1.5,      # 信息获取与来源
            "data_processing": 1.2,  # 数据处理与分析
            "creativity": 1.0        # 创造性与生成需求
        }
        
        # 意图分类
        self.intent_patterns = {
            "calculation": [r"计算", r"求和", r"加", r"减", r"乘", r"除"],
            "search": [r"搜索", r"查找", r"找", r"查询"],
            "write": [r"写", r"撰写", r"生成", r"创建"],
            "analyze": [r"分析", r"总结", r"评估", r"解读"],
            "plan": [r"计划", r"规划", r"安排", r"步骤"],
            "translate": [r"翻译", r"译成"],
            "read": [r"读取", r"打开", r"查看"],
            "execute": [r"执行", r"运行", r"调试"]
        }
    
    def analyze(self, user_input: str) -> StructuredTask:
        """完整的复杂度分析流程"""
        # 步骤1：指令预处理与标准化
        task = self._preprocess_input(user_input)
        
        # 步骤2：启发式规则快速判断
        heuristic_result = self._apply_heuristic_rules(task)
        if heuristic_result:
            task.complexity_level = heuristic_result["level"]
            task.execution_strategy = heuristic_result["strategy"]
            return task
        
        # 步骤3：七维度加权评分计算
        scores = self._calculate_seven_dimension_scores(task)
        task.complexity_score = self._calculate_total_score(scores)
        
        # 步骤4：复杂度等级映射
        task.complexity_level = self._map_to_complexity_level(task.complexity_score)
        task.execution_strategy = self._map_to_execution_strategy(task.complexity_level)
        
        return task
    
    def _preprocess_input(self, user_input: str) -> StructuredTask:
        """指令预处理与标准化"""
        # 1. 文本清洗
        cleaned_input = self._clean_text(user_input)
        
        # 2. 意图识别
        intent = self._recognize_intent(cleaned_input)
        
        # 3. 实体提取
        entities = self._extract_entities(cleaned_input)
        
        # 4. 参数解析
        parameters = self._parse_parameters(cleaned_input)
        
        # 5. 生成结构化任务对象
        return StructuredTask(
            original_input=user_input,
            cleaned_input=cleaned_input,
            intent=intent,
            entities=entities,
            parameters=parameters
        )
    
    def _clean_text(self, text: str) -> str:
        """文本清洗：去除多余空格、标点、特殊字符"""
        # 去除多余空格和换行
        text = re.sub(r'\s+', ' ', text).strip()
        
        # 去除特殊字符（保留中文、英文、数字、常用标点）
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9，。！？、：；\"\'（）\s]', '', text)
        
        return text
    
    def _recognize_intent(self, text: str) -> str:
        """意图识别"""
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    return intent
        return "unknown"
    
    def _extract_entities(self, text: str) -> List[str]:
        """实体提取：提取关键实体"""
        entities = []
        
        # 提取数字（数量、日期等）
        numbers = re.findall(r'(\d+[\u4e00-\u9fa5]*)\s*(篇|个|条|份|次|天|年)', text)
        for num, unit in numbers:
            entities.append(f"{num}{unit}")
        
        # 提取文件类型
        file_types = ["文档", "文件", "PDF", "Excel", "表格", "图片"]
        for ft in file_types:
            if ft in text:
                entities.append(ft)
        
        # 提取工具相关词
        tools = ["搜索", "浏览器", "计算器", "翻译"]
        for tool in tools:
            if tool in text:
                entities.append(tool)
        
        return entities
    
    def _parse_parameters(self, text: str) -> Dict[str, Any]:
        """参数解析：提取任务的明确参数"""
        params = {}
        
        # 提取数量参数
        num_match = re.search(r'(\d+)\s*(篇|个|条|份)', text)
        if num_match:
            params["quantity"] = int(num_match.group(1))
        
        # 提取时间参数
        time_match = re.search(r'(\d{4})年', text)
        if time_match:
            params["year"] = int(time_match.group(1))
        
        # 提取格式参数
        formats = ["Excel", "PDF", "Word", "Markdown", "CSV"]
        for fmt in formats:
            if fmt in text:
                params["format"] = fmt
                break
        
        return params
    
    def _apply_heuristic_rules(self, task: StructuredTask) -> Optional[Dict[str, str]]:
        """应用启发式规则快速判断"""
        # 规则1：模糊性优先
        for keyword in self.ambiguity_keywords:
            if keyword in task.cleaned_input:
                return {
                    "level": "ambiguous",
                    "strategy": "macro"
                }
        
        # 规则2：高风险优先
        for keyword in self.high_risk_keywords:
            if keyword in task.cleaned_input:
                return {
                    "level": "high_risk",
                    "strategy": "meso_advanced"
                }
        
        # 规则3：简单任务捷径
        for pattern in self.simple_task_patterns:
            if re.match(pattern, task.cleaned_input):
                return {
                    "level": "simple",
                    "strategy": "micro"
                }
        
        return None
    
    def _calculate_seven_dimension_scores(self, task: StructuredTask) -> Dict[str, float]:
        """计算七维度评分"""
        scores = {}
        
        # 1. 不确定性与模糊度（0-5分）
        scores["uncertainty"] = self._score_uncertainty(task)
        
        # 2. 步骤数量与依赖性（0-5分）
        scores["steps"] = self._score_steps(task)
        
        # 3. 领域专业性与风险（0-5分）
        scores["risk"] = self._score_risk(task)
        
        # 4. 工具需求与组合（0-5分）
        scores["tools"] = self._score_tools(task)
        
        # 5. 信息获取与来源（0-5分）
        scores["information"] = self._score_information(task)
        
        # 6. 数据处理与分析（0-5分）
        scores["data_processing"] = self._score_data_processing(task)
        
        # 7. 创造性与生成需求（0-5分）
        scores["creativity"] = self._score_creativity(task)
        
        return scores
    
    def _score_uncertainty(self, task: StructuredTask) -> float:
        """评估不确定性与模糊度"""
        if task.intent == "unknown":
            return 4.0
        
        if not task.entities and not task.parameters:
            return 3.0
        
        if "?" in task.original_input or "？" in task.original_input:
            return 2.0
        
        return 1.0
    
    def _score_steps(self, task: StructuredTask) -> float:
        """评估步骤数量与依赖性"""
        intent_steps = {
            "calculation": 1,
            "translate": 1,
            "search": 2,
            "read": 2,
            "write": 3,
            "analyze": 3,
            "plan": 4
        }
        
        base_steps = intent_steps.get(task.intent, 2)
        
        if len(task.entities) >= 3:
            return min(base_steps + 1, 5)
        
        return base_steps
    
    def _score_risk(self, task: StructuredTask) -> float:
        """评估领域专业性与风险"""
        risk_keywords = ["法律", "医疗", "金融", "合同", "诊断", "投资"]
        for keyword in risk_keywords:
            if keyword in task.cleaned_input:
                return 4.0
        
        technical_keywords = ["代码", "编程", "API", "数据库"]
        for keyword in technical_keywords:
            if keyword in task.cleaned_input:
                return 2.0
        
        return 1.0
    
    def _score_tools(self, task: StructuredTask) -> float:
        """评估工具需求与组合"""
        tool_count = 0
        
        tool_keywords = ["搜索", "浏览器", "计算器", "翻译", "读取", "写入"]
        for keyword in tool_keywords:
            if keyword in task.cleaned_input:
                tool_count += 1
        
        if tool_count >= 3:
            return 5.0
        elif tool_count == 2:
            return 3.0
        elif tool_count == 1:
            return 2.0
        else:
            return 1.0
    
    def _score_information(self, task: StructuredTask) -> float:
        """评估信息获取与来源"""
        info_keywords = ["搜索", "查找", "查询", "最新", "资料", "数据"]
        
        if any(kw in task.cleaned_input for kw in info_keywords):
            return 3.0
        
        if "外部" in task.cleaned_input or "网络" in task.cleaned_input:
            return 4.0
        
        return 1.0
    
    def _score_data_processing(self, task: StructuredTask) -> float:
        """评估数据处理与分析"""
        data_keywords = ["分析", "统计", "计算", "数据", "表格", "Excel"]
        
        count = sum(1 for kw in data_keywords if kw in task.cleaned_input)
        
        if count >= 2:
            return 4.0
        elif count == 1:
            return 2.0
        else:
            return 1.0
    
    def _score_creativity(self, task: StructuredTask) -> float:
        """评估创造性与生成需求"""
        creative_keywords = ["写", "生成", "创建", "设计", "建议", "方案"]
        
        if any(kw in task.cleaned_input for kw in creative_keywords):
            return 3.0
        
        if task.intent in ["write", "plan"]:
            return 4.0
        
        return 1.0
    
    def _calculate_total_score(self, scores: Dict[str, float]) -> float:
        """计算加权总分"""
        total = 0.0
        for dimension, score in scores.items():
            weight = self.dimension_weights.get(dimension, 1.0)
            total += score * weight
        return total
    
    def _map_to_complexity_level(self, score: float) -> str:
        """映射到复杂度等级"""
        if score <= 10:
            return "simple"
        elif score <= 30:
            return "medium"
        else:
            return "complex"
    
    def _map_to_execution_strategy(self, level: str) -> str:
        """映射到执行策略"""
        strategy_map = {
            "simple": "micro",
            "medium": "meso",
            "complex": "meso_advanced",
            "ambiguous": "macro",
            "high_risk": "meso_advanced"
        }
        return strategy_map.get(level, "micro")


# 测试示例
if __name__ == "__main__":
    analyzer = ComplexityAnalyzer()
    
    test_cases = [
        "计算 1024 × 768",
        "帮我搜索关于大语言模型最新进展的5篇论文，并总结它们的核心贡献",
        "帮我分析一下市场",
        "帮我起草一份房屋租赁合同",
        "帮我写一段Python代码来处理Excel数据"
    ]
    
    for test_input in test_cases:
        result = analyzer.analyze(test_input)
        print(f"输入: {test_input}")
        print(f"意图: {result.intent}")
        print(f"实体: {result.entities}")
        print(f"参数: {result.parameters}")
        print(f"复杂度分数: {result.complexity_score:.2f}")
        print(f"复杂度等级: {result.complexity_level}")
        print(f"执行策略: {result.execution_strategy}")
        print("-" * 50)
