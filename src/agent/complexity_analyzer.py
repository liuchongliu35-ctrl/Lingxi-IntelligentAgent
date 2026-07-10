from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.agent.analyzer_config import AnalyzerConfig, load_analyzer_config
from src.agent.intent_classifier import IntentClassifier, IntentPrediction
from src.agent.uncertainty_detector import UncertaintyDetector


@dataclass
class IntentScore:
    name: str
    score: float
    source: str = "rule"
    matched_keywords: List[str] = field(default_factory=list)


@dataclass
class FileInfo:
    file_path: str | None = None
    file_type: str | None = None
    operation_type: str | None = None
    supported: bool = False


@dataclass
class AnalysisResult:
    raw_input: str
    cleaned_input: str
    mode: str = "solo"
    mode_source: str = "config"
    task_type: str = "qa"
    intents: List[Dict[str, Any]] = field(default_factory=list)
    intent_sequence: List[str] = field(default_factory=list)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    missing_parameters: List[str] = field(default_factory=list)
    clarification_questions: List[str] = field(default_factory=list)
    file_info: Dict[str, Any] = field(default_factory=dict)
    edit_mode: str | None = None
    project_stage: str | None = None
    tech_stacks: List[str] = field(default_factory=list)
    risk_level: str = "low"
    risk_flags: List[str] = field(default_factory=list)
    action_policy: str = "allow"
    requires_confirmation: bool = False
    confirmation_reason: str | None = None
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    complexity_score: float = 0.0
    complexity_level: str = "simple"
    execution_strategy: str = "micro"
    recommended_tools: List[str] = field(default_factory=list)
    available_tools: List[str] = field(default_factory=list)
    missing_tools: List[str] = field(default_factory=list)
    tool_strategy: str = "model_only"
    confidence_score: float = 0.0
    confidence_level: str = "low"
    raw_analysis_trace: List[str] = field(default_factory=list)
    user_facing_summary: str = ""

    # Compatibility fields for current Planner/Executor.
    intent: List[str] = field(default_factory=list)
    original_input: str = ""
    intent_source: str = "rule"
    classifier_confidence: float | None = None
    uncertainty_score: float | None = None
    uncertainty_reason: str | None = None
    requires_clarification: bool = False
    ambiguity_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


StructuredTask = AnalysisResult


