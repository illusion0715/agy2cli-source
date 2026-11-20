"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
from __future__ import annotations
import copy
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from log import log

DEFAULT_TEMPERATURE = 0.4

def _cached_content_token_count(usage_metadata: Any) -> int:
    if not isinstance(usage_metadata, dict):
        return 0
    return int(usage_metadata.get("cachedContentTokenCount", 0) or 0)


def _anthropic_usage_from_metadata(usage_metadata: Any) -> Dict[str, Any]:
    """
    将 Gemini usageMetadata 转换为 Claude usage 字段

    语义对齐（Gemini -> Claude）:
    - Gemini promptTokenCount 包含缓存 token -> Claude input_tokens 不含缓存
      （input_tokens = promptTokenCount - cachedContentTokenCount）
    - Gemini candidatesTokenCount 不含推理 token -> Claude output_tokens 包含推理 token
      （output_tokens = candidatesTokenCount + thoughtsTokenCount）
    - 明细: cache_read_input_tokens / output_tokens_details.thinking_tokens
    """
    if not isinstance(usage_metadata, dict):
        return {"input_tokens": 0, "output_tokens": 0}

    prompt_tokens_total = int(usage_metadata.get("promptTokenCount", 0) or 0)
    cached_tokens = _cached_content_token_count(usage_metadata)
    reasoning_tokens = int(usage_metadata.get("thoughtsTokenCount", 0) or 0)
    usage: Dict[str, Any] = {
        "input_tokens": max(prompt_tokens_total - cached_tokens, 0),
        "output_tokens": (
            int(usage_metadata.get("candidatesTokenCount", 0) or 0)
            + reasoning_tokens
        ),
    }

    if cached_tokens > 0:
        usage["cache_read_input_tokens"] = cached_tokens

    if reasoning_tokens > 0:
        usage["output_tokens_details"] = {"thinking_tokens": reasoning_tokens}

    return usage


def _is_non_whitespace_text(value: Any) -> bool:
    """
    判断文本是否包含"非空白"内容。

    说明：下游（Antigravity/Claude 兼容层）会对纯 text 内容块做校验：
    - text 不能为空字符串
    - text 不能仅由空白字符（空格/换行/制表等）组成
    """
    if value is None:
        return False
    try:
        return bool(str(value).strip())
    except Exception:
        return False


def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    递归移除 dict/list 中值为 null/None 的字段/元素。

    背景：Roo/Kilo 在 Anthropic native tool 路径下，若收到 tool_use.input 中包含 null，
    可能会把 null 当作真实入参执行（例如"在 null 中搜索"）。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = _remove_nulls_for_tool_input(v)
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if item is None:
                continue
            cleaned_list.append(_remove_nulls_for_tool_input(item))
        return cleaned_list

    return value


def clean_json_schema(schema: Any) -> Any:
    """
    清理 JSON Schema，移除下游不支持的字段，并把验证要求追加到 description。
    """
    if not isinstance(schema, dict):
        return schema

    # 下游不支持的字段
    unsupported_keys = {
        "$schema", "$id", "$ref", "$defs", "definitions", "title",
        "example", "examples", "readOnly", "writeOnly", "default",
        "exclusiveMaximum", "exclusiveMinimum", "oneOf", "anyOf", "allOf",
        "const", "additionalItems", "contains", "patternProperties",
        "dependencies", "propertyNames", "if", "then", "else",
        "contentEncoding", "contentMediaType", "nullable",
    }

    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }
    fields_to_remove = {"additionalProperties"}

    validations: List[str] = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported_keys or key in fields_to_remove or key in validation_fields:
            continue

        if key == "type" and isinstance(value, list):
            # type: ["string", "null"] -> type: "string", nullable: true
            has_null = any(
                isinstance(t, str) and t.strip() and t.strip().lower() == "null" for t in value
            )
            non_null_types = [
                t.strip()
                for t in value
                if isinstance(t, str) and t.strip() and t.strip().lower() != "null"
            ]

            cleaned[key] = non_null_types[0] if non_null_types else "string"
            continue

        if key == "description" and validations:
            cleaned[key] = f"{value} ({', '.join(validations)})"
        elif isinstance(value, dict):
            cleaned[key] = clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value

    if validations and "description" not in cleaned:
        cleaned["description"] = f"Validation: {', '.join(validations)}"

    # 如果有 properties 但没有显式 type，则补齐为 object
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    # required 与 nullable 正交：null 也是合法取值，nullable 不等于可选。
    # 保留原始接口契约中的 required 约束，避免上游把必填参数当作可选，
    # 进而在工具调用时生成空参数 {}。
    if (
        isinstance(schema.get("properties"), dict)
        and isinstance(cleaned.get("required"), list)
    ):
        prop_names = set(cleaned.get("properties", {}).keys()) \
            if isinstance(cleaned.get("properties"), dict) else None
        required = []
        for item in cleaned["required"]:
            if not isinstance(item, str):
                continue
            if prop_names is not None and item not in prop_names:
                continue
            if item not in required:
                required.append(item)
        if required:
            cleaned["required"] = required
        else:
            cleaned.pop("required", None)

    return cleaned


