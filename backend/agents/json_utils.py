import json
import re
from typing import Type
from pydantic import BaseModel


def extract_json(raw: str) -> dict:
    """Extract a JSON object from raw LLM output.

    Handles:
    - Reasoning tags (<analysis>...</analysis>, <think>...</think>) from models like ornith:9b
    - Markdown code fences (```json ... ```)
    - Leading/trailing prose around the JSON
    - Nested braces
    """
    if not raw:
        return {}

    # Strip reasoning tags that some models (ornith:9b) put inline.
    raw = re.sub(r'<(?:analysis|think|reflection)>.*?</(?:analysis|think|reflection)>', '', raw, flags=re.DOTALL)

    fence_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r'\{[\s\S]*\}', raw)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            for i in range(len(brace_match.group(0)), 0, -1):
                fragment = brace_match.group(0)[:i]
                if fragment.rstrip().endswith('}'):
                    try:
                        return json.loads(fragment)
                    except json.JSONDecodeError:
                        continue

    return {}


def _resolve_ref(ref: str, root_schema: dict) -> dict:
    """Resolve a $ref like '#/$defs/CausalChain' to its schema dict."""
    parts = ref.lstrip("#/").split("/")
    node = root_schema
    for part in parts:
        node = node.get(part, {})
    return node


def _describe_field(field_info: dict, root_schema: dict, indent: int = 1) -> str:
    """Describe a single field's type, expanding nested objects."""
    pad = "  " * indent

    if "$ref" in field_info:
        nested = _resolve_ref(field_info["$ref"], root_schema)
        return _describe_object(nested, root_schema, indent)

    field_type = field_info.get("type", "any")

    if field_type == "array":
        items = field_info.get("items", {})
        if "$ref" in items:
            nested = _resolve_ref(items["$ref"], root_schema)
            inner = _describe_object(nested, root_schema, indent + 1)
            return f"[\n{inner}\n{pad}]"
        item_type = items.get("type", "any")
        if "enum" in items:
            item_type = f"one of {items['enum']}"
        return f"[<{item_type}>]"

    if field_type == "object":
        return _describe_object(field_info, root_schema, indent)

    if field_type == "string" and "enum" in field_info:
        return f"one of {field_info['enum']}"

    return f"<{field_type}>"


def _describe_object(obj_schema: dict, root_schema: dict, indent: int) -> str:
    """Describe an object schema with its fields."""
    pad = "  " * indent

    if "$ref" in obj_schema:
        obj_schema = _resolve_ref(obj_schema["$ref"], root_schema)

    props = obj_schema.get("properties", {})
    if not props:
        return f"{pad}{{}}"

    required = set(obj_schema.get("required", []))
    lines = [f"{pad}{{"]
    for fname, finfo in props.items():
        if fname == "agent":
            continue
        type_desc = _describe_field(finfo, root_schema, indent + 1)
        req = "(required)" if fname in required else "(optional)"
        default = finfo.get("default")
        if default is not None:
            req += f" default: {json.dumps(default)}"
        desc = finfo.get("description", "")
        if desc:
            req += f" — {desc}"
        lines.append(f'{pad}  "{fname}": {type_desc} {req}')
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def schema_hint(model: Type[BaseModel]) -> str:
    """Generate a human-readable JSON schema description for LLM prompts.

    Shows the exact field names, types, and defaults the model must produce,
    recursively expanding nested objects and arrays.
    """
    schema = model.model_json_schema()
    return _describe_object(schema, schema, 0)


def schema_for_prompt(model: Type[BaseModel]) -> str:
    """Full prompt block showing the required JSON structure."""
    return (
        f"\n\nYou MUST respond with ONLY a valid JSON object matching this exact structure "
        f"(no markdown, no code fences, no prose before or after). "
        f"Use the EXACT field names shown:\n"
        f"{schema_hint(model)}"
    )
