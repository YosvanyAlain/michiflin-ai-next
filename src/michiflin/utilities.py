from __future__ import annotations

import ast
import math
import operator
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import AppError

_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_UNITS = {
    "m": ("length", 1), "km": ("length", 1000), "cm": ("length", 0.01),
    "mm": ("length", 0.001), "mi": ("length", 1609.344), "ft": ("length", 0.3048),
    "in": ("length", 0.0254), "kg": ("mass", 1), "g": ("mass", 0.001),
    "lb": ("mass", 0.45359237), "oz": ("mass", 0.028349523125),
    "s": ("time", 1), "min": ("time", 60), "h": ("time", 3600), "day": ("time", 86400),
    "B": ("storage", 1), "KB": ("storage", 1000), "MB": ("storage", 1_000_000),
    "GB": ("storage", 1_000_000_000), "KiB": ("storage", 1024),
    "MiB": ("storage", 1048576), "GiB": ("storage", 1073741824),
    "L": ("volume", 1), "mL": ("volume", 0.001), "gal_us": ("volume", 3.785411784),
}


def calculate(expression: object) -> dict:
    if not isinstance(expression, str) or not 1 <= len(expression.strip()) <= 400:
        raise AppError("invalid_expression")
    try:
        tree = ast.parse(expression.replace("×", "*").replace("÷", "/"), mode="eval")
        if len(list(ast.walk(tree))) > 64:
            raise ValueError

        def evaluate(node: ast.AST) -> float | int:
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = node.value
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Pow):
                    if abs(right) > 32:
                        raise ValueError
                    value = left ** right
                elif type(node.op) in _OPERATORS:
                    value = _OPERATORS[type(node.op)](left, right)
                else:
                    raise ValueError
            else:
                raise ValueError
            if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 1e100:
                raise ValueError
            return value

        result = evaluate(tree.body)
    except (SyntaxError, ValueError, OverflowError, ZeroDivisionError, RecursionError):
        raise AppError("invalid_expression") from None
    return {"expression": expression, "value": result, "display": format(result, ".14g")}


def convert(value: object, source: str, target: str) -> dict:
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e50:
        raise AppError("invalid_number")
    if source in {"C", "F", "K"} and target in {"C", "F", "K"}:
        celsius = value if source == "C" else (value - 32) * 5 / 9 if source == "F" else value - 273.15
        if celsius < -273.15000001:
            raise AppError("below_absolute_zero")
        result = celsius if target == "C" else celsius * 9 / 5 + 32 if target == "F" else celsius + 273.15
    elif source in _UNITS and target in _UNITS and _UNITS[source][0] == _UNITS[target][0]:
        result = value * _UNITS[source][1] / _UNITS[target][1]
    else:
        raise AppError("incompatible_units")
    return {"value": result, "source": source, "target": target, "display": format(result, ".12g")}


def time_in(zone: str) -> dict:
    if not isinstance(zone, str) or len(zone) > 100:
        raise AppError("invalid_timezone")
    try:
        moment = datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        raise AppError("invalid_timezone") from None
    return {"zone": zone, "iso": moment.isoformat(), "date": moment.date().isoformat(),
            "time": moment.strftime("%H:%M:%S"), "offset": moment.strftime("%z")}
