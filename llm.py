# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — llm.py
# ─────────────────────────────────────────────
#
#  One entry point for every LLM call in the pipeline:
#
#      data = call_json(prompt, schema=SCHEMA, system=...)
#
#  - Ollama: uses JSON-schema constrained decoding (format=schema) and a
#    context window large enough for the scraped text (Ollama's default
#    num_ctx silently truncates long prompts).
#  - Claude: official Anthropic SDK with structured outputs
#    (output_config.format) — falls back to plain prompting if the schema
#    is rejected.
#  - Tolerant JSON parsing + one repair retry for both backends.
#  - Usage counters for the run summary.
# ─────────────────────────────────────────────

from __future__ import annotations

import json
import os
import re
import threading

import config

OLLAMA_HOST    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_NUM_CTX = int(os.environ.get("CI_OLLAMA_NUM_CTX", "16384"))

_stats_lock = threading.Lock()
STATS = {"calls": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0}


def backend() -> str:
    return os.environ.get("CI_LLM_BACKEND") or config.LLM_BACKEND


def model_name() -> str:
    if backend() == "claude":
        return os.environ.get("CI_CLAUDE_MODEL") or config.CLAUDE_MODEL
    return os.environ.get("CI_OLLAMA_MODEL") or config.OLLAMA_MODEL


def describe() -> str:
    return f"{backend()} · {model_name()}"


def _bump(**kw):
    with _stats_lock:
        for k, v in kw.items():
            STATS[k] = STATS.get(k, 0) + (v or 0)


# ── Backends ──────────────────────────────────

def _call_ollama(prompt: str, system: str | None, schema: dict | None, max_tokens: int) -> str:
    from ollama import Client
    client = Client(host=OLLAMA_HOST)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = {}
    if schema is not None:
        kwargs["format"] = schema
    resp = client.chat(
        model=model_name(),
        messages=messages,
        think=False,
        options={"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.1, "num_predict": max_tokens},
        **kwargs,
    )
    _bump(input_tokens=getattr(resp, "prompt_eval_count", 0) or 0,
          output_tokens=getattr(resp, "eval_count", 0) or 0)
    return resp.message.content


_claude_client = None


def _claude():
    global _claude_client
    if _claude_client is None:
        import anthropic
        key = config.CLAUDE_API_KEY or os.environ.get("ANTHROPIC_API_KEY") or None
        _claude_client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180.0)
    return _claude_client


def _call_claude(prompt: str, system: str | None, schema: dict | None, max_tokens: int) -> str:
    import anthropic
    client = _claude()
    params = {
        "model": model_name(),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        # Extraction is a well-specified task: keep thinking light to control cost.
        "output_config": {"effort": os.environ.get("CI_CLAUDE_EFFORT", "low")},
    }
    if system:
        params["system"] = system
    if schema is not None:
        params["output_config"]["format"] = {"type": "json_schema", "schema": _strictify(schema)}

    try:
        resp = client.messages.create(**params)
    except anthropic.BadRequestError as e:
        # Older models may reject effort or the schema: retry once without them.
        print(f"      [WARN] Claude rejected output_config ({str(e)[:120]}); retrying without it")
        params.pop("output_config", None)
        resp = client.messages.create(**params)

    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request (stop_reason=refusal)")
    usage = getattr(resp, "usage", None)
    if usage:
        _bump(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _strictify(schema: dict) -> dict:
    """Structured outputs need additionalProperties=false on every object."""
    if isinstance(schema, dict):
        out = {k: _strictify(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
            out.setdefault("required", list(out.get("properties", {}).keys()))
        return out
    if isinstance(schema, list):
        return [_strictify(x) for x in schema]
    return schema


# ── Public API ────────────────────────────────

def call_text(prompt: str, system: str | None = None, max_tokens: int = 2048) -> str:
    _bump(calls=1)
    if backend() == "claude":
        return _call_claude(prompt, system, None, max_tokens)
    if backend() == "ollama":
        return _call_ollama(prompt, system, None, max_tokens)
    raise ValueError(f"Unknown LLM backend: {backend()}")


def call_json(prompt: str, schema: dict | None = None, system: str | None = None,
              max_tokens: int = 2048) -> dict:
    """Call the active backend and return parsed JSON (dict). Raises on failure."""
    _bump(calls=1)
    fn = {"claude": _call_claude, "ollama": _call_ollama}.get(backend())
    if fn is None:
        raise ValueError(f"Unknown LLM backend: {backend()}")

    if schema is None and backend() == "ollama":
        schema = {"type": "object"}          # still forces JSON mode
    raw = fn(prompt, system, schema, max_tokens)
    try:
        return parse_json(raw)
    except ValueError:
        pass
    # One repair attempt: ask the model to re-emit valid JSON only.
    repair = (f"The following was supposed to be a single valid JSON object but is not.\n"
              f"Return ONLY the corrected JSON object.\n---\n{raw[:6000]}\n---")
    raw2 = fn(repair, None, schema, max_tokens)
    try:
        return parse_json(raw2)
    except ValueError:
        _bump(failures=1)
        raise


def parse_json(raw: str) -> dict:
    """Tolerant JSON-object parser for LLM output."""
    if raw is None:
        raise ValueError("Empty LLM response")
    s = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    s = re.sub(r"```(?:json)?", "", s).strip().strip("`").strip()
    if s.lower() in ("null", "none", ""):
        return {}
    try:
        val = json.loads(s)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in LLM response: {s[:200]}")
    # Scan for the longest balanced, parseable object starting at the first brace
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError:
                    break
    # Last resort: trailing-comma cleanup
    cleaned = re.sub(r",\s*([}\]])", r"\1", s[start:s.rfind("}") + 1])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        raise ValueError(f"No valid JSON found in LLM response: {s[:300]}")


def check_backend() -> tuple[bool, str]:
    """Quick health check used by the UI before a run."""
    b = backend()
    if b == "ollama":
        try:
            from ollama import Client
            names = [m.model for m in Client(host=OLLAMA_HOST).list().models]
            if not any(n == model_name() or n.split(":")[0] == model_name() for n in names):
                return False, f"Ollama is running but model '{model_name()}' is not pulled (ollama pull {model_name()})."
            return True, f"Ollama OK · {model_name()}"
        except Exception as e:
            return False, f"Ollama not reachable at {OLLAMA_HOST}: {e}"
    if b == "claude":
        if not (config.CLAUDE_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return False, "No Anthropic credentials found (set ANTHROPIC_API_KEY)."
        return True, f"Claude · {model_name()}"
    return False, f"Unknown backend {b}"


# ── Schema helpers ────────────────────────────

def nullable(t: str | dict) -> dict:
    base = {"type": t} if isinstance(t, str) else t
    return {"anyOf": [base, {"type": "null"}]}


def enum_of(values: list[str]) -> dict:
    return {"type": "string", "enum": values}
