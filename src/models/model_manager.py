#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型管理器
负责管理不同的大模型API接口
"""

import os
from typing import Optional, Dict, Any, Generator

from models.base_model import BaseModel
from models.doubao_model import DoubaoModel
from models.qianwen_model import QianwenModel
from models.openai_model import OpenAIModel


class ModelManager:
    """模型管理器"""
    
    def __init__(self):
        """初始化模型管理器"""
        # 获取模型配置
        self.model_name = os.getenv("MODEL_NAME", "doubao")
        
        # 初始化对应模型
        if self.model_name == "doubao":
            self.model = DoubaoModel()
        elif self.model_name == "qianwen":
            self.model = QianwenModel()
        elif self.model_name == "openai":
            self.model = OpenAIModel()
        else:
            raise ValueError(f"不支持的模型: {self.model_name}")
    
    def generate(self, prompt: str, **kwargs) -> str:
        """生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        return self.model.generate(prompt, **kwargs)
    
    def stream_generate(self, prompt: str, **kwargs) -> Generator[str, None, None]:
        """流式生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本流
        """
        return self.model.stream_generate(prompt, **kwargs)
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息
        
        Returns:
            模型信息
        """
        return {
            "model_name": self.model_name,
            "model": self.model.__class__.__name__
        }