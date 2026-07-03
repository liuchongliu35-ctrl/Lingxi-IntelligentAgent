#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档解析工具
读取本地TXT、MD、PDF文件内容
"""

import os
from typing import Optional

import PyPDF2


class DocumentParser:
    """文档解析工具"""
    
    def run(self, file_path: str) -> str:
        """解析文档
        
        Args:
            file_path: 文件路径
            
        Returns:
            文档内容
        """
        if not os.path.exists(file_path):
            return f"文件 {file_path} 不存在"
        
        file_extension = os.path.splitext(file_path)[1].lower()
        
        try:
            if file_extension == ".txt":
                return self._parse_txt(file_path)
            elif file_extension == ".md":
                return self._parse_md(file_path)
            elif file_extension == ".pdf":
                return self._parse_pdf(file_path)
            else:
                return f"不支持的文件类型: {file_extension}"
        except Exception as e:
            return f"解析文件失败: {str(e)}"
    
    def _parse_txt(self, file_path: str) -> str:
        """解析TXT文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件内容
        """
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    
    def _parse_md(self, file_path: str) -> str:
        """解析MD文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件内容
        """
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    
    def _parse_pdf(self, file_path: str) -> str:
        """解析PDF文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件内容
        """
        content = ""
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page_num in range(len(reader.pages)):
                page = reader.pages[page_num]
                content += page.extract_text()
        return content