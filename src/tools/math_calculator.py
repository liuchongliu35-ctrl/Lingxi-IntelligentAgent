#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数学计算工具
复杂公式运算、数据统计
"""

import math
import statistics
from typing import List


class MathCalculator:
    """数学计算工具"""
    
    def run(self, expression: str = None, data: List[float] = None, operation: str = "calculate") -> str:
        """执行数学计算
        
        Args:
            expression: 数学表达式
            data: 数据列表
            operation: 操作类型 (calculate/statistics)
            
        Returns:
            计算结果
        """
        if operation == "calculate":
            if expression:
                return self._calculate_expression(expression)
            else:
                return "请提供数学表达式"
        elif operation == "statistics":
            if data:
                return self._calculate_statistics(data)
            else:
                return "请提供数据列表"
        else:
            return f"不支持的操作类型: {operation}"
    
    def _calculate_expression(self, expression: str) -> str:
        """计算数学表达式
        
        Args:
            expression: 数学表达式
            
        Returns:
            计算结果
        """
        try:
            # 安全计算表达式
            # 只允许基本的数学运算和函数
            allowed_globals = {
                "math": math,
                "sin": math.sin,
                "cos": math.cos,
                "tan": math.tan,
                "sqrt": math.sqrt,
                "log": math.log,
                "exp": math.exp,
                "pi": math.pi,
                "e": math.e
            }
            
            result = eval(expression, allowed_globals)
            return str(result)
        except Exception as e:
            return f"计算失败: {str(e)}"
    
    def _calculate_statistics(self, data: List[float]) -> str:
        """计算数据统计
        
        Args:
            data: 数据列表
            
        Returns:
            统计结果
        """
        try:
            # 转换数据类型
            data = [float(x) for x in data]
            
            # 计算统计指标
            mean = statistics.mean(data)
            median = statistics.median(data)
            stdev = statistics.stdev(data) if len(data) > 1 else 0
            variance = statistics.variance(data) if len(data) > 1 else 0
            minimum = min(data)
            maximum = max(data)
            sum_ = sum(data)
            count = len(data)
            
            # 构建结果
            result = f"统计结果：\n"
            result += f"样本数: {count}\n"
            result += f"总和: {sum_}\n"
            result += f"平均值: {mean}\n"
            result += f"中位数: {median}\n"
            result += f"标准差: {stdev}\n"
            result += f"方差: {variance}\n"
            result += f"最小值: {minimum}\n"
            result += f"最大值: {maximum}"
            
            return result
        except Exception as e:
            return f"统计计算失败: {str(e)}"