class ComplexityAnalyzer:
    """Analyzer V1 baseline: config-driven rules plus classifier-compatible fallback."""

    MULTI_INTENT_MARKERS = ["并", "并且", "然后", "同时", "以及", "再", "接着", "先", "再"]
    AMBIGUOUS_KEYWORDS = ["分析一下", "看看", "弄一下", "搞一下", "随便", "帮我处理", "写个东西"]
    CHAT_MARKERS = ["只告诉", "不要执行", "不用执行", "告诉我怎么", "给我步骤", "解释一下", "怎么做"]
    SOLO_MARKERS = ["直接帮我", "帮我完成", "替我", "你来执行", "直接完成", "帮我做"]
    PROJECT_STAGE_KEYWORDS = {
        "design": ["设计", "架构", "方案", "规划", "design"],
        "develop": ["开发", "实现", "编码", "新增", "develop"],
        "test": ["测试", "单元测试", "跑测试", "test"],
        "debug": ["调试", "报错", "修复", "debug", "bug"],
        "deploy": ["部署", "上线", "发布", "deploy"],
        "document": ["文档", "说明", "readme", "document"],
    }

    def __init__(
        self,
        model_manager: Any | None = None,
        intent_classifier: IntentClassifier | None = None,
        uncertainty_detector: UncertaintyDetector | None = None,
        analyzer_config: AnalyzerConfig | None = None,
        tool_manager: Any | None = None,
    ):
        self.model_manager = model_manager
        self.intent_classifier = intent_classifier or IntentClassifier()
        self.uncertainty_detector = uncertainty_detector or UncertaintyDetector()
        self.config = analyzer_config or load_analyzer_config()
        self.tool_manager = tool_manager

    def analyze(self, user_input: str) -> AnalysisResult:
        cleaned = self._clean(user_input)
        result = AnalysisResult(
            raw_input=user_input,
            original_input=user_input,
            cleaned_input=cleaned,
        )
        result.mode, result.mode_source = self._detect_mode(cleaned)
        result.raw_analysis_trace.append(f"mode={result.mode} source={result.mode_source}")

        intent_scores = self._detect_intents(cleaned, result)
        result.intents = [asdict(item) for item in intent_scores]
        result.intent_sequence = [item.name for item in intent_scores]
        result.intent = list(result.intent_sequence)
        result.intent_source = intent_scores[0].source if intent_scores else result.intent_source

        result.parameters = self._extract_parameters(cleaned)
        result.file_info = asdict(self._extract_file_info(cleaned, result.intent_sequence))
        if result.file_info.get("file_path"):
            result.parameters["file"] = result.file_info["file_path"]
            result.parameters["file_path"] = result.file_info["file_path"]
            result.parameters["file_type"] = result.file_info.get("file_type")
        result.edit_mode = self._detect_edit_mode(cleaned, result.intent_sequence)
        result.entities = self._extract_entities(result.parameters)
        result.missing_parameters = self._detect_missing_parameters(result.intent_sequence, result.parameters)
        result.clarification_questions = self._build_clarification_questions(result.missing_parameters, result.intent_sequence)
        result.requires_clarification = bool(result.clarification_questions)

        result.task_type = self._detect_task_type(result.intent_sequence, result.file_info, cleaned)
        result.project_stage = self._detect_project_stage(cleaned)
        result.tech_stacks = self._detect_tech_stacks(cleaned)
        self._apply_risk_policy(result, cleaned)
        self._evaluate_tools(result)
        self._score_complexity(result, cleaned)
        self._score_confidence(result, intent_scores)
        self._finalize_user_summary(result)
        self._write_log(result)
        return result

    def _clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def _detect_mode(self, text: str) -> tuple[str, str]:
        default_mode = os.getenv("AGENT_MODE", self.config.agent_mode).strip().lower() or "solo"
        markers: List[tuple[int, str]] = []
        for marker in self.CHAT_MARKERS:
            index = text.find(marker)
            if index >= 0:
                markers.append((index, "chat"))
        for marker in self.SOLO_MARKERS:
            index = text.find(marker)
            if index >= 0:
                markers.append((index, "solo"))
        if markers:
            markers.sort(key=lambda item: item[0])
            return markers[-1][1], "input_override"
        return default_mode if default_mode in {"solo", "chat"} else "solo", "config"

    def _detect_intents(self, text: str, result: AnalysisResult) -> List[IntentScore]:
        scored: Dict[str, IntentScore] = {}
        for intent, keywords in self.config.intent_keywords.items():
            matches = [keyword for keyword in keywords if self._keyword_matches(text, keyword, intent)]
            if matches:
                score = min(100.0, 55.0 + len(matches) * 12.0)
                scored[intent] = IntentScore(intent, score, "rule", matches)

        if not scored:
            prediction = self._classifier_predict(text)
            result.intent_source = prediction.source
            result.classifier_confidence = max(prediction.probabilities.values(), default=None)
            if prediction.not_ready:
                result.uncertainty_score = 1.0
                result.uncertainty_reason = "classifier_not_ready"
                result.raw_analysis_trace.append("classifier not ready; using chat fallback")
            elif prediction.probabilities:
                uncertainty = self.uncertainty_detector.detect(prediction.probabilities)
                result.uncertainty_score = uncertainty.score
                result.uncertainty_reason = uncertainty.reason
                if not uncertainty.uncertain:
                    for intent in prediction.intents:
                        probability = prediction.probabilities.get(intent, 0.0)
                        scored[intent] = IntentScore(intent, probability * 100, prediction.source, [])

        if not scored:
            scored["chat"] = IntentScore("chat", 50.0, "fallback", [])

        threshold = self.config.intent_score_threshold
        ordered = [item for item in scored.values() if item.score >= threshold]
        ordered.sort(key=lambda item: item.score, reverse=True)
        truncated = ordered[: self.config.max_intents]
        if len(ordered) > len(truncated):
            result.raw_analysis_trace.append("intent list truncated to max_intents")
        result.raw_analysis_trace.append("intents=" + ",".join(item.name for item in truncated))
        return truncated

    def _keyword_matches(self, text: str, keyword: str, intent: str) -> bool:
        lowered = text.lower()
        key = keyword.lower()
        if intent == "calculate" and key in {"+", "-", "*", "/", "x", "×", "加", "减", "乘", "除"}:
            return bool(re.search(r"\d+(?:\.\d+)?\s*(?:[+\-*/×x]|加|减|乘|除)\s*\d+(?:\.\d+)?", lowered))
        if intent == "calculate" and key in {"+", "-", "*", "/", "x", "×"}:
            return bool(re.search(r"\d+(?:\.\d+)?\s*(?:[+\-*/×x])\s*\d+(?:\.\d+)?", lowered))
        if key == "report" and re.search(r"\breport\.(?:txt|md|pdf|docx|xlsx|csv|json)\b", lowered):
            return False
        if re.fullmatch(r"[a-z0-9_ .+/#-]+", key):
            return bool(re.search(rf"(?<![a-z0-9_]){re.escape(key)}(?![a-z0-9_])", lowered))
        return key in lowered
    def _classifier_predict(self, text: str) -> IntentPrediction:
        tokens = list(text.lower())
        if self._has_multi_marker(text):
            return self.intent_classifier.predict_multi(text, tokens)
        return self.intent_classifier.predict_single(text, tokens)

    def _has_multi_marker(self, text: str) -> bool:
        return any(marker in text for marker in self.MULTI_INTENT_MARKERS)

    def _extract_parameters(self, text: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if numbers:
            params["numbers"] = [float(n) if "." in n else int(n) for n in numbers]
            expression = re.search(r"[\d\s+\-*/().×x]+", text)
            if expression:
                params["expression"] = expression.group(0).replace("x", "*").replace("×", "*").strip()
        count_match = re.search(r"(\d+)\s*(?:个|篇|份|条|次)", text)
        if count_match:
            params["count"] = int(count_match.group(1))
        if "markdown" in text.lower() or "md" in text.lower():
            params["output_format"] = "md"
        elif "json" in text.lower():
            params["output_format"] = "json"
        return params

    def _extract_file_info(self, text: str, intents: List[str]) -> FileInfo:
        extensions = "|".join(re.escape(ext) for ext in self.config.supported_file_types) or "txt|md|pdf|docx|xlsx|csv|json"
        match = re.search(rf"[^\s'\"，。；;]+\.(?:{extensions})", text, re.I)
        file_path = match.group(0) if match else None
        file_type = Path(file_path).suffix.lower().lstrip(".") if file_path else None
        operation_type = None
        if "delete_file" in intents:
            operation_type = "delete"
        elif "move_file" in intents:
            operation_type = "move"
        elif "copy_file" in intents:
            operation_type = "copy"
        elif "rename_file" in intents:
            operation_type = "rename"
        elif "write_file" in intents:
            operation_type = "write"
        elif "read_file" in intents:
            operation_type = "read"
        return FileInfo(
            file_path=file_path,
            file_type=file_type,
            operation_type=operation_type,
            supported=bool(file_type and file_type in self.config.supported_file_types),
        )

    def _detect_edit_mode(self, text: str, intents: List[str]) -> str | None:
        if not any(intent in intents for intent in ["write_file", "write", "debug_code"]):
            return None
        if any(keyword in text for keyword in ["局部", "部分", "修改", "添加", "删除这一段", "函数", "patch"]):
            return "partial_edit"
        if any(keyword in text for keyword in ["覆盖", "重写", "全部替换", "整文件"]):
            return "full_overwrite"
        return "partial_edit" if "debug_code" in intents else "full_overwrite"

    def _extract_entities(self, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        for value in parameters.get("numbers", []):
            entities.append({"type": "number", "value": value})
        if parameters.get("file_path"):
            entities.append({"type": "file", "value": parameters["file_path"], "file_type": parameters.get("file_type")})
        return entities

    def _detect_missing_parameters(self, intents: List[str], parameters: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if "calculate" in intents and not parameters.get("expression"):
            missing.append("expression")
        if any(intent in intents for intent in ["read_file", "write_file", "delete_file", "move_file", "copy_file", "rename_file"]):
            if not parameters.get("file_path"):
                missing.append("file_path")
        if "translate" in intents and "target_language" not in parameters:
            missing.append("target_language")
        if "search" in intents and len(parameters.get("numbers", [])) == 0 and len(self._clean(parameters.get("topic", ""))) == 0:
            # Keep search usable when the text itself is the topic; no missing field by default.
            pass
        return list(dict.fromkeys(missing))

    def _build_clarification_questions(self, missing: List[str], intents: List[str]) -> List[str]:
        questions: List[str] = []
        for field_name in missing:
            if field_name == "file_path":
                questions.append("请补充要处理的文件路径。")
            elif field_name == "expression":
                questions.append("请补充要计算的完整表达式。")
            elif field_name == "target_language":
                questions.append("请说明要翻译成哪种语言。")
            else:
                questions.append(f"请补充 {field_name}。")
        if not intents or intents == ["chat"]:
            questions.append("请补充你希望我完成的具体目标或输出形式。")
        return questions

    def _detect_task_type(self, intents: List[str], file_info: Dict[str, Any], text: str) -> str:
        if any(intent in intents for intent in ["delete_file", "move_file", "copy_file", "rename_file", "write_file", "list_files", "find_files"]):
            return "file_operation"
        if any(intent in intents for intent in ["read_file", "summarize", "extract"]) and file_info.get("file_path"):
            return "document_understanding"
        if any(intent in intents for intent in ["create_project", "design_project", "debug_code", "run_test", "deploy_project"]):
            return "software_engineering"
        if any(keyword in text.lower() for keyword in ["excel", "csv", "xlsx", "数据", "分析数据", "pandas"]):
            return "data_analysis"
        if any(intent in intents for intent in ["write", "generate_report"]):
            return "content_generation"
        if "plan" in intents:
            return "project_management"
        if intents == ["chat"]:
            return "chat"
        return "qa"

    def _detect_project_stage(self, text: str) -> str | None:
        lowered = text.lower()
        for stage, keywords in self.PROJECT_STAGE_KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                return stage
        return None

    def _detect_tech_stacks(self, text: str) -> List[str]:
        lowered = text.lower()
        matches: List[str] = []
        for family, keywords in self.config.tech_stacks.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                matches.append(family)
        return matches

    def _apply_risk_policy(self, result: AnalysisResult, text: str) -> None:
        lowered = text.lower()
        for risk_name, keywords in self.config.risk_rules.get("domain_risks", {}).items():
            if any(keyword.lower() in lowered for keyword in keywords):
                result.risk_flags.append(risk_name)
        for keyword in self.config.risk_rules.get("block_keywords", []):
            if keyword.lower() in lowered:
                result.risk_flags.append("dangerous_command")
                result.action_policy = "block"
        file_path = result.file_info.get("file_path") if result.file_info else None
        if file_path:
            normalized = file_path.replace("/", "\\").lower()
            for sensitive in self.config.risk_rules.get("sensitive_paths", []):
                if normalized.startswith(str(sensitive).replace("/", "\\").lower()):
                    result.risk_flags.append("sensitive_path")
                    result.action_policy = "block"
        confirm_intents = set(self.config.risk_rules.get("confirm_intents", []))
        confirm_matches = [intent for intent in result.intent_sequence if intent in confirm_intents]
        if confirm_matches and result.action_policy != "block" and result.mode == "solo":
            result.action_policy = "confirm"
            result.requires_confirmation = True
            result.confirmation_reason = confirm_matches[0]
        if result.action_policy == "block":
            result.requires_confirmation = False
            result.confirmation_reason = "blocked_risk"
        if any(flag in result.risk_flags for flag in ["dangerous_command", "sensitive_path"]):
            result.risk_level = "high"
        elif result.action_policy == "confirm" or result.risk_flags:
            result.risk_level = "medium"
        else:
            result.risk_level = "low"

    def _evaluate_tools(self, result: AnalysisResult) -> None:
        recommended: List[str] = []
        for intent in result.intent_sequence:
            recommended.extend(self.config.tool_mapping.get(intent, []))
        result.recommended_tools = list(dict.fromkeys(recommended))
        if not result.recommended_tools:
            result.tool_strategy = "model_only"
            return
        available_names = self._available_tool_names()
        result.available_tools = [tool for tool in result.recommended_tools if tool in available_names]
        result.missing_tools = [tool for tool in result.recommended_tools if tool not in available_names]
        if result.missing_tools:
            result.tool_strategy = "blocked_missing_tools"
        elif result.available_tools:
            result.tool_strategy = "tool"
        else:
            result.tool_strategy = "model_only"

    def _available_tool_names(self) -> set[str]:
        if self.tool_manager is not None and hasattr(self.tool_manager, "list_tools"):
            return set(self.tool_manager.list_tools().keys())
        return {
            "document_parser",
            "text_processor",
            "math_calculator",
            "translator",
            "time_query",
            "search_tool",
            "code_executor",
            "file_writer",
        }

    def _score_complexity(self, result: AnalysisResult, text: str) -> None:
        weights = self.config.complexity.get("weights", {})
        dimension_raw = {
            "uncertainty": self._score_uncertainty(result, text),
            "steps": self._score_steps(result),
            "domain_risk": self._score_domain_risk(result),
            "tools": self._score_tools(result),
            "information": self._score_information(result),
            "data_processing": self._score_data_processing(result, text),
            "creativity": self._score_creativity(result),
        }
        result.dimension_scores = dimension_raw
        score = sum(dimension_raw[name] * float(weights.get(name, 1.0)) for name in dimension_raw)
        if result.risk_flags:
            score += float(self.config.complexity.get("risk_bonus", 10)) if result.risk_level == "high" else 0.0
        result.complexity_score = round(score, 2)
        if result.requires_clarification or any(keyword in text for keyword in self.AMBIGUOUS_KEYWORDS):
            result.complexity_level = "ambiguous"
            result.execution_strategy = "macro"
            result.ambiguity_flags.append("ambiguous_or_missing_parameters")
            return
        if result.action_policy == "block":
            result.complexity_level = "high_risk"
            result.execution_strategy = "meso_advanced"
            return
        thresholds = self.config.complexity.get("thresholds", {})
        simple_max = float(thresholds.get("simple_max", 10))
        medium_max = float(thresholds.get("medium_max", 30))
        if result.complexity_score <= simple_max and self._is_simple_task(result):
            result.complexity_level = "simple"
            result.execution_strategy = "micro"
        elif result.complexity_score <= medium_max:
            result.complexity_level = "medium"
            result.execution_strategy = "meso"
        else:
            result.complexity_level = "complex"
            result.execution_strategy = "meso_advanced"

    def _score_uncertainty(self, result: AnalysisResult, text: str) -> float:
        if result.requires_clarification or any(keyword in text for keyword in self.AMBIGUOUS_KEYWORDS):
            return 4.0
        if result.intent_source in {"fallback", "classifier_stub"}:
            return 3.0
        return 1.0

    def _score_steps(self, result: AnalysisResult) -> float:
        if len(result.intent_sequence) >= 4:
            return 4.0
        if len(result.intent_sequence) >= 2:
            return 3.0
        return 1.0

    def _score_domain_risk(self, result: AnalysisResult) -> float:
        if result.action_policy == "block":
            return 5.0
        if result.risk_flags:
            return 3.0
        return 0.0

    def _score_tools(self, result: AnalysisResult) -> float:
        if len(result.recommended_tools) >= 5:
            return 5.0
        if len(result.recommended_tools) >= 3:
            return 4.0
        if result.recommended_tools:
            return 2.0
        return 0.0

    def _score_information(self, result: AnalysisResult) -> float:
        return 3.0 if "search" in result.intent_sequence else 1.0 if result.file_info.get("file_path") else 0.0

    def _score_data_processing(self, result: AnalysisResult, text: str) -> float:
        if any(keyword in text.lower() for keyword in ["xlsx", "csv", "json", "excel", "数据", "统计"]):
            return 3.0
        if any(intent in result.intent_sequence for intent in ["extract", "summarize", "compare"]):
            return 2.0
        return 0.0

    def _score_creativity(self, result: AnalysisResult) -> float:
        if any(intent in result.intent_sequence for intent in ["write", "generate_report", "design_project", "recommend"]):
            return 3.0
        return 0.0

    def _is_simple_task(self, result: AnalysisResult) -> bool:
        if len(result.intent_sequence) != 1 or result.requires_clarification:
            return False
        return result.intent_sequence[0] in {"calculate", "read_file", "translate", "chat"}

    def _score_confidence(self, result: AnalysisResult, intent_scores: List[IntentScore]) -> None:
        if not intent_scores:
            result.confidence_score = 0.0
        else:
            result.confidence_score = round(max(item.score for item in intent_scores) / 100.0, 2)
        thresholds = self.config.analyzer_config.get("confidence_thresholds", {})
        if result.confidence_score >= float(thresholds.get("high", 0.85)):
            result.confidence_level = "high"
        elif result.confidence_score >= float(thresholds.get("medium", 0.6)):
            result.confidence_level = "medium"
        else:
            result.confidence_level = "low"

    def _finalize_user_summary(self, result: AnalysisResult) -> None:
        if result.action_policy == "block":
            result.user_facing_summary = "这个请求包含高风险操作，当前不会执行。"
        elif result.requires_confirmation:
            result.user_facing_summary = "这个请求需要你确认后才能继续执行。"
        elif result.requires_clarification:
            result.user_facing_summary = "这个请求还缺少必要信息，需要先补充后再继续。"
        else:
            result.user_facing_summary = "已理解任务，可以继续规划和执行。"

    def _write_log(self, result: AnalysisResult) -> None:
        path = self.config.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "raw_input": result.raw_input,
            "cleaned_input": result.cleaned_input,
            "mode": result.mode,
            "mode_source": result.mode_source,
            "intents": result.intents,
            "parameters": result.parameters,
            "missing_parameters": result.missing_parameters,
            "clarification_questions": result.clarification_questions,
            "dimension_scores": result.dimension_scores,
            "complexity_score": result.complexity_score,
            "complexity_level": result.complexity_level,
            "execution_strategy": result.execution_strategy,
            "risk_flags": result.risk_flags,
            "action_policy": result.action_policy,
            "recommended_tools": result.recommended_tools,
            "available_tools": result.available_tools,
            "missing_tools": result.missing_tools,
            "raw_analysis_trace": result.raw_analysis_trace,
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")



