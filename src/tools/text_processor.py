#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本处理工具
摘要生成、关键词提取、格式排版
"""

import re
from typing import List


class TextProcessor:
    """文本处理工具"""
    
    def run(self, text: str, operation: str = "summary") -> str:
        """处理文本
        
        Args:
            text: 文本内容
            operation: 操作类型 (summary/keywords/format)
            
        Returns:
            处理结果
        """
        if operation == "summary":
            return self._generate_summary(text)
        elif operation == "keywords":
            return self._extract_keywords(text)
        elif operation == "format":
            return self._format_text(text)
        else:
            return f"不支持的操作类型: {operation}"
    
    def _generate_summary(self, text: str, max_length: int = 200) -> str:
        """生成摘要
        
        Args:
            text: 文本内容
            max_length: 摘要最大长度
            
        Returns:
            摘要
        """
        # 简单的摘要生成实现
        # 实际应用中可以使用更复杂的算法
        sentences = re.split(r'[。！？.!?]', text)
        summary = ""
        
        for sentence in sentences:
            if len(summary) + len(sentence) <= max_length:
                summary += sentence + "。"
            else:
                break
        
        return summary
    
    def _extract_keywords(self, text: str, top_n: int = 10) -> str:
        """提取关键词
        
        Args:
            text: 文本内容
            top_n: 提取前n个关键词
            
        Returns:
            关键词列表
        """
        # 简单的关键词提取实现
        # 实际应用中可以使用TF-IDF或其他算法
        import jieba
        
        # 分词
        words = jieba.cut(text)
        
        # 统计词频
        word_count = {}
        for word in words:
            if len(word) > 1:  # 过滤单字
                word_count[word] = word_count.get(word, 0) + 1
        
        # 排序并提取前n个
        sorted_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)
        keywords = [word for word, _ in sorted_words[:top_n]]
        
        return ", ".join(keywords)
    
    def _format_text(self, text: str) -> str:
        """格式化文本
        
        Args:
            text: 文本内容
            
        Returns:
            格式化后的文本
        """
        # 去除多余的空白字符
        text = re.sub(r'\s+', ' ', text)
        
        # 确保每个句子结束后有空格
        text = re.sub(r'([。！？.!?])', r'\1 ', text)
        
        # 首字母大写
        sentences = text.split('. ')
        formatted_sentences = []
        for sentence in sentences:
            if sentence:
                formatted_sentence = sentence[0].upper() + sentence[1:]
                formatted_sentences.append(formatted_sentence)
        
        return '. '.join(formatted_sentences)