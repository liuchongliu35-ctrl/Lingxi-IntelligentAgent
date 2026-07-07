#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI模型
接入OpenAI大模型API
"""

import os
import openai
from typing import Optional, Dict, Any, Generator

from src.models.base_model import BaseModel


class OpenAIModel(BaseModel):
    """OpenAI模型"""
    
    def __init__(self):
        """初始化OpenAI模型"""
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("请设置OPENAI_API_KEY环境变量")
        
        openai.api_key = self.api_key
    
    def generate(self, prompt: str, **kwargs) -> str:
        """生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        response = openai.chat.completions.create(
            model=kwargs.get("model", "gpt-3.5-turbo"),
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 1000)
        )
        
        return response.choices[0].message.content
    
    def stream_generate(self, prompt: str, **kwargs) -> Generator[str, None, None]:
        """流式生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本流
        """
        response = openai.chat.completions.create(
            model=kwargs.get("model", "gpt-3.5-turbo"),
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 1000),
            stream=True
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
