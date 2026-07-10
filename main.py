from __future__ import annotations

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from src.agent.complexity_analyzer import ComplexityAnalyzer
from src.agent.react_agent import ReactAgent
from src.memory.long_term_memory import LongTermMemory
from src.memory.short_term_memory import ShortTermMemory
from src.models.model_manager import ModelManager
from src.rag.rag_system import RAGSystem
from src.tools.tool_manager import ToolManager


def build_agent() -> ReactAgent:
    model_manager = ModelManager()
    short_term_memory = ShortTermMemory()
    long_term_memory = LongTermMemory()
    tool_manager = ToolManager()
    rag_system = RAGSystem(long_term_memory)
    complexity_analyzer = ComplexityAnalyzer(model_manager, tool_manager=tool_manager)

    return ReactAgent(
        model_manager=model_manager,
        short_term_memory=short_term_memory,
        long_term_memory=long_term_memory,
        tool_manager=tool_manager,
        rag_system=rag_system,
        complexity_analyzer=complexity_analyzer,
    )


def main():
    load_dotenv()
    agent = build_agent()

    print("=== Multifunction Agent ===")
    print("Type 'quit' or 'exit' to end the session.")

    while True:
        user_input = input("User: ").strip()
        if user_input.lower() in {"quit", "exit", "退出"}:
            print("Bye.")
            break
        if not user_input:
            continue
        print(f"Agent: {agent.run(user_input)}")


if __name__ == "__main__":
    main()

