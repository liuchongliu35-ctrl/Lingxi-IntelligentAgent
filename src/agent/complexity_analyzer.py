from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.agent.intent_classifier import IntentClassifier
from src.agent.uncertainty_detector import UncertaintyDetector


@dataclass
class StructuredTask:
    original_input: str
    cleaned_input: str
    intent: List[str] = field(default_factory=list)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    complexity_score: float = 0.0
    complexity_level: str = "unknown"
    execution_strategy: str = "unknown"
    risk_flags: List[str] = field(default_factory=list)
    ambiguity_flags: List[str] = field(default_factory=list)
    intent_source: str = "rule"
    classifier_confidence: float | None = None
    uncertainty_score: float | None = None
    uncertainty_reason: str | None = None
    requires_clarification: bool = False


class ComplexityAnalyzer:
    """Rule-first analyzer used as the stable baseline for later classifier work."""

    INTENT_KEYWORDS = {
        "calculate": ["计算", "求和", "加", "减", "乘", "除", "+", "-", "*", "/", "x", "×"],
        "search": ["搜索", "查找", "查询", "检索", "找"],
        "summarize": ["总结", "概括", "摘要", "归纳"],
        "analyze": ["分析", "评估", "解读", "对比"],
        "write": ["写", "生成", "创建", "起草", "撰写"],
        "translate": ["翻译", "译成", "翻成"],
        "plan": ["规划", "计划", "安排", "方案"],
        "read": ["读取", "打开", "查看"],
        "execute": ["执行", "运行", "调试"],
        "recommend": ["推荐", "建议"],
        "extract": ["提取", "抽取", "摘录"],
    }

    MULTI_INTENT_MARKERS = ["并", "并且", "然后", "同时", "以及", "再", "接着"]
    AMBIGUOUS_KEYWORDS = ["分析一下", "看看", "弄一下", "搞一下", "随便", "帮我处理"]
    HIGH_RISK_KEYWORDS = {
        "legal": ["合同", "协议", "诉讼", "律师函", "遗嘱", "赔偿"],
        "medical": ["诊断", "治疗", "处方", "用药", "症状", "疾病"],
        "finance": ["投资", "股票", "基金", "贷款", "保险", "理财"],
        "privacy": ["身份证", "密码", "银行卡", "手机号", "验证码", "隐私"],
    }

    def __init__(
        self,
        model_manager: Any | None = None,
        intent_classifier: IntentClassifier | None = None,
        uncertainty_detector: UncertaintyDetector | None = None,
    ):
        self.model_manager = model_manager
        self.intent_classifier = intent_classifier or IntentClassifier()
        self.uncertainty_detector = uncertainty_detector or UncertaintyDetector()

    def analyze(self, user_input: str) -> StructuredTask:
        cleaned = self._clean(user_input)
        task = StructuredTask(original_input=user_input, cleaned_input=cleaned)
        task.intent = self._detect_intents(cleaned, task)
        task.parameters = self._extract_parameters(cleaned)
        task.entities = self._extract_entities(cleaned, task.parameters)
        self._apply_heuristics(task)

        if task.complexity_level == "unknown":
            task.complexity_score = self._score(task)
            task.complexity_level = self._level_from_score(task.complexity_score)
            task.execution_strategy = self._strategy_from_level(task.complexity_level)

        return task

    def _clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def _detect_intents(self, text: str, task: StructuredTask) -> List[str]:
        intents: List[str] = []
        lowered = text.lower()
        for intent, keywords in self.INTENT_KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                intents.append(intent)
        if intents:
            task.intent_source = "rule"
            return intents

        tokens = list(lowered)
        prediction = self.intent_classifier.predict_multi(text, tokens) if self._has_multi_marker(text) else self.intent_classifier.predict_single(text, tokens)
        if prediction.not_ready:
            task.intent_source = prediction.source
            task.uncertainty_score = 1.0
            task.uncertainty_reason = "classifier_not_ready"
            return []

        uncertainty = self.uncertainty_detector.detect(prediction.probabilities)
        task.intent_source = prediction.source
        task.classifier_confidence = max(prediction.probabilities.values(), default=None)
        task.uncertainty_score = uncertainty.score
        task.uncertainty_reason = uncertainty.reason
        if uncertainty.uncertain:
            return []
        return prediction.intents

    def _extract_parameters(self, text: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if numbers:
            params["numbers"] = [float(n) if "." in n else int(n) for n in numbers]
            expression = re.search(r"[\d\s+\-*/().×x脳]+", text)
            if expression:
                params["expression"] = expression.group(0).replace("x", "*").replace("×", "*").replace("脳", "*").strip()

        file_match = re.search(r"[\w./\\:-]+\.(?:txt|md|pdf|csv|json|xlsx|xls|py)", text, re.I)
        if file_match:
            params["file"] = file_match.group(0)

        return params

    def _has_multi_marker(self, text: str) -> bool:
        return any(marker in text for marker in self.MULTI_INTENT_MARKERS)

    def _extract_entities(self, text: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        for value in parameters.get("numbers", []):
            entities.append({"type": "number", "value": value})
        if "file" in parameters:
            entities.append({"type": "file", "value": parameters["file"]})
        return entities

    def _apply_heuristics(self, task: StructuredTask) -> None:
        text = task.cleaned_input

        if (len(text) < 4 and not task.intent) or any(k in text for k in self.AMBIGUOUS_KEYWORDS):
            task.complexity_level = "ambiguous"
            task.execution_strategy = "macro"
            task.requires_clarification = True
            task.ambiguity_flags.append("ambiguous_input")
            return

        for risk_type, keywords in self.HIGH_RISK_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                task.risk_flags.append(risk_type)

        if task.risk_flags:
            task.complexity_score += 10

        if self._is_simple_task(task):
            task.complexity_level = "simple"
            task.execution_strategy = "micro"
            return

    def _is_simple_task(self, task: StructuredTask) -> bool:
        if len(task.intent) != 1:
            return False
        if task.intent[0] in {"calculate", "read"}:
            return True
        if task.intent[0] == "translate" and len(task.cleaned_input) < 120:
            return True
        return False

    def _score(self, task: StructuredTask) -> float:
        score = task.complexity_score
        score += 3 if not task.intent else max(len(task.intent) - 1, 0) * 3
        score += 6 if any(marker in task.cleaned_input for marker in self.MULTI_INTENT_MARKERS) else 0
        score += 4 if task.intent and task.intent[0] in {"analyze", "plan", "write"} else 0
        score += 5 if task.intent and task.intent[0] in {"search", "extract"} else 0
        score += 2 if len(task.parameters) >= 2 else 0
        return score

    def _level_from_score(self, score: float) -> str:
        if score <= 10:
            return "simple"
        if score <= 30:
            return "medium"
        return "complex"

    def _strategy_from_level(self, level: str) -> str:
        return {
            "ambiguous": "macro",
            "simple": "micro",
            "medium": "meso",
            "complex": "meso_advanced",
            "high_risk": "meso_advanced",
        }.get(level, "unknown")
