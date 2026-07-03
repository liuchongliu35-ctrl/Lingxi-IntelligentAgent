from typing import List, Dict, Optional, Tuple, Set, Any
import re

# todo 启发式规则判断模块

class HeuristicRules:
    def __init__(self):
        self.ambiguous_keywords = [
            "分析一下", "看看", "帮我弄一下", "搞一下", "做点", "随便",
            "怎么回事", "如何处理",
            "帮忙处理", "处理一下", "弄一下", "搞一下", "看看", "试试",
            "研究一下", "考虑一下", "了解一下", "知道一下", "掌握一下",
            "熟悉一下", "学习一下", "体验一下", "感受一下", "欣赏一下",
            "品味一下", "理解一下", "认识一下", "了解了解", "知道知道",
        ]
        
        self.question_words = ["什么", "怎样", "如何", "为什么"]
        
        self.high_risk_keywords = {
            "法律": ["合同", "协议", "律师函", "诉讼", "遗嘱", "离婚", "继承",
                     "赔偿", "仲裁", "调解", "判决", "裁定", "上诉", "抗诉"],
            "医疗": ["诊断", "治疗", "处方", "用药", "手术", "康复", "体检",
                     "疫苗", "药品", "疾病", "症状", "检查", "化验", "影像"],
            "金融": ["投资", "股票", "基金", "债券", "贷款", "保险", "理财",
                     "收益", "风险", "亏损", "账户", "余额", "转账", "汇款"],
            "隐私": ["身份证", "密码", "银行卡", "手机号", "地址", "姓名",
                     "证件", "验证码", "指纹", "人脸", "签名"]
        }
        
        self.risky_intents = ["写", "生成"]
        
        self.simple_intents = ["计算", "读取", "查询"]
        
        self.simple_keywords = ["计算", "求和", "加", "减", "乘", "除",
                               "打开", "查看", "读取", "查询", "显示", "获取",
                               "现在", "今天", "明天", "昨天", "当前", "最新"]
    
    def _match_keyword_in_list(self, keywords: List[str], target_list: List[str]) -> bool:
        """
        检查目标列表中是否有任意元素包含关键词列表中的任意关键词（子字符串匹配）
        
        Args:
            keywords: 关键词列表
            target_list: 待检查的目标列表
            
        Returns:
            bool: 是否匹配到
        """
        if not keywords or not target_list:
            return False
        
        for target_item in target_list:
            for keyword in keywords:
                if keyword in str(target_item):
                    return True
        
        return False
    
    def _match_keyword_in_text(self, keywords: List[str], text: str) -> bool:
        """
        检查文本中是否包含关键词列表中的任意关键词（子字符串匹配）
        
        Args:
            keywords: 关键词列表
            text: 待检查的文本
            
        Returns:
            bool: 是否匹配到
        """
        if not keywords or not text:
            return False
        
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        
        return False
    
    def _get_all_high_risk_keywords(self) -> List[str]:
        """
        获取所有高风险关键词（扁平化）
        
        Returns:
            List[str]: 所有高风险关键词
        """
        all_keywords = []
        for keywords in self.high_risk_keywords.values():
            all_keywords.extend(keywords)
        return all_keywords
    
    def check_ambiguity(self, text: str, intent: List[str], parameters: Dict[str, Any]) -> Tuple[bool, str]:
        text_lower = text.lower()
        
        if self._match_keyword_in_text(self.ambiguous_keywords, text_lower):
            return True, "ambiguous_keyword"
        
        has_question_word = self._match_keyword_in_text(self.question_words, text_lower)
        if has_question_word and not intent:
            return True, "question_without_intent"
        
        if len(text) < 5 and not intent:
            return True, "too_short"
        
        if not intent and not parameters:
            return True, "no_intent_no_params"
        
        return False, ""
    
    def check_high_risk(self, text: str, intent: List[str]) -> Tuple[bool, str]:
        text_lower = text.lower()
        
        for risk_type, keywords in self.high_risk_keywords.items():
            if self._match_keyword_in_text(keywords, text_lower):
                return True, risk_type
        
        if self._match_keyword_in_list(self.risky_intents, intent):
            if self._match_keyword_in_text(self._get_all_high_risk_keywords(), text_lower):
                return True, "content_generation"
        
        return False, ""
    
    def check_simple_task(self, text: str, intent: List[str], parameters: Dict[str, Any]) -> Tuple[bool, str]:
        if len(intent) != 1:
            return False, "not_single_intent"
        
        if not self._match_keyword_in_list(self.simple_intents, intent):
            return False, "not_simple_intent"
        
        text_lower = text.lower()
        has_simple_keyword = self._match_keyword_in_text(self.simple_keywords, text_lower)
        
        if not has_simple_keyword:
            return False, "no_simple_keyword"
        
        if self._match_keyword_in_list(["计算"], intent):
            if re.search(r'\d', text):
                return True, "simple_task"
        
        if len(parameters) == 0:
            return False, "no_parameters"
        
        return True, "simple_task"
    
    def check_all_rules(self, text: str, intent: List[str], parameters: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "triggered": False,
            "rule": None,
            "risk_type": None,
            "advice": None
        }
        
        is_ambiguous, ambiguity_type = self.check_ambiguity(text, intent, parameters)
        if is_ambiguous:
            result["triggered"] = True
            result["rule"] = "ambiguity"
            result["risk_type"] = ambiguity_type
            result["advice"] = "启动Macro澄清流程，向用户澄清具体需求"
            return result
        
        is_high_risk, risk_type = self.check_high_risk(text, intent)
        if is_high_risk:
            result["triggered"] = True
            result["rule"] = "high_risk"
            result["risk_type"] = risk_type
            result["advice"] = "启用谨慎模式，执行前后发出风险提示"
            return result
        
        is_simple, simple_reason = self.check_simple_task(text, intent, parameters)
        if is_simple:
            result["triggered"] = True
            result["rule"] = "simple_task"
            result["risk_type"] = simple_reason
            result["advice"] = "启动Micro执行器，直接调用工具执行"
            return result
        
        return result
    
    def load_keywords_from_config(self, config: Dict[str, Any]) -> None:
        """
        从配置字典加载关键词库（支持外部配置文件扩展）
        
        Args:
            config: 配置字典，包含各关键词库
        """
        if "ambiguous_keywords" in config:
            self.ambiguous_keywords = config["ambiguous_keywords"]
        
        if "question_words" in config:
            self.question_words = config["question_words"]
        
        if "high_risk_keywords" in config:
            self.high_risk_keywords = config["high_risk_keywords"]
        
        if "risky_intents" in config:
            self.risky_intents = config["risky_intents"]
        
        if "simple_intents" in config:
            self.simple_intents = config["simple_intents"]
        
        if "simple_keywords" in config:
            self.simple_keywords = config["simple_keywords"]
    
    def load_keywords_from_file(self, file_path: str) -> None:
        """
        从JSON文件加载关键词库
        
        Args:
            file_path: JSON配置文件路径
        """
        import json
        
        with open(file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        self.load_keywords_from_config(config)