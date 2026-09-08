"""Client-side multi-turn tool loops for OpenAI + Gemini against Connect AI MCP.

Same loop for both providers: list MCP tools -> convert to the provider's function
schema -> model plans -> we dispatch tool calls back to MCP -> feed results ->
repeat until the model stops or the turn cap. Captures the 4 token categories,
timing split (model vs MCP), full execution trace, and any error class.
"""
import json, time, random


def _retry(fn, tries=6, base=3.0):
    """Retry a provider API call on transient errors (429/5xx/overloaded) with exp backoff + jitter."""
    _T = ("overloaded", "overload", "rate limit", "ratelimit", "rate_limit", "429", "529",
          "503", "502", "500", "timeout", "timed out", "unavailable", "resource_exhausted", "connection")
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
            transient = code in (429, 500, 502, 503, 529) or any(t in msg for t in _T)
            if not transient or i == tries - 1:
                raise
            time.sleep(min(base * (2 ** i), 60) + random.random())


SYSTEM = ("You are an enterprise data assistant. Use the available tools to answer the "
          "user's question by querying the data. Return your FINAL answer as a single "
          "markdown table with one row per account and clearly named columns. Do not "
          "invent data; only report what the tools return.")


# ---------- schema adapters ----------
def mcp_to_openai(tools):
    out = []
    for t in tools:
        out.append({"type": "function", "function": {
            "name": t["name"],
            "description": (t.get("description") or "")[:1024],
            "parameters": t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _clean_gemini_schema(s):
    """Gemini function-declaration schema: keep type/properties/items/enum/required, drop the rest."""
    if not isinstance(s, dict):
        return {"type": "object", "properties": {}}
    keep = {}
    for k in ("type", "description", "enum", "required"):
        if k in s:
            keep[k] = s[k]
    if "properties" in s and isinstance(s["properties"], dict):
        keep["properties"] = {k: _clean_gemini_schema(v) for k, v in s["properties"].items()}
    if "items" in s:
        keep["items"] = _clean_gemini_schema(s["items"])
    keep.setdefault("type", "object")
    return keep


def mcp_to_gemini(tools):
    decls = []
    for t in tools:
        decls.append({
            "name": t["name"],
            "description": (t.get("description") or "")[:1024],
            "parameters": _clean_gemini_schema(t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}}),
        })
    return decls


# ---------- base run record ----------
def _blank(model, provider, condition, run_idx):
    return {"model": model, "provider": provider, "condition": condition, "run": run_idx,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "uncached_input": 0, "cached_input": 0, "output": 0, "reasoning": 0, "raw_total": 0,
            "tool_calls": 0, "turns": 0, "hit_turn_cap": False, "stop_reason": None,
            "model_time_s": 0.0, "mcp_time_s": 0.0, "wall_s": 0.0,
            # transparency: exactly which knobs this run actually ran with
            "turn_cap": None, "temperature_requested": None, "temperature_applied": None,
            "reasoning_mode": None, "thinking_chars": 0,
            "trace": [], "final_answer": "", "error": None, "error_class": None}


# ---------- OpenAI (Responses API - required for function tools + reasoning) ----------
def mcp_to_openai_responses(tools):
    out = []
    for t in tools:
        out.append({"type": "function", "name": t["name"],
                    "description": (t.get("description") or "")[:1024],
                    "parameters": t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}}})
    return out


