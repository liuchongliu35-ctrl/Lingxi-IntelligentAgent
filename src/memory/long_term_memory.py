#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长期记忆
基于FAISS向量库，用于存储和检索文档信息
"""

import os
import pickle
from typing import List, Dict, Any

import faiss
import numpy as np

# 导入DashScopeEmbeddings
try:
    from langchain_community.embeddings import DashScopeEmbeddings
    EMBEDDING_AVAILABLE = True
except ImportError:
    print("警告：未安装 langchain_community，将使用简单嵌入模型")
    EMBEDDING_AVAILABLE = False


class LongTermMemory:
    """长期记忆"""
    
    def __init__(self, vector_store_path: str = "data/vector_store"):
        """初始化长期记忆
        
        Args:
            vector_store_path: 向量存储路径
        """
        self.vector_store_path = vector_store_path
        self.index = None
        self.documents = []
        self.embeddings = []
        
        # 初始化嵌入模型
        self._init_embedding_model()
        
        # 确保存储目录存在
        os.makedirs(os.path.dirname(self.vector_store_path), exist_ok=True)
        
        # 尝试加载已有的向量库
        self.load()
    
    def _init_embedding_model(self):
        """初始化嵌入模型"""
        if EMBEDDING_AVAILABLE:
            try:
                # 从环境变量获取API密钥
                api_key = os.getenv("DASHSCOPE_API_KEY")
                if api_key:
                    self.embedding_model = DashScopeEmbeddings(
                        dashscope_api_key=api_key
                    )
                else:
                    # 如果没有设置API密钥，尝试使用默认配置
                    self.embedding_model = DashScopeEmbeddings()
                print("已初始化 DashScopeEmbeddings")
            except Exception as e:
                print(f"初始化DashScopeEmbeddings失败: {e}")
                print("将使用简单嵌入模型")
                self.embedding_model = None
        else:
            self.embedding_model = None
    
    def add_document(self, document: str):
        """添加文档到长期记忆
        
        Args:
            document: 文档内容
        """
        self.documents.append(document)
        
        # 使用嵌入模型获取向量
        embedding = self._get_embedding(document)
        self.embeddings.append(embedding)
        
        # 更新FAISS索引
        self._update_index()
        
        # 保存到磁盘
        self.save()
    
    def add_documents(self, documents: List[str]):
        """批量添加文档到长期记忆
        
        Args:
            documents: 文档列表
        """
        self.documents.extend(documents)
        
        # 使用嵌入模型批量获取向量
        embeddings = self._get_embeddings(documents)
        self.embeddings.extend(embeddings)
        
        # 更新FAISS索引
        self._update_index()
        
        # 保存到磁盘
        self.save()
    
    def search(self, query: str, top_k: int = 3) -> List[str]:
        """搜索相关文档
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            
        Returns:
            相关文档列表
        """
        if not self.index or len(self.documents) == 0:
            return []
        
        # 获取查询向量
        query_embedding = self._get_embedding(query)
        
        # 搜索相似向量
        search_result = self.index.search(np.array([query_embedding]), k=top_k)  # type: ignore
        distances, indices = search_result
        
        # 返回相关文档
        results = []
        for i in range(min(top_k, len(indices[0]))):
            if indices[0][i] < len(self.documents):
                results.append(self.documents[indices[0][i]])
        
        return results
    
    def _get_embedding(self, text: str) -> List[float]:
        """获取文本的嵌入向量
        
        Args:
            text: 文本
            
        Returns:
            嵌入向量
        """
        # 如果有专业嵌入模型，使用它
        if self.embedding_model:
            try:
                return self.embedding_model.embed_query(text)
            except Exception as e:
                print(f"使用DashScopeEmbeddings失败: {e}")
        
        # 回退到简单的向量表示
        return self._simple_embedding(text)
    
    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量获取文本的嵌入向量
        
        Args:
            texts: 文本列表
            
        Returns:
            嵌入向量列表
        """
        # 如果有专业嵌入模型，使用它
        if self.embedding_model:
            try:
                return self.embedding_model.embed_documents(texts)
            except Exception as e:
                print(f"使用DashScopeEmbeddings批量嵌入失败: {e}")
        
        # 回退到简单的向量表示
        return [self._simple_embedding(text) for text in texts]
    
    def _simple_embedding(self, text: str) -> List[float]:
        """简单的向量表示（用于回退）"""
        import hashlib
        
        hash_obj = hashlib.md5(text.encode())
        hash_hex = hash_obj.hexdigest()
        
        vector = []
        for i in range(0, len(hash_hex), 2):
            if i + 1 < len(hash_hex):
                vector.append(int(hash_hex[i:i+2], 16) / 255.0)
        
        while len(vector) < 128:
            vector.append(0.0)
        if len(vector) > 128:
            vector = vector[:128]
        
        return vector
    
    def _update_index(self):
        """更新FAISS索引"""
        if not self.embeddings:
            return
        
        dimension = len(self.embeddings[0])
        self.index = faiss.IndexFlatL2(dimension)
        self.index.add(x=np.array(self.embeddings))  # type: ignore
    
    def save(self):
        """保存向量库到磁盘"""
        data = {
            "documents": self.documents,
            "embeddings": self.embeddings
        }
        
        with open(self.vector_store_path, "wb") as f:
            pickle.dump(data, f)
    
    def load(self):
        """从磁盘加载向量库"""
        if os.path.exists(self.vector_store_path):
            try:
                with open(self.vector_store_path, "rb") as f:
                    data = pickle.load(f)
                    self.documents = data.get("documents", [])
                    self.embeddings = data.get("embeddings", [])
                    self._update_index()
            except Exception as e:
                print(f"加载向量库失败: {e}")
                self.documents = []
                self.embeddings = []
                self.index = None
    
    def clear(self):
        """清空长期记忆"""
        self.documents = []
        self.embeddings = []
        self.index = None
        self.save()
    
    def get_document_count(self) -> int:
        """获取文档数量
        
        Returns:
            文档数量
        """
        return len(self.documents)