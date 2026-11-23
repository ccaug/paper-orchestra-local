"""Local LLM shim for PaperOrchestra (plocal.llm_shim).

PaperOrchestra's outline / literature / refinement agents call Gemini directly (google-genai), so they
cannot use the 'gpt' model-name trick. This shim redirects every one of those calls to ANY
OpenAI-compatible local server (Ollama, llama.cpp server, colibri `coli serve`...), with:
  * streaming + a visible heartbeat (tokens, tok/s) so you always know it is alive
  * JSON / LaTeX parsing with retries, using the original parsers
  * <think> stripping, optional reasoning_effort=none, num_ctx for Ollama
Images are dropped (text-only local models) and the prompt says so.
"""
import json
import os
import re
import sys
import time

MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen3:8b")
BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("LOCAL_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1"
CTX = int(os.getenv("LOCAL_LLM_CTX", "16384"))
MAX_TOKENS = int(os.getenv("LOCAL_MAX_TOKENS", "5500"))
NO_THINK = os.getenv("NO_THINK", "1") == "1"

_client = None
STATS = {"calls": 0, "tokens": 0, "seconds": 0.0}


def client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=BASE_URL, api_key=os.getenv("OPENAI_API_KEY", "local"), timeout=7200)
    return _client


def _to_text(obj):
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        return "\n\n".join(t for t in (_to_text(o) for o in obj) if t)
    t = getattr(obj, "text", None)
    if t:
        return t
    parts = getattr(obj, "parts", None)
    if parts:
        return _to_text(list(parts))
    if getattr(obj, "inline_data", None) is not None:
        return "[an image was attached here but this local model is text-only]"
    return ""


def local_chat(prompt, system=None, temperature=0.4, max_tokens=None, label="llm"):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt + ("\n/no_think" if NO_THINK else "")})
    eb = {"options": {"num_ctx": CTX}}
    if NO_THINK:
        eb["reasoning_effort"] = "none"
    est_in = sum(len(m["content"]) for m in msgs) // 3
    print(f"  [local-llm] {label}: sending ~{est_in} tokens to {MODEL} (ctx {CTX})...", flush=True)
    t0 = time.time()
    first = None
    n = think = 0
    last = t0
    out = []
    stream = client().chat.completions.create(model=MODEL, messages=msgs, temperature=temperature,
                                              max_tokens=max_tokens or MAX_TOKENS, stream=True, extra_body=eb)
    for ev in stream:
        dl = ev.choices[0].delta if ev.choices else None
        if dl is None:
            continue
        if getattr(dl, "reasoning", None) or getattr(dl, "reasoning_content", None):
            think += 1
        if dl.content:
            if first is None:
                first = time.time()
                print(f"  [local-llm] first token after {first - t0:.0f}s"
                      + (f" ({think} thinking tokens)" if think else ""), flush=True)
            out.append(dl.content)
            n += 1
        if time.time() - last > 8:
            last = time.time()
            el = last - (first or t0)
            state = "writing" if first else ("thinking" if think else "reading prompt")
            print(f"  [local-llm] {state}... {n} tok, {n / max(el, 1e-6):.1f} tok/s, {last - t0:.0f}s elapsed", flush=True)
    dt = time.time() - t0
    STATS["calls"] += 1
    STATS["tokens"] += n
    STATS["seconds"] += dt
    print(f"  [local-llm] done: {n} tokens in {dt:.0f}s", flush=True)
    return re.sub(r"<think>.*?</think>", "", "".join(out), flags=re.S).strip()


def _default_parser():
    from utils.gemini_utils import parse_gemini_json_results
    return parse_gemini_json_results


def shim_contents(contents, model_name=None, max_retries=3, base_interval_sec=5, result_parsing_func=None,
                  generation_configs=None, check_parsed_response_not_none=True, **kw):
    gc = generation_configs or {}
    system = _to_text(gc.get("system_instruction")) or None
    temp = float(gc.get("temperature", 0.4)) if gc.get("temperature") is not None else 0.4
    maxo = gc.get("max_output_tokens")
    parse = result_parsing_func or _default_parser()
    prompt = _to_text(contents)
    label = (system or prompt)[:40].replace("\n", " ")
    raw, parsed = None, None
    for attempt in range(max_retries):
        p = prompt if attempt == 0 else prompt + "\n\nIMPORTANT: your previous reply could not be parsed. Follow the requested output format exactly."
        raw = local_chat(p, system, temp, maxo, label)
        parsed = parse(raw)
        if parsed is not None or not check_parsed_response_not_none:
            return {"raw_response": raw, "parsed_response": parsed}
        print(f"  [local-llm] parse failed (attempt {attempt + 1}/{max_retries}); retrying", flush=True)
    raise RuntimeError("local model output could not be parsed after retries:\n" + (raw or "")[:500])


def shim_text_prompt(prompt, model_name=None, result_parsing_func=None, generation_configs=None,
                     check_parsed_response_not_none=True, max_retries=5, base_interval_sec=5, **kw):
    return shim_contents([prompt], model_name, min(max_retries, 3), base_interval_sec, result_parsing_func,
                         generation_configs, check_parsed_response_not_none)


def shim_images(prompt, images, model_name=None, result_parsing_func=None, generation_configs=None,
                check_parsed_response_not_none=True, max_retries=5, base_interval_sec=5, **kw):
    note = f"\n\n[{len(images)} image(s) were provided but are omitted: this local model is text-only.]" if images else ""
    return shim_contents([prompt + note], model_name, min(max_retries, 3), base_interval_sec, result_parsing_func,
                         generation_configs, check_parsed_response_not_none)


def install(model=None, base_url=None, ctx=None):
    """Redirect every Gemini call in PaperOrchestra to the local OpenAI-compatible server."""
    global MODEL, BASE_URL, CTX
    MODEL = model or MODEL
    BASE_URL = base_url or BASE_URL
    CTX = ctx or CTX
    import utils.gemini_utils as gu
    import utils.llm_backend_utils as lb
    import methods.agents.outline_agent as oa
    import methods.agents.literature_review_agent as la
    for mod in (gu, lb):
        mod.call_gemini_with_contents = shim_contents
        mod.call_gemini_with_text_prompt = shim_text_prompt
        mod.call_gemini_with_images = shim_images
    oa.call_gemini_with_contents = shim_contents
    la.call_gemini_with_contents = shim_contents
    try:
        import methods.agents.content_refinement_agent as ca
        ca.call_gemini_with_contents = shim_contents
        ca.call_gemini_with_images = shim_images
    except Exception:
        pass
    print(f"[local-llm] all Gemini calls now go to {BASE_URL} · model {MODEL} · ctx {CTX}", flush=True)
