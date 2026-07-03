"""
React Agent
基于ReAct架构的推理闭环，支持任务规划与多步骤执行
"""

from typing import Dict, Any, List
from agent import complexity_judge
from complexity_analyzer_cankao import ComplexityAnalyzer, StructuredTask


class ReactAgent:
    """React Agent"""
    
    def __init__(self, model_manager, short_term_memory, long_term_memory, tool_manager, rag_system, complexity_analyzer):
        """初始化React Agent
        
        Args:
            model_manager: 模型管理器
            short_term_memory: 短期记忆
            long_term_memory: 长期记忆
            tool_manager: 工具管理器
            rag_system: RAG系统
        """
        self.model_manager = model_manager
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.tool_manager = tool_manager
        self.rag_system = rag_system
        self.max_iterations = 6  # 最大思考步数
        self.early_stopping_method = "force"  # 强制停止
        self.complexity_judge = complexity_judge

        # 初始化复杂度分析器
        self.complexity_analyzer = complexity_analyzer

    # todo: 实现任务规划与多步骤执行
    def run(self, user_input: str) -> str:
        """运行Agent
        Args:
            user_input: 用户输入

        Returns:
            Agent响应
        """
        # 添加用户输入到短期记忆
        self.short_term_memory.add_message("user", user_input)

        
        # 获取历史上下文
        history = self.short_term_memory.get_history_text()
        
        # 检查是否是复杂任务，需要规划多步骤
        is_complex_task = self._detect_complex_task(user_input)
        
        if is_complex_task:
            # 复杂任务：进行任务规划与多步骤执行
            response = self._execute_complex_task(user_input, history)
        else:
            # 简单任务：单次思考执行
            response = self._execute_single_step(user_input, history)
        
        # 添加Agent响应到短期记忆
        self.short_term_memory.add_message("assistant", response)
        
        return response
    

    def _analyze_complexity(self, user_input: str) -> StructuredTask:
        """分析任务复杂度（使用七维度加权评分模型）
        
        Args:
            user_input: 用户输入
            
        Returns:
            结构化任务对象，包含复杂度分析结果
        """
        return self.complexity_analyzer.analyze(user_input)
    
    def _detect_complex_task(self, user_input: str) -> bool:
        """检测是否为复杂任务
        
        Args:
            user_input: 用户输入
            
        Returns:
            是否为复杂任务（需要多步骤执行）
        """
        task = self._analyze_complexity(user_input)
        # 中等和复杂任务需要多步骤执行
        return task.complexity_level in ["medium", "complex", "high_risk"]
    



    def _execute_complex_task(self, user_input: str, history: str) -> str:
        """执行复杂任务（多步骤）
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            
        Returns:
            Agent响应
        """
        # 步骤1：任务规划
        plan = self._generate_task_plan(user_input, history)
        print(f"📋 任务规划：\n{plan}\n")
        
        # 步骤2：解析计划
        steps = self._parse_plan(plan)
        
        # 步骤3：执行每个步骤
        results = []
        for i, step in enumerate(steps, 1):
            if i > self.max_iterations:
                results.append(f"⚠️ 已达到最大思考步数（{self.max_iterations}步），任务中止")
                break
            
            print(f"🔄 正在执行步骤 {i}/{len(steps)}: {step}")
            step_result = self._execute_step(step, history)
            results.append(f"步骤 {i}: {step}\n结果: {step_result}")
            
            # 更新历史上下文
            history += f"\n步骤 {i} 结果: {step_result}"
        
        # 步骤4：生成最终报告
        final_report = self._generate_final_report(user_input, plan, results)
        
        return final_report
    


    # todo 生成任务规划
    def _generate_task_plan(self, user_input: str, history: str) -> str:
        """生成任务规划
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            
        Returns:
            任务计划
        """
        prompt = f"""
你是一个任务规划专家，需要将用户的请求拆解为具体的执行步骤。

用户请求：{user_input}

历史上下文：{history}

可用工具：
{self._get_tool_list()}

请输出详细的执行步骤计划，格式如下：
步骤1：[步骤描述]，使用工具：[工具名称]，参数：[参数]
步骤2：[步骤描述]，使用工具：[工具名称]，参数：[参数]
...
步骤N：[步骤描述]，使用工具：[工具名称]，参数：[参数]

注意：
- 如果不需要使用工具，使用工具：direct
- 步骤要清晰、可执行
- 最多输出5个步骤
        """
        
        return self.model_manager.generate(prompt)
    



    # todo 解析任务计划
    def _parse_plan(self, plan: str) -> List[str]:
        """解析任务计划
        
        Args:
            plan: 任务计划文本
            
        Returns:
            步骤列表
        """
        steps = []
        for line in plan.split('\n'):
            if line.startswith("步骤") or line.strip():
                steps.append(line.strip())
        return steps
    


    # todo 执行单个步骤
    def _execute_step(self, step: str, history: str) -> str:
        """执行单个步骤
        
        Args:
            step: 步骤描述
            history: 历史上下文
            
        Returns:
            步骤执行结果
        """
        # 生成思考和决策
        thought, action, action_input = self._generate_thought_and_action(step, history)
        
        # 执行动作
        if action == "tool":
            tool_name, tool_args = self._parse_tool_input(action_input)
            return self._call_tool(tool_name, tool_args)
        else:
            return self._generate_direct_response(step, history, thought)
    


    # todo 执行简单任务（单步骤）
    def _execute_single_step(self, user_input: str, history: str) -> str:
        """执行简单任务（单步骤）
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            
        Returns:
            Agent响应
        """
        # 生成思考和决策
        thought, action, action_input = self._generate_thought_and_action(user_input, history)
        
        # 执行动作
        if action == "tool":
            # 调用工具
            tool_name, tool_args = self._parse_tool_input(action_input)
            tool_result = self._call_tool(tool_name, tool_args)
            
            # 生成最终响应
            response = self._generate_final_response(user_input, history, thought, action, action_input, tool_result)
        else:
            # 直接生成响应
            response = self._generate_direct_response(user_input, history, thought)
        
        return response
    


    # todo 生成思考和决策
    def _generate_thought_and_action(self, user_input: str, history: str) -> tuple:
        """生成思考和决策
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            
        Returns:
            (思考, 动作, 动作输入)
        """
        prompt = f"""
你是一个基于ReAct架构的智能助手，需要按照以下格式进行思考和决策：

思考：[你的思考过程]
动作：[action_type]，其中action_type可以是tool或direct
动作输入：[action_input]

如果选择tool，请指定工具名称和参数，格式为：工具名称:参数1=值1,参数2=值2
如果选择direct，直接输出你的回答

工具列表：
{self._get_tool_list()}

历史上下文：
{history}

用户输入：
{user_input}

请按照格式输出你的思考、动作和动作输入：
        """
        
        response = self.model_manager.generate(prompt)
        
        # 解析响应
        lines = response.strip().split('\n')
        thought = ""
        action = "direct"
        action_input = ""
        
        for line in lines:
            if line.startswith("思考："):
                thought = line[3:].strip()
            elif line.startswith("动作："):
                action = line[3:].strip()
            elif line.startswith("动作输入："):
                action_input = line[5:].strip()
        
        return thought, action, action_input
    



    # todo 解析工具输入
    def _parse_tool_input(self, tool_input: str) -> tuple:
        """解析工具输入
        
        Args:
            tool_input: 工具输入字符串
            
        Returns:
            (工具名称, 工具参数)
        """
        if ':' in tool_input:
            parts = tool_input.split(':', 1)
            tool_name = parts[0].strip()
            args_str = parts[1].strip() if len(parts) > 1 else ''
            
            # 解析参数
            tool_args = {}
            if args_str:
                for arg in args_str.split(','):
                    if '=' in arg:
                        key, value = arg.split('=', 1)
                        tool_args[key.strip()] = value.strip()
            
            return tool_name, tool_args
        else:
            return tool_input.strip(), {}
    



    # todo 调用工具
    def _call_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """调用工具
        
        Args:
            tool_name: 工具名称
            tool_args: 工具参数
            
        Returns:
            工具执行结果
        """
        # 最多重试2次
        for i in range(3):
            try:
                result = self.tool_manager.run_tool(tool_name, **tool_args)
                if result is None:
                    return "工具执行结果为空"
                return result
            except Exception as e:
                if i == 2:
                    return f"工具调用失败: {str(e)}"
        # 确保所有代码路径都返回字符串
        return "工具调用失败: 未知错误"
    



    # todo 生成最终响应
    def _generate_final_response(self, user_input: str, history: str, thought: str, action: str, action_input: str, tool_result: str) -> str:
        """生成最终响应
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            thought: 思考过程
            action: 动作
            action_input: 动作输入
            tool_result: 工具执行结果
            
        Returns:
            最终响应
        """
        prompt = f"""
根据以下信息，生成最终的回答：

历史上下文：
{history}

用户输入：
{user_input}

思考：
{thought}

动作：
{action}

动作输入：
{action_input}

工具执行结果：
{tool_result}

请生成一个自然、友好的回答，直接输出最终结果，不要包含思考过程。
        """
        
        return self.model_manager.generate(prompt)
    



    # todo 直接生成响应
    def _generate_direct_response(self, user_input: str, history: str, thought: str) -> str:
        """直接生成响应
        
        Args:
            user_input: 用户输入
            history: 历史上下文
            thought: 思考过程
            
        Returns:
            直接响应
        """
        prompt = f"""
根据以下信息，生成直接回答：

历史上下文：
{history}

用户输入：
{user_input}

思考：
{thought}

请生成一个自然、友好的回答，直接输出最终结果。
        """
        
        return self.model_manager.generate(prompt)
    



    # todo 生成最终报告
    def _generate_final_report(self, user_input: str, plan: str, results: List[str]) -> str:
        """生成最终报告
        
        Args:
            user_input: 用户输入
            plan: 任务计划
            results: 各步骤执行结果
            
        Returns:
            最终报告
        """
        prompt = f"""
根据以下信息，生成详细的任务执行报告：

用户请求：
{user_input}

任务计划：
{plan}

执行结果：
{chr(10).join(results)}

请生成一个详细的任务执行报告，包括完成情况、关键结果和总结。
        """
        
        return self.model_manager.generate(prompt)
    


    # todo 获取工具列表
    def _get_tool_list(self) -> str:
        """获取工具列表
        
        Returns:
            工具列表字符串
        """
        tools = self.tool_manager.list_tools()
        tool_list = ""
        for name, description in tools.items():
            tool_list += f"{name}: {description}\n"
        return tool_list