"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
import base64
import copy
import json
import re
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from pypinyin import lazy_pinyin
from log import log

from collections import OrderedDict
from typing import Union
from pypinyin import Style
_STREAM_TOOL_INDEX = OrderedDict()
_STREAM_TOOL_INDEX_MAX_ENTRIES = 512

def _next_stream_tool_call_indices(response_id: str, count: int) -> List[int]:
    """为本 chunk 内的 count 个工具调用分配连续的流内序号。"""
    base = _STREAM_TOOL_INDEX.get(response_id, 0)
    _STREAM_TOOL_INDEX[response_id] = base + count
    _STREAM_TOOL_INDEX.move_to_end(response_id)
    while len(_STREAM_TOOL_INDEX) > _STREAM_TOOL_INDEX_MAX_ENTRIES:
        _STREAM_TOOL_INDEX.popitem(last=False)
    return list(range(base, base + count))


def _convert_usage_metadata(usage_metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    将Gemini的usageMetadata转换为OpenAI格式的usage字段

    语义对齐（Gemini -> OpenAI）:
    - Gemini promptTokenCount 已包含缓存 token -> OpenAI prompt_tokens 同样包含缓存 token
    - Gemini candidatesTokenCount 不包含推理 token -> OpenAI completion_tokens 包含推理 token
      （completion_tokens = candidatesTokenCount + thoughtsTokenCount）
    - 明细字段: prompt_tokens_details.cached_tokens / completion_tokens_details.reasoning_tokens

    Args:
        usage_metadata: Gemini API的usageMetadata字段

    Returns:
        OpenAI格式的usage字典，如果没有usage数据则返回None
    """
    if not usage_metadata:
        return None

    prompt_tokens = int(usage_metadata.get("promptTokenCount", 0) or 0)
    cached_tokens = int(usage_metadata.get("cachedContentTokenCount", 0) or 0)
    reasoning_tokens = int(usage_metadata.get("thoughtsTokenCount", 0) or 0)
    completion_tokens = (
        int(usage_metadata.get("candidatesTokenCount", 0) or 0) + reasoning_tokens
    )
    total_tokens = int(
        usage_metadata.get(
            "totalTokenCount",
            prompt_tokens + completion_tokens,
        )
        or 0
    )

    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

    if cached_tokens > 0:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}

    if reasoning_tokens > 0:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}

    return usage


def _build_message_with_reasoning(role: str, content: str, reasoning_content: str) -> dict:
    """构建包含可选推理内容的消息对象"""
    message = {"role": role, "content": content}

    # 如果有thinking tokens，添加reasoning_content
    if reasoning_content:
        message["reasoning_content"] = reasoning_content

    return message


def _map_finish_reason(gemini_reason: str) -> str:
    """
    将Gemini结束原因映射到OpenAI结束原因

    Args:
        gemini_reason: 来自Gemini API的结束原因

    Returns:
        OpenAI兼容的结束原因
    """
    if gemini_reason == "STOP":
        return "stop"
    elif gemini_reason == "MAX_TOKENS":
        return "length"
    elif gemini_reason in ["SAFETY", "RECITATION"]:
        return "content_filter"
    else:
        # 对于 None 或未知的 finishReason，返回 "stop" 作为默认值
        # 避免返回 None 导致 MCP 客户端误判为响应未完成而循环调用
        return "stop"


def _normalize_function_name(name: str) -> str:
    """
    规范化函数名以符合 Gemini API 要求

    规则：
    - 必须以字母或下划线开头
    - 只能包含 a-z, A-Z, 0-9, 下划线, 英文句点, 英文短划线
    - 最大长度 64 个字符

    转换策略：
    1. 中文字符转换为拼音
    2. 将非法字符替换为下划线
    3. 如果以非字母/下划线开头，添加下划线前缀
    4. 截断到 64 个字符

    Args:
        name: 原始函数名

    Returns:
        规范化后的函数名
    """
    if not name:
        return "_unnamed_function"

    # 步骤1：转换中文字符为拼音
    if re.search(r"[\u4e00-\u9fff]", name):
        try:
            parts = []
            for char in name:
                if "\u4e00" <= char <= "\u9fff":
                    # 中文字符转换为拼音
                    pinyin = lazy_pinyin(char, style=Style.NORMAL)
                    parts.append("".join(pinyin))
                else:
                    parts.append(char)
            normalized = "".join(parts)
        except ImportError:
            log.warning("pypinyin not installed, cannot convert Chinese characters to pinyin")
            normalized = name
    else:
        normalized = name

    # 步骤2：将非法字符替换为下划线
    # 合法字符：a-z, A-Z, 0-9, _, ., -
    normalized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", normalized)

    # 步骤3：确保以字母或下划线开头
    if normalized and not (normalized[0].isalpha() or normalized[0] == "_"):
        # 以数字、点或短横线开头，添加下划线前缀
        normalized = "_" + normalized

    # 步骤4：截断到 64 个字符
    if len(normalized) > 64:
        normalized = normalized[:64]

    # 步骤5：确保不为空
    if not normalized:
        normalized = "_unnamed_function"

    return normalized


def _resolve_ref(ref: str, root_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    解析 $ref 或 ref 引用
    
    Args:
        ref: 引用路径，如 "#/definitions/MyType" 或 "#/$defs/MyType"
        root_schema: 根 schema 对象
        
    Returns:
        解析后的 schema，如果失败返回 None
    """
    if not isinstance(ref, str):
        return None
        
    if not ref.startswith('#/'):
        # 尝试在 definitions 或 $defs 中查找
        for key in ["definitions", "$defs"]:
            if key in root_schema and ref in root_schema[key]:
                return root_schema[key][ref]
        return None
    
    path = ref[2:].split('/')
    current = root_schema
    
    for segment in path:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    
    return current if isinstance(current, dict) else None


def _clean_schema_for_claude(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    清理 JSON Schema，转换为 Claude API 支持的格式（符合 JSON Schema draft 2020-12）

    处理逻辑：
    1. 解析 $ref 引用
    2. 合并 allOf 中的 schema
    3. 转换 anyOf 为更兼容的格式
    4. 保持标准 JSON Schema 类型（不转换为大写）
    5. 处理 array 的 items
    6. 清理 Claude 不支持的字段

    Args:
        schema: JSON Schema 对象
        root_schema: 根 schema（用于解析 $ref）
        visited: 已访问的对象集合（防止循环引用）

    Returns:
        清理后的 schema
    """
    # 非字典类型直接返回
    if not isinstance(schema, dict):
        return schema

    # 初始化
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    # 防止循环引用
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)

    # 创建副本避免修改原对象
    result = {}

    # 1. 处理 $ref
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_schema)
        if resolved:
            result = copy.deepcopy(resolved)
            for key, value in schema.items():
                if key != "$ref":
                    result[key] = value
            schema = result
            result = {}

    # 2. 处理 allOf（合并所有 schema）
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_claude(item, root_schema, visited)

            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])

            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])

            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value

        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        result = dict(schema)

    # 3. 处理 type 数组（如 ["string", "null"]）
    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            # Claude 支持 type 数组，保持不变
            pass

    # 4. 处理 array 的 items
    if result.get("type") == "array":
        if "items" not in result:
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple 定义，检查是否所有元素类型相同
            tuple_items = result["items"]
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)

            if is_homogeneous and first_type:
                result["items"] = _clean_schema_for_claude(tuple_items[0], root_schema, visited)
            else:
                # 异质元组，使用 anyOf 表示
                result["items"] = {
                    "anyOf": [_clean_schema_for_claude(item, root_schema, visited) for item in tuple_items]
                }
        else:
            result["items"] = _clean_schema_for_claude(result["items"], root_schema, visited)

    # 5. 处理 anyOf（保持 anyOf，递归清理）
    if "anyOf" in result:
        result["anyOf"] = [_clean_schema_for_claude(item, root_schema, visited) for item in result["anyOf"]]

    # 6. 清理 Claude 不支持的字段（根据 JSON Schema 2020-12）
    # Claude API 对某些字段比较严格，移除可能导致问题的字段
    unsupported_keys = {
        "title", "$schema", "strict",
        "additionalItems",  # 废弃字段，使用 items 替代
        "exclusiveMaximum", "exclusiveMinimum",  # 在 2020-12 中这些应该是数值而非布尔值
        "$defs", "definitions",  # 移除 definitions 相关字段避免冲突
        "example", "examples", "readOnly", "writeOnly",
        "const",  # const 可能导致问题
        "contentEncoding", "contentMediaType",
        "oneOf",  # oneOf 可能导致问题，用 anyOf 替代
        "patternProperties", "dependencies", "propertyNames",  # Google API 不支持
    }

    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]

    # 递归处理 additionalProperties（如果存在）
    if "additionalProperties" in result and isinstance(result["additionalProperties"], dict):
        result["additionalProperties"] = _clean_schema_for_claude(result["additionalProperties"], root_schema, visited)

    # 7. 递归处理 properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_claude(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props

    # 8. 确保有 type 字段（如果有 properties 但没有 type）
    if "properties" in result and "type" not in result:
        result["type"] = "object"

    # 9. 去重 required 数组
    if "required" in result and isinstance(result["required"], list):
        result["required"] = list(dict.fromkeys(result["required"]))

    return result


def _clean_schema_for_gemini(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    清理 JSON Schema，转换为 Gemini 支持的格式

    参考 worker.mjs 的 transformOpenApiSchemaToGemini 实现

    处理逻辑：
    1. 解析 $ref 引用
    2. 合并 allOf 中的 schema
    3. 转换 anyOf 为 enum（如果可能）
    4. 类型映射（string -> STRING）
    5. 处理 ARRAY 的 items（包括 Tuple）
    6. 将 default 值移到 description
    7. 清理不支持的字段

    Args:
        schema: JSON Schema 对象
        root_schema: 根 schema（用于解析 $ref）
        visited: 已访问的对象集合（防止循环引用）

    Returns:
        清理后的 schema
    """
    # 非字典类型直接返回
    if not isinstance(schema, dict):
        return schema
    
    # 初始化
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()
    
    # 防止循环引用
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)
    
    # 创建副本避免修改原对象
    result = {}
    
    # 1. 处理 $ref 或 ref
    ref_key = "$ref" if "$ref" in schema else ("ref" if "ref" in schema else None)
    if ref_key:
        resolved = _resolve_ref(schema[ref_key], root_schema)
        if resolved:
            # 检测循环引用
            resolved_id = id(resolved)
            if resolved_id in visited:
                return {"type": "OBJECT", "description": "(circular reference)"}
            
            visited.add(resolved_id)
            # 合并解析后的 schema
            merged = dict(resolved)
            
            # 重要：根据 Gemini API 限制，当存在引用时，只能并列存在 description 和 default
            # 其他字段（如 type, properties 等）必须丢弃，否则会触发 400 错误
            for key in ["description", "default"]:
                if key in schema:
                    merged[key] = schema[key]
            
            schema = merged
            result = {}
    
    # 2. 处理 allOf（合并所有 schema）
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_gemini(item, root_schema, visited)
            
            # 合并 properties
            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])
            
            # 合并 required
            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])
            
            # 合并其他字段（简单覆盖）
            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value
        
        # 复制其他字段
        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        # 复制所有字段
        result = dict(schema)
    
    # 3. 类型映射（转换为大写）
    # 注意：Gemini API 的 type 字段必须是字符串，不能是数组
    if "type" in result:
        type_value = result["type"]

        # 如果 type 是列表，提取主要类型（非 null）
        if isinstance(type_value, list):
            primary_type = next((t for t in type_value if t != "null"), None)
            type_value = primary_type if primary_type else "STRING"  # 默认为 STRING

        # 类型映射
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        if isinstance(type_value, str) and type_value.lower() in type_map:
            # 确保 result["type"] 是字符串而不是列表
            result["type"] = type_map[type_value.lower()]
        else:
            # 未知类型，删除该字段
            del result["type"]
    
    # 4. 处理 ARRAY 的 items
    if result.get("type") == "ARRAY":
        if "items" not in result:
            # 没有 items，默认允许任意类型
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple 定义（items 是数组）
            tuple_items = result["items"]
            
            # 提取类型信息用于 description
            tuple_types = [item.get("type", "any") for item in tuple_items]
            tuple_desc = f"(Tuple: [{', '.join(tuple_types)}])"
            
            original_desc = result.get("description", "")
            result["description"] = f"{original_desc} {tuple_desc}".strip()
            
            # 检查是否所有元素类型相同
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)
            
            if is_homogeneous and first_type:
                # 同质元组，转换为 List<Type>
                result["items"] = _clean_schema_for_gemini(tuple_items[0], root_schema, visited)
            else:
                # 异质元组，Gemini 不支持，设为 {}
                result["items"] = {}
        else:
            # 递归处理 items
            result["items"] = _clean_schema_for_gemini(result["items"], root_schema, visited)
    
    # 5. 处理 anyOf（尝试转换为 enum）
    if "anyOf" in result:
        any_of_schemas = result["anyOf"]
        
        # 递归处理每个 schema
        cleaned_any_of = [_clean_schema_for_gemini(item, root_schema, visited) for item in any_of_schemas]
        
        # 尝试提取 enum
        if all("const" in item for item in cleaned_any_of):
            enum_values = [
                str(item["const"]) 
                for item in cleaned_any_of 
                if item.get("const") not in ["", None]
            ]
            if enum_values:
                result["type"] = "STRING"
                result["enum"] = enum_values
        elif "type" not in result:
            # 如果不是 enum，尝试取第一个有效的类型定义
            first_valid = next((item for item in cleaned_any_of if item.get("type") or item.get("enum")), None)
            if first_valid:
                result.update(first_valid)
        
        # 删除 anyOf
        del result["anyOf"]
    
    # 6. 将 default 值移到 description
    if "default" in result:
        default_value = result["default"]
        original_desc = result.get("description", "")
        result["description"] = f"{original_desc} (Default: {json.dumps(default_value)})".strip()
        del result["default"]
    
    # 7. 清理不支持的字段
    unsupported_keys = {
        "title", "$schema", "$ref", "ref", "strict", "exclusiveMaximum",
        "exclusiveMinimum", "additionalProperties", "oneOf", "allOf",
        "$defs", "definitions", "example", "examples", "readOnly",
        "writeOnly", "const", "additionalItems", "contains",
        "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType"
    }
    
    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]
    
    # 8. 递归处理 properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_gemini(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props
    
    # 9. 确保有 type 字段（如果有 properties 但没有 type）
    if "properties" in result and "type" not in result:
        result["type"] = "OBJECT"
    
    # 10. 去重 required 数组
    if "required" in result and isinstance(result["required"], list):
        result["required"] = list(dict.fromkeys(result["required"]))  # 保持顺序去重

    # Gemini OpenAPI 规范要求：enum 元素必须为 TYPE_STRING
    if "enum" in result:
        enum_val = result.get("enum")
        if isinstance(enum_val, list):
            if result.get("type") == "BOOLEAN" or any(isinstance(x, bool) for x in enum_val):
                result.pop("enum", None)
            else:
                result["enum"] = [str(x) for x in enum_val]

    return result


