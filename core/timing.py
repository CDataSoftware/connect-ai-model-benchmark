"""Network-independent run time, computed after the fact from what each run logged.

Measured wall time mixes the work a run did with network latency, gateway load and provider
throttling. normalized_time prices the work at typical speeds instead:
  - each tool call at that tool's median latency across all runs, and
  - model time from a per-model fit, model_time ~ a * turns + b * output_tokens, over that
    model's runs with retry back-off removed.
Two runs that make the same calls and generate the same tokens get the same normalized time,
however fast the network was.
"""
import statistics


def _output_tokens(rec):
    # Gemini reports thoughts separately and bills them as output; elsewhere reasoning is nested.
    return (rec.get("output") or 0) + ((rec.get("reasoning") or 0) if rec.get("provider") == "google" else 0)


def _calls(rec):
    return [e for e in rec.get("trace") or [] if "tool" in e]


def references(recs):
    """Median latency per tool name, and per-model (a, b) coefficients, from error-free runs."""
    ok = [r for r in recs if not r.get("error")]
    lat = {}
    for r in ok:
        for e in _calls(r):
            if e.get("mcp_s") is not None:
                lat.setdefault(e["tool"], []).append(e["mcp_s"])
    tool_med = {t: statistics.median(v) for t, v in lat.items()}

    by_model = {}
    for r in ok:
        net = (r.get("model_time_s") or 0) - (r.get("model_retry_backoff_s") or 0)
        if r.get("turns") and net > 0:
            by_model.setdefault(r["model"], []).append((r["turns"], _output_tokens(r), net))
    rates = {}
    for m, pts in by_model.items():
        a = b = None
        if len(pts) >= 3:
            # least squares, no intercept, for t ~ a*turns + b*tokens
            stt = sum(x * x for x, _, _ in pts); soo = sum(y * y for _, y, _ in pts)
            sto = sum(x * y for x, y, _ in pts)
            stv = sum(x * v for x, _, v in pts); sov = sum(y * v for _, y, v in pts)
            det = stt * soo - sto * sto
            if det > 0:
                a = (stv * soo - sov * sto) / det
                b = (sov * stt - stv * sto) / det
        if a is None or a < 0 or b < 0:
            a, b = statistics.median(v / x for x, _, v in pts), 0.0  # fallback: per-turn median
        rates[m] = (a, b)
    return tool_med, rates


def normalized_time(rec, tool_med, rates):
    """Normalized seconds for one run, or None if it can't be priced."""
    a, b = rates.get(rec.get("model"), (None, None))
    if a is None or rec.get("error"):
        return None
    mcp = sum(tool_med.get(e["tool"], e.get("mcp_s") or 0) for e in _calls(rec))
    return round(mcp + a * (rec.get("turns") or 0) + b * _output_tokens(rec), 1)


def search_context_use(rec):
    """(calls, seconds) spent in search_context during the run."""
    sc = [e for e in _calls(rec) if e["tool"].endswith("search_context")]
    return len(sc), round(sum(e.get("mcp_s") or 0 for e in sc), 2)
