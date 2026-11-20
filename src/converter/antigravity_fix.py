"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_ASPECT_RATIOS = [(1,1),(2,3),(3,2),(3,4),(4,3),(4,5),(5,4),(9,16),(16,9),(21,9)]

def _append_schema_hint(schema: Dict[str, Any], hint: str) -> None:
    """Move fragile validation details into description instead of sending them raw."""
    if not hint:
        return
    desc = schema.get("description")
    schema["description"] = f"{desc} ({hint})" if desc else hint


def _resolve_schema_ref(ref: str, root_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None

    node: Any = root_schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]

    return node if isinstance(node, dict) else None


def _clean_parameters_json_schema(
    schema: Any,
    root_schema: Optional[Dict[str, Any]] = None,
    visited: Optional[set] = None,
) -> Any:
    """Clean a tool schema for Code Assist's parametersJsonSchema field."""
    if isinstance(schema, list):
        return [_clean_parameters_json_schema(item, root_schema, visited) for item in schema]
    if not isinstance(schema, dict):
        return schema

    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        return {"type": "object", "description": "circular reference"}
    visited.add(schema_id)

    ref_key = "$ref" if "$ref" in schema else ("ref" if "ref" in schema else None)
    if ref_key:
        resolved = _resolve_schema_ref(schema[ref_key], root_schema)
        if resolved:
            merged = dict(resolved)
            for key in ("description", "default"):
                if key in schema:
                    merged[key] = schema[key]
            schema = merged

    if "allOf" in schema:
        result: Dict[str, Any] = {}
        for item in schema.get("allOf") or []:
            cleaned_item = _clean_parameters_json_schema(item, root_schema, visited)
            if not isinstance(cleaned_item, dict):
                continue
            if "properties" in cleaned_item:
                result.setdefault("properties", {}).update(cleaned_item["properties"])
            if "required" in cleaned_item:
                result.setdefault("required", []).extend(cleaned_item["required"])
            for key, value in cleaned_item.items():
                if key not in ("properties", "required"):
                    result[key] = value
        for key, value in schema.items():
            if key not in ("allOf", "properties", "required"):
                result[key] = value
            elif key in ("properties", "required") and key not in result:
                result[key] = value
    else:
        result = dict(schema)

    if result.get("nullable") is True:
        _append_schema_hint(result, "nullable")

    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            non_null_types = [
                str(t).lower()
                for t in type_value
                if isinstance(t, str) and t.lower() != "null"
            ]
            if non_null_types:
                result["type"] = non_null_types[0]
                if any(str(t).lower() == "null" for t in type_value):
                    _append_schema_hint(result, "nullable")
            else:
                result["type"] = "string"
        elif isinstance(type_value, str):
            lower_type = type_value.lower()
            if lower_type in {"string", "number", "integer", "boolean", "array", "object"}:
                result["type"] = lower_type
            elif lower_type == "null":
                result["type"] = "string"
                _append_schema_hint(result, "nullable")
            else:
                result.pop("type", None)

    if "anyOf" in result or "oneOf" in result:
        union_key = "anyOf" if "anyOf" in result else "oneOf"
        union_items = result.get(union_key) or []
        cleaned_items = [
            item for item in (
                _clean_parameters_json_schema(item, root_schema, visited)
                for item in union_items
            )
            if isinstance(item, dict)
        ]
        enum_values = [
            item.get("const")
            for item in union_items
            if isinstance(item, dict) and item.get("const") not in ("", None)
        ]
        if enum_values and len(enum_values) == len(union_items):
            result["type"] = "string"
            result["enum"] = [str(v) for v in enum_values]
        else:
            preferred = next(
                (
                    item for item in cleaned_items
                    if item.get("type") in ("object", "array") or item.get("properties")
                ),
                None,
            )
            if preferred is None:
                preferred = next((item for item in cleaned_items if item.get("type") or item.get("enum")), None)
            if preferred:
                original_description = result.get("description")
                result.update(preferred)
                if original_description:
                    _append_schema_hint(result, original_description)
        result.pop("anyOf", None)
        result.pop("oneOf", None)

    if result.get("type") == "array":
        items = result.get("items")
        if isinstance(items, list):
            if items:
                result["items"] = _clean_parameters_json_schema(items[0], root_schema, visited)
                _append_schema_hint(result, "tuple schema simplified")
            else:
                result.pop("items", None)
        elif isinstance(items, dict):
            result["items"] = _clean_parameters_json_schema(items, root_schema, visited)

    validation_keys = {
        "default", "minLength", "maxLength", "minimum", "maximum",
        "minItems", "maxItems", "pattern", "format", "uniqueItems",
    }
    for key in list(result.keys()):
        if key in validation_keys:
            value = result.pop(key)
            if value not in (None, "", {}, []):
                _append_schema_hint(result, f"{key}: {json.dumps(value, ensure_ascii=False)}")

    unsupported_keys = {
        "title", "$schema", "$id", "$ref", "ref", "strict", "nullable",
        "exclusiveMaximum", "exclusiveMinimum", "additionalProperties",
        "allOf", "anyOf", "oneOf", "$defs", "definitions", "example",
        "examples", "readOnly", "writeOnly", "const", "additionalItems",
        "contains", "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType",
    }
    for key in list(result.keys()):
        if key in unsupported_keys or key.startswith("x-"):
            del result[key]

    if isinstance(result.get("properties"), dict):
        result["properties"] = {
            prop_name: _clean_parameters_json_schema(prop_schema, root_schema, visited)
            for prop_name, prop_schema in result["properties"].items()
        }

    if "properties" in result and "type" not in result:
        result["type"] = "object"

    # required 仅约束字段必须出现，null 也是合法取值；nullable 不等于可选。
    # 若把 nullable 属性（如 Pydantic v2 的 type: ["string", "null"]）从 required
    # 中剔除，上游 Gemini 会认为所有参数都非必填，频繁生成空参数 {} 的工具调用，
    # 因此保留原始接口契约中的 required 约束。
    if isinstance(result.get("required"), list):
        prop_names = set(result.get("properties", {}).keys()) if isinstance(result.get("properties"), dict) else None
        required = []
        for item in result["required"]:
            if not isinstance(item, str):
                continue
            if prop_names is not None and item not in prop_names:
                continue
            if item not in required:
                required.append(item)
        if required:
            result["required"] = required
        else:
            result.pop("required", None)

    return result