def _append_schema_hint(schema: Dict[str, Any], hint: str) -> None:
    """把不兼容的校验信息挪到 description 里，避免上游直接拒收。"""
    if not hint:
        return
    desc = schema.get("description")
    schema["description"] = f"{desc} ({hint})" if desc else hint


def _clean_schema_for_parameters_json_schema(
    schema: Any,
    root_schema: Optional[Dict[str, Any]] = None,
    visited: Optional[set] = None,
) -> Any:
    """
    清理 JSON Schema，供 Gemini CLI 内部接口的 parametersJsonSchema 使用。

    Code Assist 的内部接口更接近官方 Gemini CLI：工具参数应放在
    parametersJsonSchema 中，并保持 JSON Schema 的小写 type。
    """
    if not isinstance(schema, dict):
        return schema

    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        return {"type": "object", "description": "(circular reference)"}
    visited.add(schema_id)

    result: Dict[str, Any]

    ref_key = "$ref" if "$ref" in schema else ("ref" if "ref" in schema else None)
    if ref_key:
        resolved = _resolve_ref(schema[ref_key], root_schema)
        if resolved:
            result = copy.deepcopy(resolved)
            for key in ("description", "default"):
                if key in schema:
                    result[key] = schema[key]
            schema = result

    if "allOf" in schema:
        result = {}
        for item in schema.get("allOf") or []:
            cleaned_item = _clean_schema_for_parameters_json_schema(item, root_schema, visited)
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

    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            non_null_types = [t for t in type_value if isinstance(t, str) and t.lower() != "null"]
            if non_null_types:
                result["type"] = non_null_types[0]
                if "null" in [str(t).lower() for t in type_value]:
                    _append_schema_hint(result, "nullable")
            else:
                result["type"] = "string"
        elif isinstance(type_value, str):
            lower_type = type_value.lower()
            if lower_type in {"string", "number", "integer", "boolean", "array", "object", "null"}:
                result["type"] = "string" if lower_type == "null" else lower_type
            else:
                del result["type"]

    if "anyOf" in result or "oneOf" in result:
        union_key = "anyOf" if "anyOf" in result else "oneOf"
        union_items = result.get(union_key) or []
        cleaned_items = [
            item for item in (
                _clean_schema_for_parameters_json_schema(item, root_schema, visited)
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
                existing_description = result.get("description")
                result.update(preferred)
                if existing_description:
                    _append_schema_hint(result, existing_description)
        result.pop("anyOf", None)
        result.pop("oneOf", None)

    if result.get("type") == "array":
        items = result.get("items")
        if isinstance(items, list):
            if items:
                result["items"] = _clean_schema_for_parameters_json_schema(items[0], root_schema, visited)
                _append_schema_hint(result, "tuple schema simplified")
            else:
                result.pop("items", None)
        elif isinstance(items, dict):
            result["items"] = _clean_schema_for_parameters_json_schema(items, root_schema, visited)

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
        "title", "$schema", "$id", "$ref", "ref", "strict",
        "exclusiveMaximum", "exclusiveMinimum", "additionalProperties",
        "allOf", "anyOf", "oneOf", "$defs", "definitions", "example",
        "examples", "readOnly", "writeOnly", "const", "additionalItems",
        "contains", "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType",
    }
    for key in list(result.keys()):
        if key in unsupported_keys or key.startswith("x-"):
            del result[key]

    if "properties" in result and isinstance(result["properties"], dict):
        cleaned_props = {
            prop_name: _clean_schema_for_parameters_json_schema(prop_schema, root_schema, visited)
            for prop_name, prop_schema in result["properties"].items()
        }
        result["properties"] = cleaned_props

    if "properties" in result and "type" not in result:
        result["type"] = "object"

    # required 仅约束字段必须出现，null 也是合法取值；nullable 不等于可选。
    # 若把 nullable 属性（如 Pydantic v2 的 type: ["string", "null"]）从 required
    # 中剔除，上游 Gemini 会认为所有参数都非必填，频繁生成空参数 {} 的工具调用，
    # 因此保留原始接口契约中的 required 约束。
    if "required" in result and isinstance(result["required"], list):
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

    # Gemini OpenAPI 规范要求：enum 元素必须为 TYPE_STRING
    if "enum" in result:
        enum_val = result.get("enum")
        if isinstance(enum_val, list):
            if result.get("type") == "boolean" or any(isinstance(x, bool) for x in enum_val):
                result.pop("enum", None)
            else:
                result["enum"] = [str(x) for x in enum_val]

    return result


def fix_tool_call_args_types(
    args: Dict[str, Any],
    parameters_schema: Dict[str, Any]
) -> Dict[str, Any]:
    """
    根据工具的参数 schema 修正函数调用参数的类型
    
    例如：将字符串 "5" 转换为数字 5，根据 schema 中的 type 定义
    
    Args:
        args: 函数调用的参数字典
        parameters_schema: 工具定义中的 parameters schema
        
    Returns:
        类型修正后的参数字典
    """
    if not args or not parameters_schema:
        return args
    
    properties = parameters_schema.get("properties", {})
    if not properties:
        return args
    
    fixed_args = {}
    for key, value in args.items():
        if key not in properties:
            # 参数不在 schema 中，保持原样
            fixed_args[key] = value
            continue
        
        param_schema = properties[key]
        param_type = param_schema.get("type")
        
        # 根据 schema 中的类型修正参数值
        if param_type == "number" or param_type == "integer":
            # 如果值是字符串，尝试转换为数字
            if isinstance(value, str):
                try:
                    if param_type == "integer":
                        fixed_args[key] = int(value)
                    else:
                        # 尝试转换为 float，如果是整数则保持为 int
                        num_value = float(value)
                        fixed_args[key] = int(num_value) if num_value.is_integer() else num_value
                    log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} '{value}' -> {fixed_args[key]} ({param_type})")
                except (ValueError, AttributeError):
                    # 转换失败，保持原样
                    fixed_args[key] = value
                    log.warning(f"[OPENAI2GEMINI] 无法将参数 {key} 的值 '{value}' 转换为 {param_type}")
            else:
                fixed_args[key] = value
        elif param_type == "boolean":
            # 如果值是字符串，转换为布尔值
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    fixed_args[key] = True
                elif value.lower() in ("false", "0", "no"):
                    fixed_args[key] = False
                else:
                    fixed_args[key] = value
                if fixed_args[key] != value:
                    log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} '{value}' -> {fixed_args[key]} (boolean)")
            else:
                fixed_args[key] = value
        elif param_type == "string":
            # 如果值不是字符串，转换为字符串
            if not isinstance(value, str):
                fixed_args[key] = str(value)
                log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} {value} -> '{fixed_args[key]}' (string)")
            else:
                fixed_args[key] = value
        else:
            # 其他类型（array, object 等）保持原样
            fixed_args[key] = value
    
    return fixed_args


