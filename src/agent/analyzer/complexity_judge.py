from typing import Dict, Any, List, Optional, Tuple
import re
import jieba
from dataclasses import dataclass, field

from agent.ComplexIntent_filter import MultiIntentFilter
from agent.heuristic_rules import HeuristicRules
from agent.entity_extractor import EntityExtractor


@dataclass
class StructuredTask:
    original_input: str = ""
    cleaned_input: str = ""
    hit_keywords: List[str] = field(default_factory=list)
    intent: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    tokens: List[str] = field(default_factory=list)
    complexity_score: float = 0.0
    complexity_level: str = "unknown"
    execution_strategy: str = "unknown"
    heuristic_result: Optional[Dict[str, str]] = None
    seven_dimension_scores: Dict[str, float] = field(default_factory=dict)


class ComplexityJudge:
    def __init__(self):
        self.multi_intent_filter = MultiIntentFilter()
        self.heuristic_rules = HeuristicRules()
        self.entity_extractor = EntityExtractor()
        self.task = StructuredTask()

        self.dimension_weights = {
            "uncertainty": 3.0,
            "steps": 2.0,
            "domain_risk": 1.8,
            "tools": 1.5,
            "information": 1.5,
            "data_processing": 1.2,
            "creativity": 1.0
        }

        self.intent_to_steps = {
            "计算": 1,
            "读取": 1,
            "查询": 1,
            "搜索": 2,
            "总结": 2,
            "分析": 3,
            "写": 3,
            "翻译": 2,
            "规划": 4,
            "执行": 2,
            "推荐": 3,
            "比较": 3,
            "解释": 2,
            "提取": 2
        }

        self.intent_to_tools = {
            "计算": 1,
            "读取": 1,
            "查询": 1,
            "搜索": 2,
            "总结": 2,
            "分析": 3,
            "写": 2,
            "翻译": 1,
            "规划": 3,
            "执行": 2,
            "推荐": 3,
            "比较": 3,
            "解释": 2,
            "提取": 2
        }

    def preprocess(self, text: str) -> Tuple[str, List[str]]:
        cleaned_text = self._clean_text(text)
        self.task.original_input = text
        self.task.cleaned_input = cleaned_text
        tokens = self._tokenize(cleaned_text)
        self.task.tokens = tokens
        has_multi, hit_keywords = self.multi_intent_filter.has_multi_intent(tokens)
        self.task.hit_keywords = hit_keywords
        self.task.intent = self.multi_intent_filter.rule_matching(tokens, is_multi_intent=has_multi)
        
        entity_result = self.entity_extractor.extract_all(cleaned_text)
        self.task.entities = entity_result['entities']
        self.task.parameters = entity_result['parameters']
        
        return cleaned_text, tokens

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9，。！？、：；""''（）\\s]', '', text)
        return text

    def _tokenize(self, text: str) -> List[str]:
        tokens = jieba.cut(text)
        tokens = list(tokens)
        return tokens

    def check_heuristic_rules(self) -> Dict[str, str]:
        result = self.heuristic_rules.check_all_rules(
            self.task.cleaned_input,
            self.task.intent,
            self.task.parameters
        )
        self.task.heuristic_result = result
        return result

    def calculate_seven_dimension_scores(self) -> Dict[str, float]:
        scores = {}
        
        scores["uncertainty"] = self._score_uncertainty()
        scores["steps"] = self._score_steps()
        scores["domain_risk"] = self._score_domain_risk()
        scores["tools"] = self._score_tools()
        scores["information"] = self._score_information()
        scores["data_processing"] = self._score_data_processing()
        scores["creativity"] = self._score_creativity()
        
        self.task.seven_dimension_scores = scores
        return scores

    def _score_uncertainty(self) -> float:
        text = self.task.cleaned_input
        intent = self.task.intent
        parameters = self.task.parameters
        
        score = 0
        
        if len(intent) == 0:
            score += 4
        elif len(intent) >= 2:
            score += 1
        
        if len(parameters) == 0:
            score += 3
        elif len(parameters) == 1:
            score += 1
        
        ambiguous_keywords = ["什么", "怎样", "如何", "为什么", "怎么回事", "分析一下"]
        for keyword in ambiguous_keywords:
            if keyword in text:
                score += 1
        
        if len(text) < 10:
            score += 2
        
        return min(score, 5)

    def _score_steps(self) -> float:
        intent = self.task.intent
        
        if not intent:
            return 3
        
        total_steps = 0
        for i in intent:
            total_steps += self.intent_to_steps.get(i, 2)
        
        if len(intent) >= 2:
            total_steps += 2
        
        if total_steps <= 1:
            return 0
        elif total_steps == 2:
            return 1
        elif total_steps == 3:
            return 2
        elif total_steps == 4:
            return 3
        elif total_steps == 5:
            return 4
        else:
            return 5

    def _score_domain_risk(self) -> float:
        text = self.task.cleaned_input
        
        high_risk_keywords = ["合同", "协议", "诉讼", "诊断", "治疗", "处方",
                             "投资", "股票", "基金", "贷款", "保险", "身份证",
                             "密码", "银行卡", "手机号"]
        
        medium_risk_keywords = ["分析", "评估", "研究", "报告", "建议", "规划"]
        
        score = 0
        
        for keyword in high_risk_keywords:
            if keyword in text:
                score += 3
        
        for keyword in medium_risk_keywords:
            if keyword in text:
                score += 1
        
        return min(score, 5)

    def _score_tools(self) -> float:
        intent = self.task.intent
        
        if not intent:
            return 2
        
        total_tools = 0
        for i in intent:
            total_tools += self.intent_to_tools.get(i, 2)
        
        if len(intent) >= 2:
            total_tools += 1
        
        if total_tools <= 1:
            return 0
        elif total_tools == 2:
            return 1
        elif total_tools == 3:
            return 2
        elif total_tools == 4:
            return 3
        elif total_tools == 5:
            return 4
        else:
            return 5

    def _score_information(self) -> float:
        text = self.task.cleaned_input
        intent = self.task.intent
        
        score = 0
        
        search_keywords = ["搜索", "查找", "查询", "检索", "寻找"]
        for keyword in search_keywords:
            if keyword in text or keyword in intent:
                score += 2
        
        if len(self.task.entities) >= 3:
            score += 1
        
        if score <= 0:
            return 0
        elif score == 1:
            return 1
        elif score == 2:
            return 2
        elif score == 3:
            return 3
        elif score == 4:
            return 4
        else:
            return 5

    def _score_data_processing(self) -> float:
        text = self.task.cleaned_input
        intent = self.task.intent
        
        score = 0
        
        processing_keywords = ["分析", "统计", "计算", "处理", "提取", "比较"]
        for keyword in processing_keywords:
            if keyword in text or keyword in intent:
                score += 2
        
        file_extensions = [".xlsx", ".csv", ".json", ".xml", ".txt", ".pdf"]
        for ext in file_extensions:
            if ext in text:
                score += 1
        
        if score <= 0:
            return 0
        elif score == 1:
            return 1
        elif score == 2:
            return 2
        elif score == 3:
            return 3
        elif score == 4:
            return 4
        else:
            return 5

    def _score_creativity(self) -> float:
        intent = self.task.intent
        
        creative_intents = ["写", "生成", "创作", "设计", "规划", "推荐"]
        
        score = 0
        
        for i in intent:
            for creative in creative_intents:
                if creative in i:
                    score += 2
        
        if score <= 0:
            return 0
        elif score == 1:
            return 1
        elif score == 2:
            return 2
        elif score == 3:
            return 3
        elif score == 4:
            return 4
        else:
            return 5

    def calculate_complexity_score(self) -> float:
        scores = self.task.seven_dimension_scores
        
        total_score = 0
        for dimension, score in scores.items():
            weight = self.dimension_weights.get(dimension, 1.0)
            total_score += score * weight
        
        heuristic_result = self.task.heuristic_result
        if heuristic_result and heuristic_result["rule"] == "high_risk":
            total_score += 10
        
        self.task.complexity_score = total_score
        return total_score

    def determine_complexity_level(self) -> str:
        score = self.task.complexity_score
        heuristic_result = self.task.heuristic_result
        
        if heuristic_result:
            if heuristic_result["rule"] == "ambiguity":
                self.task.complexity_level = "ambiguous"
                return "ambiguous"
            elif heuristic_result["rule"] == "simple_task":
                self.task.complexity_level = "simple"
                return "simple"
        
        if score <= 10:
            self.task.complexity_level = "simple"
            return "simple"
        elif score <= 30:
            self.task.complexity_level = "medium"
            return "medium"
        else:
            self.task.complexity_level = "complex"
            return "complex"

    def determine_execution_strategy(self) -> str:
        level = self.task.complexity_level
        
        if level == "ambiguous":
            self.task.execution_strategy = "macro"
            return "macro"
        elif level == "simple":
            self.task.execution_strategy = "micro"
            return "micro"
        elif level == "medium":
            self.task.execution_strategy = "meso"
            return "meso"
        elif level == "complex":
            self.task.execution_strategy = "meso_advanced"
            return "meso_advanced"
        else:
            self.task.execution_strategy = "unknown"
            return "unknown"

    def judge(self, text: str) -> StructuredTask:
        self.task = StructuredTask()
        
        self.preprocess(text)
        
        heuristic_result = self.check_heuristic_rules()
        
        if heuristic_result["rule"] == "simple_task":
            self.determine_complexity_level()
            self.determine_execution_strategy()
            return self.task
        
        if heuristic_result["rule"] == "ambiguity":
            self.determine_complexity_level()
            self.determine_execution_strategy()
            return self.task
        
        self.calculate_seven_dimension_scores()
        self.calculate_complexity_score()
        
        self.determine_complexity_level()
        self.determine_execution_strategy()
        
        return self.task