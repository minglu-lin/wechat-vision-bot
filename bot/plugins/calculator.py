#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Calculator Plugin: Safe Math Evaluation & Unit Conversions
Executes mathematical expressions in a secure AST sandbox.
"""

import ast
import math
import operator
import re
from .base import BasePlugin

SAFE_OPERATORS = {
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

SAFE_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log10,
    "ln": math.log,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
}

SAFE_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("仅支持数字常量")
    elif isinstance(node, ast.BinOp):
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            if op_type == ast.Pow and (right > 1000 or left > 10000):
                raise ValueError("指数计算数值过大，防止耗尽资源")
            return SAFE_OPERATORS[op_type](left, right)
        raise ValueError(f"不支持的操作符: {op_type}")
    elif isinstance(node, ast.UnaryOp):
        operand = _safe_eval(node.operand)
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            return SAFE_OPERATORS[op_type](operand)
        raise ValueError(f"不支持的一元操作符: {op_type}")
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_FUNCTIONS:
            args = [_safe_eval(arg) for arg in node.args]
            return SAFE_FUNCTIONS[node.func.id](*args)
        raise ValueError("不支持的函数调用")
    elif isinstance(node, ast.Name):
        if node.id in SAFE_CONSTANTS:
            return SAFE_CONSTANTS[node.id]
        raise ValueError(f"未定义的常量: {node.id}")
    raise ValueError(f"不支持的语法节点: {type(node)}")


class CalculatorPlugin(BasePlugin):
    name = "calculate"
    description = "执行科学数学计算（加减乘除、求幂、开方、三角函数、对数、取模等）"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "需要计算的数学表达式，例如：'2^10'、'sqrt(144) + 5'、'1024 * 768 / 2'",
            }
        },
        "required": ["expression"],
    }

    fast_patterns = [
        r'^(?:算一下?|计算|求一下?|算算)\s*(?P<expression>.+)$',
    ]

    def execute(self, expression: str, **kwargs) -> dict:
        expr = (expression or "").strip()
        if not expr:
            return {"error": "未提供数学表达式"}

        # Normalization
        clean_expr = expr.replace("×", "*").replace("÷", "/").replace("（", "(").replace("）", ")")
        clean_expr = clean_expr.replace("^", "**").replace("的平方", "**2").replace("的立方", "**3")
        clean_expr = re.sub(r'(\d+)\s*万', r'(\1 * 10000)', clean_expr)
        clean_expr = re.sub(r'(\d+)\s*亿', r'(\1 * 100000000)', clean_expr)

        try:
            tree = ast.parse(clean_expr, mode="eval")
            result = _safe_eval(tree.body)
            if isinstance(result, float):
                if result.is_integer():
                    result = int(result)
                else:
                    result = round(result, 6)
            return {
                "expression": expr,
                "result": result,
                "formatted": f"{result:,}" if isinstance(result, (int, float)) and abs(result) >= 10000 else str(result),
            }
        except Exception as e:
            return {"error": f"计算表达式失败: {e}"}

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"计算失败：{result['error']}"

        return f"🧮 计算结果：{result['expression']} = {result['formatted']}"