def run_openai(model, mcp, prompt, condition, run_idx, api_key, turn_cap=15, reasoning_effort=None, temperature=None):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    rec = _blank(model, "openai", condition, run_idx)
    rec["turn_cap"] = turn_cap; rec["temperature_requested"] = temperature
    rec["reasoning_mode"] = f"effort={reasoning_effort}" if (reasoning_effort and reasoning_effort != "none") else "off"
    tools = mcp_to_openai_responses(mcp.list_tools())
    rec["trace"].append({"event": "tools_listed", "count": len(tools), "names": [t["name"] for t in tools][:20]})
    t_start = time.perf_counter()

    def create(inp, prev_id):
        base = dict(model=model, input=inp, tools=tools, tool_choice="auto")
        if prev_id:
            base["previous_response_id"] = prev_id
        if reasoning_effort and reasoning_effort != "none":
            base["reasoning"] = {"effort": reasoning_effort}
        if temperature is not None:
            base["temperature"] = temperature
        try:
            r = _retry(lambda: client.responses.create(**base)); rec["temperature_applied"] = temperature; return r
        except Exception:
            base.pop("temperature", None); rec["temperature_applied"] = "provider_default"  # reasoning models reject temperature
            try:
                return _retry(lambda: client.responses.create(**base))
            except Exception:
                base.pop("reasoning", None)
                return _retry(lambda: client.responses.create(**base))

    try:
        inp = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        prev_id = None
        for turn in range(1, turn_cap + 1):
            rec["turns"] = turn
            t0 = time.perf_counter()
            resp = create(inp, prev_id)
            rec["model_time_s"] += time.perf_counter() - t0
            prev_id = resp.id
            u = resp.usage
            it = int(getattr(u, "input_tokens", 0) or 0); ot = int(getattr(u, "output_tokens", 0) or 0)
            cached = int(getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0)
            reason = int(getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0)
            rec["uncached_input"] += it - cached; rec["cached_input"] += cached
            rec["output"] += ot; rec["reasoning"] += reason; rec["raw_total"] += int(getattr(u, "total_tokens", 0) or 0)
            fcalls = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
            rec["stop_reason"] = getattr(resp, "status", None)
            if not fcalls:
                rec["final_answer"] = getattr(resp, "output_text", "") or ""
                break
            outputs = []
            for item in fcalls:
                rec["tool_calls"] += 1
                try:
                    args = json.loads(item.arguments or "{}")
                except Exception:
                    args = {}
                t1 = time.perf_counter()
                result = mcp.call_tool(item.name, args)
                dt = time.perf_counter() - t1; rec["mcp_time_s"] += dt
                body = mcp.result_text(result)
                rec["trace"].append({"turn": turn, "tool": item.name, "args": args, "result_chars": len(body), "mcp_s": round(dt, 2)})
                outputs.append({"type": "function_call_output", "call_id": item.call_id, "output": body})
            inp = outputs  # server keeps context via previous_response_id
            if turn == turn_cap:
                rec["hit_turn_cap"] = True
    except Exception as e:
        rec["error"] = str(e)[:400]; rec["error_class"] = type(e).__name__
    rec["wall_s"] = round(time.perf_counter() - t_start, 2)
    rec["model_time_s"] = round(rec["model_time_s"], 2); rec["mcp_time_s"] = round(rec["mcp_time_s"], 2)
    return rec


