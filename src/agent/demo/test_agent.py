from agent.complexity_judge import ComplexityJudge

def test_agent():
    judge = ComplexityJudge()
    
    test_cases = [
        {
            "text": "计算1024×768",
            "expected_intent": ["计算"],
            "expected_strategy": "micro"
        },
        {
            "text": "今天天气怎么样",
            "expected_intent": [],
            "expected_strategy": "macro"
        },
        {
            "text": "搜索论文并总结核心贡献",
            "expected_intent": ["搜索", "总结"],
            "expected_strategy": "meso"
        },
        {
            "text": "帮我分析一下市场情况",
            "expected_intent": ["分析"],
            "expected_strategy": "macro"
        },
        {
            "text": "帮我规划下周末的上海三日游",
            "expected_intent": ["规划"],
            "expected_strategy": "meso"
        },
        {
            "text": "搜索并分析数据",
            "expected_intent": ["搜索", "分析"],
            "expected_strategy": "meso"
        },
        {
            "text": "帮我写一份房屋租赁合同",
            "expected_intent": ["写"],
            "expected_strategy": "meso"
        },
        {
            "text": "查看当前时间",
            "expected_intent": ["读取"],
            "expected_strategy": "micro"
        },
        {
            "text": "翻译这篇英文文档",
            "expected_intent": ["翻译"],
            "expected_strategy": "micro"
        },
        {
            "text": "推荐几本Python入门书籍",
            "expected_intent": ["推荐"],
            "expected_strategy": "meso"
        }
    ]
    
    print("=" * 80)
    print("Agent整体流程测试")
    print("=" * 80)
    
    for i, case in enumerate(test_cases, 1):
        print(f"\n测试用例 {i}: {case['text']}")
        print("-" * 60)
        
        try:
            task = judge.judge(case['text'])
            
            print(f"原始输入: {task.original_input}")
            print(f"清洗后输入: {task.cleaned_input}")
            print(f"分词结果: {task.tokens}")
            print(f"命中连接词: {task.hit_keywords}")
            print(f"识别到的意图: {task.intent}")
            print(f"提取的实体: {task.entities}")
            print(f"解析的参数: {task.parameters}")
            print(f"启发式规则结果: {task.heuristic_result}")
            print(f"七维度评分: {task.seven_dimension_scores}")
            print(f"复杂度分数: {task.complexity_score}")
            print(f"复杂度等级: {task.complexity_level}")
            print(f"执行策略: {task.execution_strategy}")
            
            print(f"\n预期意图: {case['expected_intent']}")
            print(f"实际意图: {task.intent}")
            print(f"意图匹配: {'✓' if set(task.intent) == set(case['expected_intent']) else '✗'}")
            
            print(f"\n预期策略: {case['expected_strategy']}")
            print(f"实际策略: {task.execution_strategy}")
            print(f"策略匹配: {'✓' if task.execution_strategy == case['expected_strategy'] else '✗'}")
            
        except Exception as e:
            print(f"测试失败: {e}")
            import traceback
            traceback.print_exc()
        
        print("-" * 60)
    
    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

if __name__ == "__main__":
    test_agent()