#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络搜索工具
通过搜索引擎获取实时信息
"""

import requests
from bs4 import BeautifulSoup


class SearchTool:
    """网络搜索工具"""
    
    def run(self, query: str, max_results: int = 5) -> str:
        """执行网络搜索
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            搜索结果摘要
        """
        try:
            # 使用Bing搜索API
            # 需要在.env中配置BING_API_KEY
            import os
            api_key = os.getenv("BING_API_KEY")
            
            if api_key:
                return self._bing_search(query, max_results, api_key)
            else:
                # 如果没有API密钥，使用简易搜索方法
                return self._simple_search(query, max_results)
                
        except Exception as e:
            return f"搜索失败: {str(e)}"
    
    def _bing_search(self, query: str, max_results: int, api_key: str) -> str:
        """使用Bing搜索API
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            api_key: Bing API密钥
            
        Returns:
            搜索结果摘要
        """
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {"q": query, "count": max_results}
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        results = []
        
        for i, result in enumerate(data.get("webPages", {}).get("value", [])):
            title = result.get("name", "")
            snippet = result.get("snippet", "")
            url = result.get("url", "")
            results.append(f"{i+1}. [{title}]({url})\n   {snippet}")
        
        return "\n\n".join(results)
    
    def _simple_search(self, query: str, max_results: int) -> str:
        """简易搜索方法（不依赖API密钥）
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            搜索结果摘要
        """
        # 使用Google搜索（需要网络访问）
        search_url = f"https://www.google.com/search?q={query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        response = requests.get(search_url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        
        for i, result in enumerate(soup.find_all("div", class_="g")[:max_results]):
            title = result.find("h3")
            snippet = result.find("span", class_="aCOpRe")
            url = result.find("a")["href"] if result.find("a") else ""
            
            if title and snippet:
                results.append(f"{i+1}. {title.get_text()}\n   {snippet.get_text()}\n   {url}")
        
        if results:
            return "\n\n".join(results)
        else:
            return f"未找到关于 '{query}' 的搜索结果"