def convert_tools(anthropic_tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 Anthropic tools[] 转换为下游 tools（functionDeclarations）结构。
    """
    if not anthropic_tools:
        return None

    gemini_tools: List[Dict[str, Any]] = []
    for tool in anthropic_tools:
        name = tool.get("name", "nameless_function")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {}) or {}
        parameters = clean_json_schema(input_schema)

        gemini_tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": name,
                        "description": description,
                        "parametersJsonSchema": parameters,
                    }
                ]
            }
        )

    return gemini_tools or None


def _extract_tool_result_output(content: Any) -> str:
    """从 tool_result.content 中提取输出字符串"""
    if isinstance(content, list):
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return str(first.get("text", ""))
        return str(first)
    if content is None:
        return ""
    return str(content)


def reorganize_tool_messages(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    重新组织消息，满足 tool_use/tool_result 约束。
    """
    tool_results: Dict[str, Dict[str, Any]] = {}

    for msg in contents:
        for part in msg.get("parts", []) or []:
            if isinstance(part, dict) and "functionResponse" in part:
                tool_id = (part.get("functionResponse") or {}).get("id")
                if tool_id:
                    tool_results[str(tool_id)] = part

    flattened: List[Dict[str, Any]] = []
    for msg in contents:
        role = msg.get("role")
        for part in msg.get("parts", []) or []:
            flattened.append({"role": role, "parts": [part]})

    new_contents: List[Dict[str, Any]] = []
    i = 0
    while i < len(flattened):
        msg = flattened[i]
        part = msg["parts"][0]

        if isinstance(part, dict) and "functionResponse" in part:
            i += 1
            continue

        if isinstance(part, dict) and "functionCall" in part:
            tool_id = (part.get("functionCall") or {}).get("id")
            new_contents.append({"role": "model", "parts": [part]})

            if tool_id is not None and str(tool_id) in tool_results:
                new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})

            i += 1
            continue

        new_contents.append(msg)
        i += 1

    return new_contents


def convert_tool_choice_to_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: Anthropic 格式的 tool_choice
            - {"type": "auto"}: 模型自动决定是否使用工具
            - {"type": "any"}: 模型必须使用工具
            - {"type": "tool", "name": "tool_name"}: 模型必须使用指定工具

    Returns:
        Gemini 格式的 toolConfig，如果无效则返回 None
    """
    if not tool_choice:
        return None
    
    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        
        if choice_type == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif choice_type == "any":
            return {"functionCallingConfig": {"mode": "ANY"}}
        elif choice_type == "tool":
            tool_name = tool_choice.get("name")
            if tool_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [tool_name],
                    }
                }
    
    # 无效或不支持的 tool_choice，返回 None
    return None


def build_generation_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 Anthropic Messages 请求构造下游 generationConfig。

    Returns:
        generation_config: 生成配置字典
    """
    config: Dict[str, Any] = {
        "topP": 1,
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>",
        ],
    }

    temperature = payload.get("temperature", None)
    config["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature

    top_p = payload.get("top_p", None)
    if top_p is not None:
        config["topP"] = top_p

    top_k = payload.get("top_k", None)
    if top_k is not None:
        config["topK"] = top_k

    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens

    # 处理 extended thinking 参数 (plan mode)
    thinking = payload.get("thinking")
    is_plan_mode = False
    if thinking and isinstance(thinking, dict):
        thinking_type = thinking.get("type")
        budget_tokens = thinking.get("budget_tokens")
        
        # 如果启用了 extended thinking，设置 thinkingConfig
        if thinking_type == "enabled":
            is_plan_mode = True
            thinking_config: Dict[str, Any] = {}
            
            # 设置思考预算，默认使用较大的值以支持计划模式
            if budget_tokens is not None:
                thinking_config["thinkingBudget"] = budget_tokens
            else:
                # 默认给一个较大的思考预算以支持完整的计划生成
                thinking_config["thinkingBudget"] = 48000
            
            # 始终包含思考内容，这样才能看到计划
            thinking_config["includeThoughts"] = True
            
            config["thinkingConfig"] = thinking_config
            log.info(f"[ANTHROPIC2GEMINI] Extended thinking enabled with budget: {thinking_config['thinkingBudget']}")
        elif thinking_type == "disabled":
            # 明确禁用思考模式
            config["thinkingConfig"] = {
                "includeThoughts": False
            }
            log.info("[ANTHROPIC2GEMINI] Extended thinking explicitly disabled")

    stop_sequences = payload.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        config["stopSequences"] = config["stopSequences"] + [str(s) for s in stop_sequences]
    elif is_plan_mode:
        # Plan mode 时清空默认 stop sequences，避免过早停止
        # 默认的 stop sequences 可能会导致模型在生成计划时过早停止
        config["stopSequences"] = []
        log.info("[ANTHROPIC2GEMINI] Plan mode: cleared default stop sequences to prevent premature stopping")
    
    # 如果不是 plan mode 且没有自定义 stop_sequences，保持默认值
    # (默认值已经在 config 初始化时设置)

    return config


def anthropic_to_gemini_request(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

def gemini_to_anthropic_response(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

def gemini_stream_to_anthropic_stream(*args, **kwargs):
    raise NotImplementedError("This capability is intentionally unavailable in the archival snapshot")

