#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通义千问模型
接入通义千问大模型API
"""

import os
import requests
from typing import Optional, Dict, Any, Generator

from src.models.base_model import BaseModel


class QianwenModel(BaseModel):
    """通义千问模型"""
    
    def __init__(self):
        """初始化通义千问模型"""
        self.api_key = os.getenv("QIANWEN_API_KEY")
        if not self.api_key:
            raise ValueError("请设置QIANWEN_API_KEY环境变量")
        
        self.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    
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
            "model": "qwen-plus",  # 通义千问模型
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 1000)
            }
        }
        
        response = requests.post(self.base_url, headers=headers, json=data)
        response.raise_for_status()
        
        return response.json()["output"]["text"]
    
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
            "model": "qwen-plus",  # 通义千问模型
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 1000),
                "stream": True
            }
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
                            if "output" in data and "text" in data["output"]:
                                yield data["output"]["text"]
                        except json.JSONDecodeError:
                            pass
