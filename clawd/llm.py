"""Talks to OpenAI-compatible chat APIs (OpenAI, OpenRouter, Ollama, LM Studio, any proxy)
as an alternative to the Claude Code CLI. Uses only the standard library."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT = 90

# name -> (label, base URL, suggested models)
PRESETS = {
    "openrouter": (
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        ["openai/gpt-4o-mini", "google/gemini-2.0-flash-001", "anthropic/claude-3.5-haiku"],
    ),
    "openai": ("OpenAI", "https://api.openai.com/v1", ["gpt-4o-mini", "gpt-4.1-mini"]),
    "custom": ("OpenAI-compatible (Ollama, LM Studio, ...)", "http://localhost:11434/v1", ["llama3.2"]),
}
PROVIDERS = ("claude", *PRESETS)


class ApiError(Exception):
    """`fatal` means retrying won't help until the settings change (bad key, wrong model...)."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def endpoint(base: str) -> str:
    base = base.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _content(data: dict) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ApiError("unexpected response from the API") from None
    if isinstance(content, list):  # some servers return a list of parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise ApiError("the model returned an empty answer")
    return content


def complete(base: str, key: str, model: str, system: str, prompt: str, provider: str = "custom") -> str:
    """One chat completion; returns the model's text."""
    if not base.strip().lower().startswith(("http://", "https://")):
        raise ApiError("API address must start with http:// or https://", fatal=True)
    if not model.strip():
        raise ApiError("model is not set", fatal=True)
    headers = {"Content-Type": "application/json", "User-Agent": "Clawd"}
    if key.strip():
        headers["Authorization"] = f"Bearer {key.strip()}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/pikmis/claudepet"
        headers["X-Title"] = "Clawd"
    body = {
        "model": model.strip(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        endpoint(base), data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            error = json.loads(e.read().decode("utf-8", errors="replace")).get("error")
            detail = error.get("message", "") if isinstance(error, dict) else str(error or "")
        except (ValueError, AttributeError):
            pass
        raise ApiError(f"HTTP {e.code}: {detail or e.reason}", fatal=e.code in (400, 401, 402, 403, 404)) from None
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach the API: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise ApiError(f"cannot reach the API: {e}") from None
    except ValueError:
        raise ApiError("the API did not answer with JSON") from None
    return _content(data)
