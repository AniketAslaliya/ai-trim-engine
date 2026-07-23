"""Provider-agnostic LLM client. Every pipeline stage that needs an LLM call
(intent parsing, semantic predicate resolution, visual tagging) goes through
complete_text()/complete_vision() and never imports a provider SDK directly —
swapping providers is a config.LLM_PROVIDER change, not a per-call-site edit.

Default provider is Gemini (free tier, day-one build); Anthropic is the
documented fallback (see config.py, backend/README.md).
"""
from app import config


def complete_text(system: str | None, user_content: str, max_tokens: int = 1000) -> str:
    if config.LLM_PROVIDER == "gemini":
        return _gemini_text(system, user_content, max_tokens)
    return _anthropic_text(system, user_content, max_tokens)


def complete_vision(image_bytes: bytes, prompt: str, max_tokens: int = 300) -> str:
    if config.LLM_PROVIDER == "gemini":
        return _gemini_vision(image_bytes, prompt, max_tokens)
    return _anthropic_vision(image_bytes, prompt, max_tokens)


def _gemini_client():
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)


def _gemini_text(system: str | None, user_content: str, max_tokens: int) -> str:
    from google.genai import types

    client = _gemini_client()
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=max_tokens
        ),
    )
    return resp.text


def _gemini_vision(image_bytes: bytes, prompt: str, max_tokens: int) -> str:
    from google.genai import types

    client = _gemini_client()
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return resp.text


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
