#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础模型接口
定义所有模型需要实现的方法
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Generator


class BaseModel(ABC):
    """基础模型接口"""
    
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        pass
    
    @abstractmethod
    def stream_generate(self, prompt: str, **kwargs) -> Generator[str, None, None]:
        """流式生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本流
        """
        pass