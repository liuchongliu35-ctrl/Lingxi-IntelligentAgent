from __future__ import annotations

import ast
import math
import operator
import statistics
from typing import Iterable


class MathCalculator:
    """Safe calculator for basic arithmetic and statistics."""

    _OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    _NAMES = {
        "pi": math.pi,
        "e": math.e,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
    }

    def run(self, expression: str | None = None, data: Iterable[float] | None = None, operation: str = "calculate") -> str:
        if operation == "calculate":
            if not expression:
                return "Please provide a math expression."
            return str(self._eval_expression(expression))

        if operation == "statistics":
            if data is None:
                return "Please provide data for statistics."
            return self._statistics(data)

        return f"Unsupported math operation: {operation}"

    def _eval_expression(self, expression: str) -> float:
        tree = ast.parse(expression.replace("×", "*"), mode="eval")
        return float(self._eval_node(tree.body))

    def _eval_node(self, node: ast.AST):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](self._eval_node(node.left), self._eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](self._eval_node(node.operand))
        if isinstance(node, ast.Name) and node.id in self._NAMES:
            return self._NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self._NAMES:
            func = self._NAMES[node.func.id]
            args = [self._eval_node(arg) for arg in node.args]
            return func(*args)
        raise ValueError("Unsupported expression.")

    def _statistics(self, data: Iterable[float]) -> str:
        values = [float(item) for item in data]
        if not values:
            return "Data is empty."
        return (
            f"count={len(values)}, sum={sum(values)}, mean={statistics.mean(values)}, "
            f"median={statistics.median(values)}, min={min(values)}, max={max(values)}"
        )