def convert_openai_tools_to_gemini(openai_tools: List, model: str = "") -> List[Dict[str, Any]]:
    """
    将 OpenAI tools 格式转换为 Gemini functionDeclarations 格式

    Args:
        openai_tools: OpenAI 格式的工具列表（可能是字典或 Pydantic 模型）
        model: 模型名称（用于判断是否为 Claude 模型）

    Returns:
        Gemini 格式的工具列表
    """
    if not openai_tools:
        return []

    # 判断是否为 Claude 模型
    is_claude_model = "claude" in model.lower()

    function_declarations = []

    for tool in openai_tools:
        if tool.get("type") != "function":
            log.warning(f"Skipping non-function tool type: {tool.get('type')}")
            continue

        function = tool.get("function")
        if not function:
            log.warning("Tool missing 'function' field")
            continue

        # 获取并规范化函数名
        original_name = function.get("name")
        if not original_name:
            log.warning("Tool missing 'name' field, using default")
            original_name = "_unnamed_function"

        normalized_name = _normalize_function_name(original_name)

        # 如果名称被修改了，记录日志
        if normalized_name != original_name:
            log.debug(f"Function name normalized: '{original_name}' -> '{normalized_name}'")

        # 构建 Gemini function declaration
        declaration = {
            "name": normalized_name,
            "description": function.get("description", ""),
        }

        # 添加参数（如果有）- Gemini CLI 内部接口更适合 parametersJsonSchema
        if "parameters" in function:
            if is_claude_model:
                cleaned_params = _clean_schema_for_parameters_json_schema(function["parameters"])
                log.debug(f"[OPENAI2GEMINI] Using Claude schema cleaning for tool: {normalized_name}")
            else:
                cleaned_params = _clean_schema_for_parameters_json_schema(function["parameters"])

            if cleaned_params:
                declaration["parametersJsonSchema"] = cleaned_params
            elif is_claude_model:
                declaration["parametersJsonSchema"] = {"type": "object", "properties": {}}
        elif is_claude_model:
            declaration["parametersJsonSchema"] = {"type": "object", "properties": {}}

        function_declarations.append(declaration)

    if not function_declarations:
        return []

    # Gemini 格式：工具数组中包含 functionDeclarations
    return [{"functionDeclarations": function_declarations}]


