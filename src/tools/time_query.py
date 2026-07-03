#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时间查询工具
获取当前时间、日期换算
"""

import datetime
from typing import Optional


class TimeQuery:
    """时间查询工具"""
    
    def run(self, operation: str = "current", date: str = None) -> str:
        """查询时间
        
        Args:
            operation: 操作类型 (current/convert)
            date: 日期字符串 (YYYY-MM-DD)
            
        Returns:
            时间查询结果
        """
        if operation == "current":
            return self._get_current_time()
        elif operation == "convert":
            if date:
                return self._convert_date(date)
            else:
                return "请提供日期字符串"
        else:
            return f"不支持的操作类型: {operation}"
    
    def _get_current_time(self) -> str:
        """获取当前时间
        
        Returns:
            当前时间
        """
        now = datetime.datetime.now()
        return now.strftime("当前时间: %Y-%m-%d %H:%M:%S")
    
    def _convert_date(self, date_str: str) -> str:
        """换算日期
        
        Args:
            date_str: 日期字符串 (YYYY-MM-DD)
            
        Returns:
            日期换算结果
        """
        try:
            # 解析日期
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            
            # 获取星期
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekdays[date.weekday()]
            
            # 获取月份天数
            import calendar
            days_in_month = calendar.monthrange(date.year, date.month)[1]
            
            # 构建结果
            result = f"日期: {date_str}\n"
            result += f"星期: {weekday}\n"
            result += f"月份天数: {days_in_month}\n"
            result += f"是当年的第 {date.timetuple().tm_yday} 天"
            
            return result
        except Exception as e:
            return f"日期解析失败: {str(e)}"