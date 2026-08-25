from __future__ import annotations

import ast
import math
import operator
import statistics
from typing import Any, Iterable

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode


class MathCalculator:
    """Deterministic calculator for limited arithmetic and simple statistics."""

    _OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    _FUNCTIONS = {
        "abs": abs,
        "ceil": math.ceil,
        "floor": math.floor,
        "log": math.log,
        "round": round,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
    }

    _CONSTANTS = {
        "e": math.e,
        "pi": math.pi,
    }

    def run(
        self,
        expression: str | None = None,
        data: Iterable[Any] | None = None,
        operation: str = "calculate",
    ) -> ToolResult:
        normalized_operation = str(operation or "calculate").strip().lower()
        if normalized_operation in {"calculate", "expression"}:
            if not isinstance(expression, str) or not expression.strip():
                return ToolResult.fail(
                    "expression is required for calculation.",
                    code=ToolErrorCode.MISSING_REQUIRED_PARAM.value,
                )
            try:
                value = self._eval_expression(expression)
            except Exception as exc:
                return ToolResult.fail(
                    f"Invalid or unsupported math expression: {exc}",
                    code=ToolErrorCode.INVALID_ARGS.value,
                    data={
                        "operation": "expression",
                        "normalized_expression": _normalize_expression(expression),
                    },
                )
            data_result = {
                "result": value,
                "operation": "expression",
                "normalized_expression": _normalize_expression(expression),
            }
            return ToolResult.ok(data=data_result, message=str(value))

        if normalized_operation in {"statistics", "stats"}:
            try:
                values = _numeric_values(data)
            except (TypeError, ValueError) as exc:
                return ToolResult.fail(
                    f"Invalid statistics data: {exc}",
                    code=ToolErrorCode.INVALID_ARGS.value,
                )
            if not values:
                return ToolResult.fail(
                    "data must contain at least one number.",
                    code=ToolErrorCode.INVALID_ARGS.value,
                )
            result = {
                "count": len(values),
                "sum": sum(values),
                "mean": statistics.mean(values),
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
            }
            return ToolResult.ok(
                data={
                    "result": result,
                    "operation": "statistics",
                    "normalized_expression": None,
                },
                message=jsonish(result),
            )

        return ToolResult.fail(
            f"Unsupported math operation: {operation}",
            code=ToolErrorCode.INVALID_ARGS.value,
            data={"operation": operation},
        )

    def _eval_expression(self, expression: str) -> float:
        tree = ast.parse(_normalize_expression(expression), mode="eval")
        return float(self._eval_node(tree.body))

    def _eval_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in self._OPS:
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
                raise ValueError("exponent is too large")
            return self._OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](self._eval_node(node.operand))
        if isinstance(node, ast.Name) and node.id in self._CONSTANTS:
            return self._CONSTANTS[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in self._FUNCTIONS:
                raise ValueError("function is not allowed")
            if node.keywords:
                raise ValueError("keyword arguments are not allowed")
            args = [self._eval_node(arg) for arg in node.args]
            return self._FUNCTIONS[node.func.id](*args)
        raise ValueError("expression node is not allowed")


def _normalize_expression(expression: str) -> str:
    return str(expression or "").replace("×", "*").strip()


def _numeric_values(data: Iterable[Any] | None) -> list[float]:
    if data is None:
        raise TypeError("data is required")
    if isinstance(data, (str, bytes)):
        raise TypeError("data must be an array of numbers")
    return [float(item) for item in data]


def jsonish(value: dict[str, Any]) -> str:
    parts = [f"{key}={value[key]}" for key in sorted(value)]
    return ", ".join(parts)


__all__ = ["MathCalculator"]