# ---------- Gemini ----------
def run_gemini(model, mcp, prompt, condition, run_idx, api_key, turn_cap=15, thinking_level=None, temperature=None):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    rec = _blank(model, "google", condition, run_idx)
    rec["turn_cap"] = turn_cap; rec["temperature_requested"] = temperature
    rec["reasoning_mode"] = f"thinking_level={thinking_level}" if thinking_level else "off"
    decls = mcp_to_gemini(mcp.list_tools())
    rec["trace"].append({"event": "tools_listed", "count": len(decls), "names": [d["name"] for d in decls][:20]})

    # Try to create a context cache for system + tools (min 1024 tokens; fall back if ineligible)
    _cache_name = None
    try:
        _cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                system_instruction=SYSTEM,
                tools=[types.Tool(function_declarations=decls)],
                ttl="600s",
            ),
        )
        _cache_name = _cache.name
        rec["trace"].append({"event": "cache_created", "name": _cache_name})
    except Exception as _ce:
        rec["trace"].append({"event": "cache_skipped", "reason": str(_ce)[:120]})

    if _cache_name:
        cfg_kwargs = dict(cached_content=_cache_name,
                          automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    else:
        cfg_kwargs = dict(tools=[types.Tool(function_declarations=decls)], system_instruction=SYSTEM,
                          automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    if temperature is not None:
        cfg_kwargs["temperature"] = temperature; rec["temperature_applied"] = temperature
    try:
        if thinking_level:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    except Exception:
        pass
    config = types.GenerateContentConfig(**cfg_kwargs)
    contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    t_start = time.perf_counter()
    try:
        for turn in range(1, turn_cap + 1):
            rec["turns"] = turn
            t0 = time.perf_counter()
            resp = _retry(lambda: client.models.generate_content(model=model, contents=contents, config=config))
            rec["model_time_s"] += time.perf_counter() - t0
            um = resp.usage_metadata
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            cached = int(getattr(um, "cached_content_token_count", 0) or 0)
            cand = int(getattr(um, "candidates_token_count", 0) or 0)
            thoughts = int(getattr(um, "thoughts_token_count", 0) or 0)
            rec["uncached_input"] += pt - cached; rec["cached_input"] += cached
            rec["output"] += cand; rec["reasoning"] += thoughts
            rec["raw_total"] += int(getattr(um, "total_token_count", 0) or 0)
            cand0 = resp.candidates[0]
            parts = cand0.content.parts or []
            fcalls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            rec["stop_reason"] = str(getattr(cand0, "finish_reason", ""))
            if not fcalls:
                rec["final_answer"] = resp.text or ""
                break
            contents.append(cand0.content)
            resp_parts = []
            for fc in fcalls:
                rec["tool_calls"] += 1
                args = dict(fc.args or {})
                t1 = time.perf_counter()
                result = mcp.call_tool(fc.name, args)
                dt = time.perf_counter() - t1; rec["mcp_time_s"] += dt
                body = mcp.result_text(result)
                rec["trace"].append({"turn": turn, "tool": fc.name, "args": args, "result_chars": len(body), "mcp_s": round(dt, 2)})
                resp_parts.append(types.Part.from_function_response(name=fc.name, response={"result": body}))
            contents.append(types.Content(role="user", parts=resp_parts))
            if turn == turn_cap:
                rec["hit_turn_cap"] = True
    except Exception as e:
        rec["error"] = str(e)[:400]; rec["error_class"] = type(e).__name__
    finally:
        if _cache_name:
            try:
                client.caches.delete(name=_cache_name)
            except Exception:
                pass
    rec["wall_s"] = round(time.perf_counter() - t_start, 2)
    rec["model_time_s"] = round(rec["model_time_s"], 2); rec["mcp_time_s"] = round(rec["mcp_time_s"], 2)
    return rec


# ---------- Anthropic (Messages API) ----------
def run_anthropic(model, mcp, prompt, condition, run_idx, api_key, turn_cap=12, thinking_budget=None, temperature=None, **kw):
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    rec = _blank(model, "anthropic", condition, run_idx)
    rec["turn_cap"] = turn_cap; rec["temperature_requested"] = temperature
    rec["reasoning_mode"] = f"thinking_budget={thinking_budget}" if thinking_budget else "off"
    # Extended thinking: budget_tokens must be < max_tokens; thinking tokens are billed inside
    # output_tokens (no separate usage field), so cost already accounts for them. thinking_chars
    # is a transparency proxy for effort since Anthropic doesn't report thinking-token counts.
    # Two thinking APIs: older models take manual {enabled, budget_tokens}; newer ones (Sonnet 5,
    # Opus 4.8) reject that with a 400 and require {adaptive, display:summarized} + output_config.
    # effort. We try manual first and flip to adaptive once per run on that specific 400.
    max_toks = max(16384, (thinking_budget or 0) + 8192)
    think = {"mode": "enabled" if thinking_budget else None}  # may flip to "adaptive"
    tools = [{"name": t["name"], "description": (t.get("description") or "")[:1024],
              "input_schema": t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}}}
             for t in mcp.list_tools()]
    if tools:
        tools[-1] = dict(tools[-1], cache_control={"type": "ephemeral"})
    rec["trace"].append({"event": "tools_listed", "count": len(tools), "names": [t["name"] for t in tools][:20]})
    system_prompt = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": prompt}]
    t_start = time.perf_counter()
    try:
        for turn in range(1, turn_cap + 1):
            rec["turns"] = turn
            t0 = time.perf_counter()
            def _mc(temp):
                kw = dict(model=model, system=system_prompt, messages=messages, tools=tools, max_tokens=max_toks)
                if think["mode"] == "enabled":  # thinking forces temperature to provider default
                    kw["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
                elif think["mode"] == "adaptive":
                    kw["thinking"] = {"type": "adaptive", "display": "summarized"}  # display -> thinking_chars visible
                    kw["output_config"] = {"effort": "low"}
                elif temp is not None:
                    kw["temperature"] = temp
                return _retry(lambda: client.messages.create(**kw))
            if think["mode"]:
                try:
                    resp = _mc(None)
                except Exception as e:
                    if think["mode"] == "enabled" and "adaptive" in str(e).lower():
                        think["mode"] = "adaptive"; rec["reasoning_mode"] = "adaptive(effort=low)"
                        resp = _mc(None)
                    else:
                        raise
                rec["temperature_applied"] = "provider_default"  # forced by thinking
            else:
                try:
                    resp = _mc(temperature)
                    rec["temperature_applied"] = temperature if temperature is not None else "provider_default"
                except Exception as e:
                    if "temperature" in str(e).lower():
                        resp = _mc(None); rec["temperature_applied"] = "provider_default"  # Sonnet 5 / Opus deprecate it
                    else:
                        raise
            rec["model_time_s"] += time.perf_counter() - t0
            for b in resp.content:  # accumulate thinking effort (proxy)
                if getattr(b, "type", None) == "thinking":
                    rec["thinking_chars"] += len(getattr(b, "thinking", "") or "")
            u = resp.usage
            it = int(getattr(u, "input_tokens", 0) or 0); ot = int(getattr(u, "output_tokens", 0) or 0)
            cread = int(getattr(u, "cache_read_input_tokens", 0) or 0); ccreate = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
            rec["uncached_input"] += it + ccreate; rec["cached_input"] += cread; rec["output"] += ot
            rec["raw_total"] += it + ccreate + cread + ot
            rec["stop_reason"] = resp.stop_reason
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                rec["final_answer"] = "\n".join(texts); break
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                rec["tool_calls"] += 1
                args = dict(tu.input or {})
                t1 = time.perf_counter(); result = mcp.call_tool(tu.name, args); dt = time.perf_counter() - t1; rec["mcp_time_s"] += dt
                body = mcp.result_text(result)
                rec["trace"].append({"turn": turn, "tool": tu.name, "args": args, "result_chars": len(body), "mcp_s": round(dt, 2)})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": body})
            messages.append({"role": "user", "content": results})
            if turn == turn_cap:
                rec["hit_turn_cap"] = True
    except Exception as e:
        rec["error"] = str(e)[:400]; rec["error_class"] = type(e).__name__
    rec["wall_s"] = round(time.perf_counter() - t_start, 2)
    rec["model_time_s"] = round(rec["model_time_s"], 2); rec["mcp_time_s"] = round(rec["mcp_time_s"], 2)
    return rec


# ---------- Generic OpenAI-compatible (Together AI, Groq, Ollama, vLLM, …) ----------
def run_openai_compat(model, mcp, prompt, condition, run_idx, api_key, turn_cap=12,
                      base_url=None, reasoning_effort=None, temperature=None, use_stream=False):
    """Tool-use loop for any OpenAI-compatible inference endpoint.

    Set base_url in models.yaml (e.g. https://api.together.ai/v1, http://localhost:11434/v1).
    For keyless local servers (Ollama) omit api_key_env in models.yaml — 'nokey' is passed here.
    Set use_stream: true in models.yaml for providers that require streaming (e.g. Qwen3.7-Max
    on Together.ai). Token counts come from the final stream chunk's usage field.
    """
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "nokey", base_url=base_url)
    rec = _blank(model, "openai_compat", condition, run_idx)
    rec["turn_cap"] = turn_cap; rec["temperature_requested"] = temperature
    rec["reasoning_mode"] = f"effort={reasoning_effort}" if reasoning_effort else "off"
    tools = mcp_to_openai(mcp.list_tools())
    rec["trace"].append({"event": "tools_listed", "count": len(tools), "names": [t["function"]["name"] for t in tools][:20]})
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    t_start = time.perf_counter()

    def _call_streaming(base):
        """Stream response, reassemble content + tool calls, return (message_dict, usage, finish_reason)."""
        chunks = list(_retry(lambda: client.chat.completions.create(
            **base, stream=True, stream_options={"include_usage": True})))
        content = ""; tcs = {}; finish = None; usage = None
        for chunk in chunks:
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta; finish = chunk.choices[0].finish_reason or finish
            if d.content:
                content += d.content
            for tc in (d.tool_calls or []):
                e = tcs.setdefault(tc.index, {"id": tc.id or "", "name": "", "arguments": ""})
                if tc.id: e["id"] = tc.id
                if tc.function:
                    if tc.function.name: e["name"] += tc.function.name
                    if tc.function.arguments: e["arguments"] += tc.function.arguments
        tool_calls = [{"id": e["id"], "type": "function",
                       "function": {"name": e["name"], "arguments": e["arguments"]}}
                      for e in tcs.values()] if tcs else None
        return content, tool_calls, usage, finish

    def create(msgs):
        base = dict(model=model, messages=msgs, tools=tools, tool_choice="auto")
        if reasoning_effort:
            base["reasoning_effort"] = reasoning_effort
        attempts = [{"temperature": temperature}, {}] if temperature is not None else [{}]
        last = None
        for extra in attempts:
            try:
                if use_stream:
                    result = _retry(lambda: _call_streaming({**base, **extra}))
                    rec["temperature_applied"] = extra.get("temperature", "provider_default")
                    return result
                r = _retry(lambda: client.chat.completions.create(**base, **extra))
                rec["temperature_applied"] = extra.get("temperature", "provider_default")
                return r
            except Exception as e:
                last = e
        raise last

    try:
        for turn in range(1, turn_cap + 1):
            rec["turns"] = turn
            t0 = time.perf_counter(); resp = create(messages); rec["model_time_s"] += time.perf_counter() - t0
            if use_stream:
                content, tool_calls_raw, u, finish_reason = resp
            else:
                msg = resp.choices[0].message; finish_reason = resp.choices[0].finish_reason
                content = msg.content; tool_calls_raw = None; u = resp.usage
                if msg.tool_calls:
                    tool_calls_raw = [{"id": tc.id, "type": "function",
                                       "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                      for tc in msg.tool_calls]
            pt = int(getattr(u, "prompt_tokens", 0) or 0); ct = int(getattr(u, "completion_tokens", 0) or 0)
            cached = int(getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
            reason = int(getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
            rec["uncached_input"] += pt - cached; rec["cached_input"] += cached
            rec["output"] += ct; rec["reasoning"] += reason
            rec["raw_total"] += int(getattr(u, "total_tokens", 0) or 0)
            rec["stop_reason"] = finish_reason
            if not tool_calls_raw:
                rec["final_answer"] = content or ""; break
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls_raw})
            for tc in tool_calls_raw:
                rec["tool_calls"] += 1
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                fn = tc["function"]["name"]; tc_id = tc["id"]
                t1 = time.perf_counter(); result = mcp.call_tool(fn, args); dt = time.perf_counter() - t1; rec["mcp_time_s"] += dt
                body = mcp.result_text(result)
                rec["trace"].append({"turn": turn, "tool": fn, "args": args, "result_chars": len(body), "mcp_s": round(dt, 2)})
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": body})
            if turn == turn_cap:
                rec["hit_turn_cap"] = True
    except Exception as e:
        rec["error"] = str(e)[:400]; rec["error_class"] = type(e).__name__
    rec["wall_s"] = round(time.perf_counter() - t_start, 2)
    rec["model_time_s"] = round(rec["model_time_s"], 2); rec["mcp_time_s"] = round(rec["mcp_time_s"], 2)
    return rec


# ---------- Grok (xAI, OpenAI-compatible chat completions) ----------
def run_grok(model, mcp, prompt, condition, run_idx, api_key, turn_cap=12, reasoning_effort=None, temperature=None):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    rec = _blank(model, "xai", condition, run_idx)
    rec["turn_cap"] = turn_cap; rec["temperature_requested"] = temperature
    rec["reasoning_mode"] = f"effort={reasoning_effort}" if reasoning_effort else "off"
    tools = mcp_to_openai(mcp.list_tools())
    rec["trace"].append({"event": "tools_listed", "count": len(tools), "names": [t["function"]["name"] for t in tools][:20]})
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    t_start = time.perf_counter()

    def create(msgs):
        base = dict(model=model, messages=msgs, tools=tools, tool_choice="auto")
        if reasoning_effort:
            base["reasoning_effort"] = reasoning_effort
        last = None
        attempts = [{"temperature": temperature}, {}] if temperature is not None else [{}]
        for extra in attempts:
            try:
                r = _retry(lambda: client.chat.completions.create(**base, **extra))
                rec["temperature_applied"] = extra.get("temperature", "provider_default")
                return r
            except Exception as e:
                last = e
        raise last

    try:
        for turn in range(1, turn_cap + 1):
            rec["turns"] = turn
            t0 = time.perf_counter(); resp = create(messages); rec["model_time_s"] += time.perf_counter() - t0
            u = resp.usage
            pt = int(getattr(u, "prompt_tokens", 0) or 0); ct = int(getattr(u, "completion_tokens", 0) or 0)
            cached = int(getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
            reason = int(getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
            rec["uncached_input"] += pt - cached; rec["cached_input"] += cached; rec["output"] += ct; rec["reasoning"] += reason
            rec["raw_total"] += int(getattr(u, "total_tokens", 0) or 0)
            msg = resp.choices[0].message; rec["stop_reason"] = resp.choices[0].finish_reason
            if not msg.tool_calls:
                rec["final_answer"] = msg.content or ""; break
            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                rec["tool_calls"] += 1
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                t1 = time.perf_counter(); result = mcp.call_tool(tc.function.name, args); dt = time.perf_counter() - t1; rec["mcp_time_s"] += dt
                body = mcp.result_text(result)
                rec["trace"].append({"turn": turn, "tool": tc.function.name, "args": args, "result_chars": len(body), "mcp_s": round(dt, 2)})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": body})
            if turn == turn_cap:
                rec["hit_turn_cap"] = True
    except Exception as e:
        rec["error"] = str(e)[:400]; rec["error_class"] = type(e).__name__
    rec["wall_s"] = round(time.perf_counter() - t_start, 2)
    rec["model_time_s"] = round(rec["model_time_s"], 2); rec["mcp_time_s"] = round(rec["mcp_time_s"], 2)
    return rec
