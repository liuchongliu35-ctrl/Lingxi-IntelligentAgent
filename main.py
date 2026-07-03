#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多功能文档问答与任务处理智能体 
主入口文件
"""

import os
import sys
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加src目录到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from agent.complexity_analyzer import ComplexityAnalyzer
from agent.react_agent import ReactAgent
from memory.short_term_memory import ShortTermMemory
from memory.long_term_memory import LongTermMemory
from models.model_manager import ModelManager
from tools.tool_manager import ToolManager
from rag.rag_system import RAGSystem


def main():
    """主函数"""
    print("=== 智能任务执行助手 ===")
    print("正在初始化...")
    
    # 初始化各个模块
    model_manager = ModelManager()
    short_term_memory = ShortTermMemory()
    long_term_memory = LongTermMemory()
    tool_manager = ToolManager()
    rag_system = RAGSystem(long_term_memory)
    complexity_analyzer = ComplexityAnalyzer(model_manager)
    
    # 初始化Agent调度层
    agent = ReactAgent(
        model_manager=model_manager,
        short_term_memory=short_term_memory,
        long_term_memory=long_term_memory,
        tool_manager=tool_manager,
        rag_system=rag_system,
        complexity_analyzer=complexity_analyzer
    )
    
    print("初始化完成！")
    print("输入'退出'或'quit'结束对话")
    print("\n请输入您的问题：")
    
    # 开始对话循环
    while True:
        user_input = input("用户: ")
        
        if user_input.lower() in ['退出', 'quit', 'exit']:
            print("再见！")
            break
        
        print("\nAgent: ", end="")
        response = agent.run(user_input)
        print(response)
        print()


if __name__ == "__main__":
    main()