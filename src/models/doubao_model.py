#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆包模型
接入豆包大模型API
"""

import os
import requests
from typing import Optional, Dict, Any, Generator

from src.models.base_model import BaseModel


class DoubaoModel(BaseModel):
    """豆包模型"""
    
    def __init__(self):
        """初始化豆包模型"""
        self.api_key = os.getenv("DOUBAO_API_KEY")
        if not self.api_key:
            raise ValueError("请设置DOUBAO_API_KEY环境变量")
        
        self.base_url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    
    def generate(self, prompt: str, **kwargs) -> str:
        """生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "ep-20240414141157-8sw7r",  # 豆包模型ID
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1000)
        }
        
        response = requests.post(self.base_url, headers=headers, json=data)
        response.raise_for_status()
        
        return response.json()["choices"][0]["message"]["content"]
    
    def stream_generate(self, prompt: str, **kwargs) -> Generator[str, None, None]:
        """流式生成文本
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本流
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "ep-20240414141157-8sw7r",  # 豆包模型ID
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1000),
            "stream": True
        }
        
        response = requests.post(self.base_url, headers=headers, json=data, stream=True)
        response.raise_for_status()
        
        for chunk in response.iter_lines():
            if chunk:
                chunk = chunk.decode("utf-8")
                if chunk.startswith("data: "):
                    chunk = chunk[6:]
                    if chunk != "[DONE]":
                        import json
                        try:
                            data = json.loads(chunk)
                            if "choices" in data and data["choices"]:
                                delta = data["choices"][0].get("delta", {})
                                if "content" in delta:
                                    yield delta["content"]
                        except json.JSONDecodeError:
                            pass
