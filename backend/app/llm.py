"""Provider-agnostic LLM client. Every pipeline stage that needs an LLM call
(intent parsing, semantic predicate resolution, visual tagging) goes through
complete_text()/complete_vision() and never imports a provider SDK directly —
swapping providers is a config.LLM_PROVIDER change, not a per-call-site edit.

Default provider is Gemini (free tier, day-one build); Anthropic is the
documented fallback (see config.py, backend/README.md).
"""
import json

from app import config


def complete_text(system: str | None, user_content: str, max_tokens: int = 1000) -> str:
    if config.LLM_PROVIDER == "gemini":
        return _gemini_text(system, user_content, max_tokens)
    return _anthropic_text(system, user_content, max_tokens)


def complete_json(system: str | None, user_content: str, schema: dict, max_tokens: int = 1000):
    """Like complete_text, but the response is guaranteed to validate against
    `schema` (a plain JSON Schema dict) instead of being free text we then
    hope contains a parseable `{...}` somewhere. Both providers support
    schema-enforced structured output natively; use it instead of manual
    brace-finding, which is fragile (see backend/README.md — this replaced a
    real bug where truncated/prefixed model output broke json.loads)."""
    if config.LLM_PROVIDER == "gemini":
        return _gemini_json(system, user_content, schema, max_tokens)
    return _anthropic_json(system, user_content, schema, max_tokens)


def complete_vision(image_bytes: bytes, prompt: str, max_tokens: int = 300) -> str:
    if config.LLM_PROVIDER == "gemini":
        return _gemini_vision(image_bytes, prompt, max_tokens)
    return _anthropic_vision(image_bytes, prompt, max_tokens)


def complete_vision_json(image_bytes: bytes, prompt: str, schema: dict, max_tokens: int = 300):
    if config.LLM_PROVIDER == "gemini":
        return _gemini_vision_json(image_bytes, prompt, schema, max_tokens)
    return _anthropic_vision_json(image_bytes, prompt, schema, max_tokens)


def _gemini_client():
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)


def _gemini_generate(contents, config_kwargs: dict):
    """Calls generate_content with thinking disabled where supported. Some
    model families (e.g. flash-lite) reject the thinking_config param outright
    (400 INVALID_ARGUMENT) rather than ignoring it, so retry without it instead
    of hardcoding which models support what — that list changes constantly."""
    from google.genai import types

    client = _gemini_client()
    try:
        cfg = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0), **config_kwargs
        )
        return client.models.generate_content(model=config.GEMINI_MODEL, contents=contents, config=cfg)
    except Exception as e:
        if "INVALID_ARGUMENT" not in str(e):
            raise
        cfg = types.GenerateContentConfig(**config_kwargs)
        return client.models.generate_content(model=config.GEMINI_MODEL, contents=contents, config=cfg)


def _gemini_text(system: str | None, user_content: str, max_tokens: int) -> str:
    resp = _gemini_generate(
        user_content,
        {"system_instruction": system, "max_output_tokens": max_tokens},
    )
    return resp.text


def _gemini_json(system: str | None, user_content: str, schema: dict, max_tokens: int):
    resp = _gemini_generate(
        user_content,
        {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
        },
    )
    return json.loads(resp.text)


def _gemini_vision(image_bytes: bytes, prompt: str, max_tokens: int) -> str:
    from google.genai import types

    resp = _gemini_generate(
        [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
        {"max_output_tokens": max_tokens},
    )
    return resp.text


def _gemini_vision_json(image_bytes: bytes, prompt: str, schema: dict, max_tokens: int):
    from google.genai import types

    resp = _gemini_generate(
        [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
        {
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
        },
    )
    return json.loads(resp.text)


def _anthropic_text(system: str | None, user_content: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_content}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return resp.content[0].text


def _anthropic_json(system: str | None, user_content: str, schema: dict, max_tokens: int):
    """Claude has no bare response_schema mode — force it via a single
    synthetic tool whose input_schema is our schema, and tool_choice pinned
    to it, then read the structured tool call arguments back out."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_content}],
        "tools": [{"name": "emit_result", "description": "Emit the structured result.", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "emit_result"},
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise ValueError("Anthropic response had no tool_use block")


def _anthropic_vision(image_bytes: bytes, prompt: str, max_tokens: int) -> str:
    import base64

    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(image_bytes).decode(),
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return resp.content[0].text


def _anthropic_vision_json(image_bytes: bytes, prompt: str, schema: dict, max_tokens: int):
    import base64

    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(image_bytes).decode(),
                }},
                {"type": "text", "text": prompt},
            ],
        }],
        tools=[{"name": "emit_result", "description": "Emit the structured result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "emit_result"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise ValueError("Anthropic response had no tool_use block")
