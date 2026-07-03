#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具管理器
管理和调用各种工具
"""

from typing import Dict, Any, Optional

from tools.document_parser import DocumentParser
from tools.text_processor import TextProcessor
from tools.math_calculator import MathCalculator
from tools.translator import Translator
from tools.time_query import TimeQuery
from tools.search_tool import SearchTool
from tools.code_executor import CodeExecutor
from tools.file_writer import FileWriter


class ToolManager:
    """工具管理器"""
    
    def __init__(self):
        """初始化工具管理器"""
        # 初始化各个工具
        self.tools = {
            "document_parser": DocumentParser(),
            "text_processor": TextProcessor(),
            "math_calculator": MathCalculator(),
            "translator": Translator(),
            "time_query": TimeQuery(),
            "search_tool": SearchTool(),
            "code_executor": CodeExecutor(),
            "file_writer": FileWriter()
        }
    
    def get_tool(self, tool_name: str) -> Optional[Any]:
        """获取工具
        
        Args:
            tool_name: 工具名称
            
        Returns:
            工具实例
        """
        return self.tools.get(tool_name)
    
    def list_tools(self) -> Dict[str, str]:
        """列出所有工具
        
        Returns:
            工具名称和描述的字典
        """
        return {
            "document_parser": "文档解析工具：读取本地TXT、MD、PDF文件内容",
            "text_processor": "文本处理工具：摘要生成、关键词提取、格式排版",
            "math_calculator": "数学计算工具：复杂公式运算、数据统计",
            "translator": "翻译工具：中英日韩多语言互译",
            "time_query": "时间查询工具：获取当前时间、日期换算",
            "search_tool": "网络搜索工具：通过搜索引擎获取实时信息",
            "code_executor": "代码执行工具：执行Python代码并返回结果",
            "file_writer": "文件写入工具：将内容写入文件"
        }
    
    def run_tool(self, tool_name: str, **kwargs) -> str:
        """运行工具
        
        Args:
            tool_name: 工具名称
            **kwargs: 工具参数
            
        Returns:
            工具执行结果
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return f"工具 {tool_name} 不存在"
        
        try:
            # 调用工具的run方法
            if hasattr(tool, "run"):
                return tool.run(**kwargs)
            else:
                return f"工具 {tool_name} 没有run方法"
        except Exception as e:
            return f"工具 {tool_name} 执行失败: {str(e)}"