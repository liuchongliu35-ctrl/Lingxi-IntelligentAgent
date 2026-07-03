#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG系统
集成FAISS向量库，用于知识库检索
"""

from typing import List, Optional


class RAGSystem:
    """RAG系统"""
    
    def __init__(self, long_term_memory):
        """初始化RAG系统
        
        Args:
            long_term_memory: 长期记忆
        """
        self.long_term_memory = long_term_memory
    
    def retrieve(self, query: str, top_k: int = 3) -> List[str]:
        """检索相关文档
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            
        Returns:
            相关文档列表
        """
        return self.long_term_memory.search(query, top_k)
    
    def add_document(self, document: str):
        """添加文档到知识库
        
        Args:
            document: 文档内容
        """
        self.long_term_memory.add_document(document)
    
    def get_document_count(self) -> int:
        """获取文档数量
        
        Returns:
            文档数量
        """
        return self.long_term_memory.get_document_count()
    
    def clear(self):
        """清空知识库"""
        self.long_term_memory.clear()
    
    def generate_answer(self, query: str, model_manager, context: str = "") -> str:
        """生成回答
        
        Args:
            query: 查询文本
            model_manager: 模型管理器
            context: 额外上下文
            
        Returns:
            生成的回答
        """
        # 检索相关文档
        relevant_docs = self.retrieve(query)
        
        # 构建上下文
        retrieved_context = "\n".join(relevant_docs)
        full_context = f"{context}\n\n知识库信息:\n{retrieved_context}"
        
        # 生成回答
        prompt = f"""
基于以下信息回答问题：

{full_context}

问题：
{query}

请根据上述信息生成一个详细、准确的回答。
        """
        
        return model_manager.generate(prompt)