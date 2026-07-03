# todo 这个代码只需要实现步骤一的逻辑，包括前置筛选器，实体提取器，意图提取器，
# 得到的结果最后由StructuredTask这个来组装结构化任务对象
from typing import Tuple, List, Dict, Optional, Any
import os
import json
import pickle
import math
# todo 复意图筛选器
class MultiIntentFilter:
    def __init__(self):
        self.multi_intent_single_words = [
            "并", "并且", "和", "与", "及", "以及", "还有", "同时",
            "然后", "接着", "随后", "之后", "继而", "再", "跟着",
            "或", "或者",
            "但是", "然而", "却", "不过", "可是",
            "因此", "因而", "从而", "以致",
            "为了", "以便", "以免"
        ]
        
        self.intent_keywords = {
            "搜索": ["搜索", "查找", "查询", "搜一下", "找一下", "检索", "寻找", "百度一下"],
            "总结": ["总结", "概括", "归纳", "提炼", "摘要", "要点", "简而言之", "概括一下", "精简"],
            "分析": ["分析", "研究", "评估", "解读", "剖析", "洞察", "对比分析", "诊断"],
            "计算": ["计算", "求和", "加", "减", "乘", "除", "统计", "合计", "总计", "平均", "算一下", "总共多少", "数值运算"],
            "写": ["写", "生成", "创建", "编写", "撰写", "起草", "制作", "生成一段"],
            "翻译": ["翻译", "译成", "转换语言", "翻译成", "译为"],
            "规划": ["规划", "安排", "计划", "步骤", "制定方案", "路线", "行程", "方案", "攻略"],
            "读取": ["读取", "打开", "查看", "加载", "获取", "读入"],
            "执行": ["执行", "运行", "调试", "启动", "触发"],
            "推荐": ["推荐", "建议", "给我推荐", "有什么好", "推荐一下", "安利"],
            "比较": ["对比", "比较", "哪个好", "差异", "区别", "孰优孰劣"],
            "解释": ["解释", "说明", "为什么", "怎么回事", "原因", "原理", "定义"],
            "提取": ["提取", "抽取", "摘录", "抓取", "取出", "截取"],
        }
        
        self.intent_list = list(self.intent_keywords.keys())
        
        self.model = None
        self.tokenizer = None
        self.label_encoder = None
        self.config = None
        
        self._load_model()
    
    def _load_model(self):
        model_path = os.path.join(os.path.dirname(__file__), '../model_trains/saved_models/bert')
        
        if not os.path.exists(model_path):
            print(f"警告：模型目录不存在 {model_path}，将使用规则匹配作为备选")
            return
        
        try:
            with open(os.path.join(model_path, 'config.json'), 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            
            with open(os.path.join(model_path, 'label_encoder.pkl'), 'rb') as f:
                self.label_encoder = pickle.load(f)
            
            from transformers import BertTokenizer, BertForSequenceClassification
            self.tokenizer = BertTokenizer.from_pretrained(model_path)
            self.model = BertForSequenceClassification.from_pretrained(model_path)
            self.model.eval()
            
            print("意图识别模型加载成功")
            
        except Exception as e:
            print(f"模型加载失败: {e}")
            self.model = None
            self.tokenizer = None
            self.label_encoder = None
            self.config = None
    
    def has_multi_intent_feature(self, tokens: List[str])  -> Tuple[bool, List[str]]:
        hit_keywords = []
        
        for token in tokens:
            if token in self.multi_intent_single_words:
                hit_keywords.append(token)
        
        for i in range(len(tokens) - 1):
            two_words = tokens[i] + tokens[i+1]
            if two_words in ["先再", "首先然后"]:
                hit_keywords.append(two_words)
        
        return len(hit_keywords) > 0, hit_keywords
    
    def get_intent_by_word(self, word: str) -> Optional[str]:
        for intent, keywords in self.intent_keywords.items():
            if word in keywords:
                return intent
        return None
    
    def has_multi_intent(self, tokens: List[str]) -> Tuple[bool, List[str]]:
        has_multi, hit_keywords = self.has_multi_intent_feature(tokens)
        return has_multi, hit_keywords
    
    def rule_matching_by_keyword(self, tokens: List[str]) -> List[str]:
        matched_intents = []
        for token in tokens:
            intent = self.get_intent_by_word(token)
            if intent and intent not in matched_intents:
                matched_intents.append(intent)
        return matched_intents
    
    def _calculate_entropy(self, probabilities: List[float]) -> float:
        entropy = 0.0
        for p in probabilities:
            if p > 0:
                entropy -= p * math.log(p, 2)
        return entropy
    
    def extract_intent(self, tokens: List[str]) -> Dict[str, Any]:
        has_multi, hit_keywords = self.has_multi_intent_feature(tokens)
        
        if has_multi:
            result = self._extract_multi_intent(tokens)
        else:
            result = self._extract_single_intent(tokens)
        
        result["hit_keywords"] = hit_keywords
        return result
    
    def _extract_multi_intent(self, tokens: List[str]) -> Dict[str, Any]:
        rule_matched = self.rule_matching_by_keyword(tokens)
        
        if len(rule_matched) >= 2:
            return {
                "intents": rule_matched,
                "method": "rule_matching",
                "confidence": "high",
                "needs_llm_fallback": False
            }
        
        if len(rule_matched) == 1:
            cls_intents = self.light_weight_multi_intent_classification(tokens)
            combined = rule_matched.copy()
            for intent in cls_intents:
                if intent not in combined:
                    combined.append(intent)
            
            if len(combined) >= 2:
                return {
                    "intents": combined,
                    "method": "rule_matching + classifier",
                    "confidence": "medium",
                    "needs_llm_fallback": False
                }
            
            if len(combined) == 1:
                return self._check_uncertainty_for_single(tokens, combined[0])
        
        cls_intents = self.light_weight_multi_intent_classification(tokens)
        
        if not cls_intents:
            return self.llm_fallback(tokens)
        
        if len(cls_intents) >= 2:
            return {
                "intents": cls_intents,
                "method": "multi_intent_classifier",
                "confidence": "medium",
                "needs_llm_fallback": False
            }
        
        if len(cls_intents) == 1:
            return self._check_uncertainty_for_single(tokens, cls_intents[0])
        
        return self.llm_fallback(tokens)
    
    def _extract_single_intent(self, tokens: List[str]) -> Dict[str, Any]:
        rule_matched = self.rule_matching_by_keyword(tokens)
        
        if rule_matched:
            return {
                "intents": rule_matched,
                "method": "rule_matching",
                "confidence": "high",
                "needs_llm_fallback": False
            }
        
        classification_result = self.light_weight_one_intent_classification(tokens)
        
        if not classification_result:
            return self.llm_fallback(tokens)
        
        reliability = self.check_classification_reliability(classification_result)
        
        if reliability == "high":
            return {
                "intents": classification_result["intents"],
                "method": "single_intent_classifier",
                "confidence": "high",
                "needs_llm_fallback": False
            }
        
        if reliability == "medium":
            return {
                "intents": classification_result["intents"],
                "method": "single_intent_classifier",
                "confidence": "medium",
                "needs_llm_fallback": False
            }
        
        if reliability == "low":
            recheck_result = self._extract_multi_intent(tokens)
            if len(recheck_result.get("intents", [])) >= 2:
                return recheck_result
            return self.llm_fallback(tokens)
        
        if reliability == "unknown":
            return self.user_clarify(tokens)
        
        return self.llm_fallback(tokens)
    
    def _check_uncertainty_for_single(self, tokens: List[str], intent: str) -> Dict[str, Any]:
        if self.model is None or self.tokenizer is None or self.config is None:
            return {
                "intents": [intent],
                "method": "rule_matching",
                "confidence": "medium",
                "needs_llm_fallback": False
            }
        
        try:
            import torch
            
            text = ''.join(tokens)
            encoding = self.tokenizer(
                text,
                padding='max_length',
                truncation=True,
                max_length=self.config.get('max_seq_len', 30),
                return_tensors='pt'
            )
            
            with torch.no_grad():
                outputs = self.model(**encoding)
                probabilities = torch.softmax(outputs.logits, dim=1).cpu().numpy()[0]
            
            max_prob = max(probabilities)
            entropy = self._calculate_entropy(probabilities)
            
            if entropy > 0.7 or max_prob < 0.6:
                return self.llm_fallback(tokens)
            
            return {
                "intents": [intent],
                "method": "classifier_with_uncertainty_check",
                "confidence": "medium",
                "needs_llm_fallback": False
            }
        
        except Exception as e:
            print(f"不确定性判断失败: {e}")
            return {
                "intents": [intent],
                "method": "fallback",
                "confidence": "low",
                "needs_llm_fallback": False
            }
    
    def check_classification_reliability(self, classification_result: Dict[str, Any]) -> str:
        if not classification_result or not classification_result.get("probabilities"):
            return "unknown"
        
        probabilities = classification_result["probabilities"]
        sorted_probs = sorted(probabilities, reverse=True)
        
        if len(sorted_probs) < 2:
            return "unknown"
        
        p1, p2 = sorted_probs[0], sorted_probs[1]
        
        if p1 > 0.85:
            return "high"
        
        if 0.6 < p1 <= 0.85 and (p1 - p2) > 0.2:
            return "medium"
        
        if p1 <= 0.6 or (p1 - p2) <= 0.2:
            return "low"
        
        return "medium"
    
    def light_weight_multi_intent_classification(self, tokens: List[str]) -> List[str]:
        if self.model is None or self.tokenizer is None or self.label_encoder is None or self.config is None:
            return self._fallback_multi_intent_classification(tokens)
        
        try:
            import torch
            
            text = ''.join(tokens)
            
            encoding = self.tokenizer(
                text,
                padding='max_length',
                truncation=True,
                max_length=self.config.get('max_seq_len', 30),
                return_tensors='pt'
            )
            
            with torch.no_grad():
                outputs = self.model(**encoding)
                probabilities = torch.sigmoid(outputs.logits).cpu().numpy()[0]
            
            threshold = 0.5
            intents = []
            
            for idx, prob in enumerate(probabilities):
                if prob > threshold:
                    intent_name = self.label_encoder.inverse_transform([idx])[0]
                    if intent_name in self.intent_keywords:
                        intents.append(intent_name)
            
            max_prob = max(probabilities) if len(probabilities) > 0 else 0
            
            if max_prob < 0.6:
                return []
            
            return intents
            
        except Exception as e:
            print(f"多意图分类失败: {e}")
            return self._fallback_multi_intent_classification(tokens)
    
    def light_weight_one_intent_classification(self, tokens: List[str]) -> Optional[Dict[str, Any]]:
        if self.model is None or self.tokenizer is None or self.label_encoder is None or self.config is None:
            matched = self._fallback_one_intent_classification(tokens)
            if matched:
                return {
                    "intents": matched,
                    "probabilities": [0.8]
                }
            return None
        
        try:
            import torch
            
            text = ''.join(tokens)
            
            encoding = self.tokenizer(
                text,
                padding='max_length',
                truncation=True,
                max_length=self.config.get('max_seq_len', 30),
                return_tensors='pt'
            )
            
            with torch.no_grad():
                outputs = self.model(**encoding)
                probabilities = torch.softmax(outputs.logits, dim=1).cpu().numpy()[0]
            
            sorted_indices = sorted(range(len(probabilities)), key=lambda i: probabilities[i], reverse=True)
            sorted_probs = [probabilities[i] for i in sorted_indices]
            
            intent_name = self.label_encoder.inverse_transform([sorted_indices[0]])[0]
            
            if intent_name not in self.intent_keywords:
                return {
                    "intents": [],
                    "probabilities": sorted_probs,
                    "is_unknown": True
                }
            
            return {
                "intents": [intent_name],
                "probabilities": sorted_probs
            }
            
        except Exception as e:
            print(f"单意图分类失败: {e}")
            matched = self._fallback_one_intent_classification(tokens)
            if matched:
                return {
                    "intents": matched,
                    "probabilities": [0.7]
                }
            return None
    
    def llm_fallback(self, tokens: List[str]) -> Dict[str, Any]:
        text = ''.join(tokens)
        
        print(f"触发大模型兜底: {text}")
        
        return {
            "intents": [],
            "method": "llm_fallback",
            "confidence": "unknown",
            "needs_llm_fallback": True,
            "original_text": text,
            "message": "需要大模型进一步分析"
        }
    
    def user_clarify(self, tokens: List[str]) -> Dict[str, Any]:
        text = ''.join(tokens)
        
        return {
            "intents": [],
            "method": "user_clarify",
            "confidence": "unknown",
            "needs_llm_fallback": False,
            "original_text": text,
            "message": "意图不明确，请用户补充信息"
        }
    
    def _fallback_multi_intent_classification(self, tokens: List[str]) -> List[str]:
        matched = self.rule_matching_by_keyword(tokens)
        
        for token in tokens:
            for intent, keywords in self.intent_keywords.items():
                if intent not in matched:
                    for keyword in keywords:
                        if keyword in token or token in keyword:
                            matched.append(intent)
                            break
        
        return list(set(matched))
    
    def _fallback_one_intent_classification(self, tokens: List[str]) -> List[str]:
        matched = self.rule_matching_by_keyword(tokens)
        
        if len(matched) == 0:
            for intent, keywords in self.intent_keywords.items():
                for keyword in keywords:
                    for token in tokens:
                        if keyword in token or token in keyword:
                            return [intent]
        
        return matched[:1] if matched else []
    
    def rule_matching(self, tokens: List[str], is_multi_intent: bool = False) -> List[str]:
        result = self.extract_intent(tokens)
        return result.get("intents", [])


if __name__ == "__main__":
    import jieba
    
    filter = MultiIntentFilter()
    
    test_cases = [
        "计算1024×768",
        "今天天气怎么样",
        "搜索论文并总结核心贡献",
        "帮我分析一下市场情况",
        "帮我规划下周末的上海三日游",
        "搜索并分析数据"
    ]
    
    for text in test_cases:
        tokens = jieba.lcut(text)
        has_multi, hit_keywords = filter.has_multi_intent(tokens)
        intents = filter.rule_matching(tokens, is_multi_intent=has_multi)
        print(f"指令: {text}")
        print(f"分词结果: {tokens}")
        print(f"是否包含多意图特征: {has_multi}")
        print(f"命中连接词: {hit_keywords}")
        print(f"识别到的意图: {intents}")
        print("-" * 50)