def _normalize_tools_for_internal_api(tools: Any) -> Any:
    if not isinstance(tools, list):
        return tools

    normalized_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            normalized_tools.append(tool)
            continue

        normalized_tool = tool.copy()
        declarations = normalized_tool.get("functionDeclarations")
        if declarations is None:
            declarations = normalized_tool.get("function_declarations")
        if isinstance(declarations, list):
            normalized_declarations = []
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    normalized_declarations.append(declaration)
                    continue

                normalized_declaration = declaration.copy()
                if "parametersJsonSchema" in normalized_declaration:
                    schema = normalized_declaration["parametersJsonSchema"]
                elif "parameters_json_schema" in normalized_declaration:
                    schema = normalized_declaration.pop("parameters_json_schema", None)
                else:
                    schema = normalized_declaration.pop("parameters", None)

                normalized_declaration.pop("parameters", None)
                normalized_declaration.pop("parameters_json_schema", None)
                if schema not in (None, {}, []):
                    normalized_declaration["parametersJsonSchema"] = _clean_parameters_json_schema(schema)
                else:
                    normalized_declaration.pop("parametersJsonSchema", None)

                normalized_declarations.append(normalized_declaration)

            normalized_tool.pop("function_declarations", None)
            normalized_tool["functionDeclarations"] = normalized_declarations

        normalized_tools.append(normalized_tool)

    return normalized_tools


def _stable_json(value: Any) -> Optional[str]:
    """稳定序列化（排序键），失败返回 None"""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _deterministic_tool_id(material: str) -> str:
    """基于稳定材料确定性派生 toolu_ 前缀 ID（截断 32 hex）。

    同一段历史在多轮往返中补全出的 ID 恒定不变，保证 Claude 路径下
    KV 缓存依赖的前缀 token 序列字节级一致（随机 ID 会使前缀哈希
    从缺失处开始全部改变，导致上游缓存彻底失效）。
    """
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"toolu_{digest[:32]}"


def _parse_size_to_image_config(size_str: str) -> Dict[str, str]:
    """
    解析用户传入的 size 参数为 Gemini imageConfig 参数

    支持格式: "1024x1536", "1024*1536", "1024X1536"

    Returns:
        包含 aspectRatio 和/或 imageSize 的字典
    """
    config = {}
    size_str = size_str.strip()

    match = re.match(r"^(\d+)\s*[xX*×]\s*(\d+)$", size_str)
    if not match:
        return config

    width, height = int(match.group(1)), int(match.group(2))

    if width <= 0 or height <= 0:
        return config

    # 计算最接近的支持宽高比
    target_ratio = width / height
    best_ratio = None
    best_diff = float("inf")
    for w, h in SUPPORTED_ASPECT_RATIOS:
        diff = abs(target_ratio - w / h)
        if diff < best_diff:
            best_diff = diff
            best_ratio = f"{w}:{h}"
    if best_ratio:
        config["aspectRatio"] = best_ratio

    # 根据最大边长确定 imageSize（使用最接近的档位）
    max_dim = max(width, height)
    if max_dim <= 1280:
        config["imageSize"] = "1K"
    elif max_dim <= 2560:
        config["imageSize"] = "2K"
    else:
        config["imageSize"] = "4K"

    return config


def _enforce_uppercase_schema_types(schema: Any) -> None:
    """递归将 schema 的 type 字段值转为大写（官方 HAR 全工具采用大写类型标记 OBJECT/STRING/...）。

    注意：仅转换 type 键的值，其他字段（如 enum 值、format）保持原样。
    """
    if isinstance(schema, dict):
        type_value = schema.get("type")
        if isinstance(type_value, str):
            schema["type"] = type_value.upper()
        for key in ("properties",):
            if isinstance(schema.get(key), dict):
                for prop_schema in schema[key].values():
                    _enforce_uppercase_schema_types(prop_schema)
        if isinstance(schema.get("items"), (dict, list)):
            _enforce_uppercase_schema_types(schema["items"])
    elif isinstance(schema, list):
        for item in schema:
            _enforce_uppercase_schema_types(item)


def normalize_antigravity_request(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

