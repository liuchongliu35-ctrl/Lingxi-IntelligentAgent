#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件写入工具
将内容写入文件
"""

import os


class FileWriter:
    """文件写入工具"""
    
    def run(self, content: str, file_path: str, overwrite: bool = False) -> str:
        """将内容写入文件
        
        Args:
            content: 要写入的内容
            file_path: 文件路径
            overwrite: 是否覆盖现有文件
            
        Returns:
            操作结果
        """
        try:
            # 检查文件是否存在
            if os.path.exists(file_path) and not overwrite:
                return f"文件 {file_path} 已存在，请设置 overwrite=True 覆盖"
            
            # 确保目录存在
            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return f"文件写入成功: {file_path}"
            
        except Exception as e:
            return f"文件写入失败: {str(e)}"