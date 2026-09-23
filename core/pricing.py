"""Pre-run checks on models.yaml: pinned ids, and prices against providers that publish them.

models.yaml stays the source of every cost figure. Live prices only verify it; a mismatch stops
the run so a price change is a decision, not a silent change to published numbers.

Live price endpoints exist for Together (/v1/models) and xAI (/v1/language-models). Anthropic,
OpenAI, Google and Mistral publish no machine-readable prices, so their entries are unverified.
"""
import os, re
import requests

TOLERANCE = 0.005  # relative; absorbs float noise in provider responses

ALIAS = re.compile(r"(^|[-_])latest$")


def unpinned(models):
    """Ids that float to a new version without the config changing."""
    return [m["id"] for m in models if ALIAS.search(m["id"])]


def _source(m):
    if m["provider"] == "xai":
        return "xai"
    if m["provider"] == "openai_compat" and "together.ai" in (m.get("base_url") or ""):
        return "together"
    return None


def _get(url, key):
    r = requests.get(url, headers={"Authorization": "Bearer " + key}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict):
        return d.get("data") or d.get("models") or []
    return d


def _together(key):
    # Together bills cached input at the full input rate, so there is no cached price to check.
    return {m["id"]: {"input": m["pricing"]["input"], "output": m["pricing"]["output"]}
            for m in _get("https://api.together.ai/v1/models", key) if m.get("pricing")}


def _xai(key):
    # Prices are reported in units of $1e-4 per MTok. Standard (<200K prompt) tier only, matching
    # how models.yaml prices every model.
    return {m["id"]: {"input": m["prompt_text_token_price"] / 1e4,
                      "cached": m["cached_prompt_text_token_price"] / 1e4,
                      "output": m["completion_text_token_price"] / 1e4}
            for m in _get("https://api.x.ai/v1/language-models", key)}


FETCH = {"together": (_together, "TOGETHER_API_KEY"), "xai": (_xai, "XAI_API_KEY")}
FIELDS = {"input": "price_input_per_mtok", "cached": "price_cached_input_per_mtok",
          "output": "price_output_per_mtok"}


def check(models):
    """Compare configured prices with live ones.

    Returns (verified, problems, unverified): ids that matched, human-readable problem lines,
    and ids with no live price source.
    """
    live, problems = {}, []
    for src in {_source(m) for m in models} - {None}:
        fn, keyenv = FETCH[src]
        if not os.environ.get(keyenv):
            continue  # models without a key are skipped by the runner anyway
        try:
            live[src] = fn(os.environ[keyenv])
        except Exception as e:
            problems.append(f"{src}: could not fetch live prices ({type(e).__name__}: {str(e)[:120]})")

    verified, unverified = [], []
    for m in models:
        src = _source(m)
        if src is None or not os.environ.get(FETCH[src][1]):
            unverified.append(m["id"]); continue
        if src not in live:
            continue  # fetch failure already reported
        price = live[src].get(m["id"])
        if price is None:
            problems.append(f"{m['id']}: not listed by {src}")
            continue
        drift = [f"{f} config={m[FIELDS[f]]} live={round(v, 4)}" for f, v in price.items()
                 if abs(m[FIELDS[f]] - v) > TOLERANCE * max(abs(v), 1e-9)]
        if drift:
            problems.append(f"{m['id']}: " + "; ".join(drift))
        else:
            verified.append(m["id"])
    return verified, problems, unverified
