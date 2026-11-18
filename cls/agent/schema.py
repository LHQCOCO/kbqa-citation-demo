"""最小 JSON Schema 校验：type / required / enum / additionalProperties。

刻意不支持 $ref、oneOf、pattern 等，保持可审计、零依赖。
"""
from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPES.get(name)
    if py is None:
        return True  # 未知类型名不阻断，交给调用方约定
    return isinstance(value, py)


def validate(schema: dict | None, value: Any, path: str = "$") -> list[dict]:
    """返回错误列表：[{"path": ..., "message": ...}]，空列表表示通过。"""
    errors: list[dict] = []
    if not isinstance(schema, dict):
        return errors

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(_type_ok(value, n) for n in names):
            errors.append({"path": path, "message": f"类型应为 {'/'.join(names)}"})
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "message": f"取值必须是 {schema['enum']} 之一"})

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"path": f"{path}.{key}", "message": "缺少必填字段"})

        props = schema.get("properties") or {}
        extra_schema = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors.extend(validate(props[key], item, f"{path}.{key}"))
            elif extra_schema is False:
                errors.append({"path": f"{path}.{key}", "message": "未声明的字段"})
            elif isinstance(extra_schema, dict):
                errors.extend(validate(extra_schema, item, f"{path}.{key}"))

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(validate(schema["items"], item, f"{path}[{i}]"))

    return errors
