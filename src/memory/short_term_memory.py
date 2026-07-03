#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短期记忆
保存当前会话的上下文信息
"""

from typing import List, Dict, Any


class ShortTermMemory:
    """短期记忆"""
    
    def __init__(self, max_history: int = 10):
        """初始化短期记忆
        
        Args:
            max_history: 最大历史记录数
        """
        self.max_history = max_history
        self.history: List[Dict[str, str]] = []
    
    def add_message(self, role: str, content: str):
        """添加消息到记忆
        
        Args:
            role: 角色（user/assistant）
            content: 内容
        """
        self.history.append({"role": role, "content": content})
        
        # 保持历史记录在最大范围内
        if len(self.history) > self.max_history:
            # 丢掉前面的历史记录，只保留最近的max_history条记录
            self.history = self.history[-self.max_history:]
    
    def get_history(self) -> List[Dict[str, str]]:
        """获取历史记录
        
        Returns:
            历史记录列表
        """
        return self.history
    
    def get_history_text(self) -> str:
        """获取历史记录文本
        
        Returns:
            组装后的历史记录文本
        """
        text = ""
        for message in self.history:
            text += f"{message['role']}: {message['content']}\n"
        return text
    
    def clear(self):
        """清空记忆"""
        self.history = []
    
    def get_last_message(self) -> Dict[str, str]:
        """获取最后一条消息
        
        Returns:
            最后一条消息
        """
        if self.history:
            return self.history[-1]
        return {"role": "", "content": ""}
    
    def get_history_length(self) -> int:
        """获取历史记录长度
        
        Returns:
            历史记录长度
        """
        return len(self.history)