def convert_tool_choice_to_tool_config(
    tool_choice: Union[str, Dict[str, Any]],
    tool_names: Optional[set] = None,
) -> Dict[str, Any]:
    """
    将 OpenAI tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: OpenAI 格式的 tool_choice

    Returns:
        Gemini 格式的 toolConfig
    """
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "required":
            if tool_names is not None and not tool_names:
                raise ValueError("tool_choice 'required' needs at least one function tool")
            return {"functionCallingConfig": {"mode": "ANY"}}
    elif isinstance(tool_choice, dict):
        # {"type": "function", "function": {"name": "my_function"}}
        function = tool_choice.get("function")
        if tool_choice.get("type") == "function" and isinstance(function, dict):
            function_name = function.get("name")
            if isinstance(function_name, str) and function_name:
                if tool_names is not None and function_name not in tool_names:
                    raise ValueError(
                        f"tool_choice refers to an undeclared function: {function_name}"
                    )
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [_normalize_function_name(function_name)],
                    }
                }

    raise ValueError("Unsupported tool_choice for Antigravity")


def _reverse_transform_value(value: Any) -> Any:
    """
    将值转换回原始类型（Gemini 可能将所有值转为字符串）

    仅处理 Gemini 在工具参数中常见的布尔/空值字符串化情况，
    不再对数字字符串做启发式转换，避免把 schema 声明为 string
    的参数错误还原成 integer。
    
    参考 worker.mjs 的 reverseTransformValue
    
    Args:
        value: 要转换的值
        
    Returns:
        转换后的值
    """
    if not isinstance(value, str):
        return value
    
    # 布尔值
    if value == 'true':
        return True
    if value == 'false':
        return False
    
    # null
    if value == 'null':
        return None
    
    # 其他情况保持字符串
    return value


