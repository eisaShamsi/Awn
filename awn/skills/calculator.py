"""Calculator skill — evaluates arithmetic expressions safely."""

from __future__ import annotations

import ast
import operator
from typing import Union

from .base import BaseSkill

_Number = Union[int, float]

_OPS: dict[type, object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


def _eval(node: ast.AST) -> _Number:
    """Recursively evaluate an AST node using only safe operations."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_eval(node.left), _eval(node.right))  # type: ignore[operator]
    if isinstance(node, ast.UnaryOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_eval(node.operand))  # type: ignore[operator]
    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


class CalculatorSkill(BaseSkill):
    """Evaluate arithmetic expressions."""

    names = ("calc", "calculate", "math", "=")
    description = "Evaluate an arithmetic expression."
    usage = "calc <expression>  (e.g. calc 2 + 3 * 4)"

    def run(self, args: str) -> str:
        expr = args.strip()
        if not expr:
            return "Usage: calc <expression>"
        try:
            tree = ast.parse(expr, mode="eval")
            result = _eval(tree)
            # Present integers without a decimal point
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"{expr} = {result}"
        except ZeroDivisionError:
            return "Error: division by zero."
        except Exception:
            return f"Could not evaluate '{expr}'. Please use a valid arithmetic expression."
