#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代码执行工具
执行Python代码并返回结果
"""

import subprocess
import tempfile
import os


class CodeExecutor:
    """代码执行工具"""
    
    def run(self, code: str, timeout: int = 30) -> str:
        """执行Python代码
        
        Args:
            code: Python代码
            timeout: 执行超时时间（秒）
            
        Returns:
            代码执行结果
        """
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            # 执行代码
            result = subprocess.run(
                ['python', temp_file],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            # 清理临时文件
            os.unlink(temp_file)
            
            # 返回结果
            if result.returncode == 0:
                return f"代码执行成功:\n{result.stdout}"
            else:
                return f"代码执行失败:\n错误信息: {result.stderr}"
                
        except subprocess.TimeoutExpired:
            if 'temp_file' in locals():
                os.unlink(temp_file)
            return f"代码执行超时（超过{timeout}秒）"
        except Exception as e:
            if 'temp_file' in locals():
                os.unlink(temp_file)
            return f"代码执行异常: {str(e)}"