def _reverse_transform_args(args: Any) -> Any:
    """
    递归转换函数参数，将字符串转回原始类型
    
    参考 worker.mjs 的 reverseTransformArgs
    
    Args:
        args: 函数参数（可能是字典、列表或其他类型）
        
    Returns:
        转换后的参数
    """
    if not isinstance(args, (dict, list)):
        return args
    
    if isinstance(args, list):
        return [_reverse_transform_args(item) for item in args]
    
    # 处理字典
    result = {}
    for key, value in args.items():
        if isinstance(value, (dict, list)):
            result[key] = _reverse_transform_args(value)
        else:
            result[key] = _reverse_transform_value(value)
    
    return result


def extract_images_from_content(content: Any) -> Dict[str, Any]:
    """
    从 OpenAI content 中提取文本和图片
    
    Args:
        content: OpenAI 消息的 content 字段（可能是字符串或列表）
    
    Returns:
        包含 text 和 images 的字典
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result["text"] += item.get("text", "")
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    # 解析 data:image/png;base64,xxx 格式
                    if image_url.startswith("data:image/"):
                        match = re.match(r"^data:image/(\w+);base64,(.+)$", image_url)
                        if match:
                            mime_type = match.group(1)
                            base64_data = match.group(2)
                            result["images"].append({
                                "inlineData": {
                                    "mimeType": f"image/{mime_type}",
                                    "data": base64_data
                                }
                            })

    return result


def convert_openai_to_gemini_request(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

def convert_gemini_to_openai_response(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

def convert_gemini_to_openai_stream(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

