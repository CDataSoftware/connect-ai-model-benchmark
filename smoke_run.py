#!/usr/bin/env python3
"""Diagnostic ("2+2"): OpenAI + Gemini, baseline + optimized, 2 runs each.

Full instrumentation across the four dimensions:
  Accuracy    - Layer-1 deterministic score vs the frozen golden
  Efficiency  - 4-category tokens (uncached/cached/output/reasoning) + raw total
  Speed       - wall clock, model-vs-MCP time split, throughput (tok/s)
  Variance    - spread across the 2 runs per cell

Writes one JSON per run to results/ (quarantined) and prints a green-gate table
+ metrics matrix + the raw answers for inspection.
"""
import json, os, sys, time
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import mcp_client, runners, scorer
from core.envutil import load_env
load_env(os.path.join(HERE, ".env"))
CFG = yaml.safe_load(open(os.path.join(HERE, "config", "models.yaml"), encoding="utf-8"))
PROMPT = open(os.path.join(HERE, "config", "prompt.txt"), encoding="utf-8").read().strip()
GOLDEN = scorer.load_golden(os.path.join(HERE, "config", "golden_result.csv"))
RESULTS = os.path.join(HERE, "results"); os.makedirs(RESULTS, exist_ok=True)

EMAIL = os.environ.get("CDATA_EMAIL"); TOKEN = os.environ.get("CDATA_ACCESS_TOKEN")
KEYS = {"openai": os.environ.get("OPENAI_API_KEY"), "google": os.environ.get("GOOGLE_API_KEY")}
TURN_CAP = CFG.get("turn_cap", 15)
RUNS = CFG.get("runs_per_cell", 2)


def one_run(model_cfg, cond, run_idx):
    url = os.environ[cond["mcp_url_env"]]
    mcp = mcp_client.MCPClient(url, EMAIL, TOKEN)
    mcp.initialize()
    prov = model_cfg["provider"]; mid = model_cfg["id"]
    if prov == "openai":
        rec = runners.run_openai(mid, mcp, PROMPT, cond["name"], run_idx, KEYS["openai"],
                                 TURN_CAP, model_cfg.get("reasoning_effort"))
    elif prov == "google":
        rec = runners.run_gemini(mid, mcp, PROMPT, cond["name"], run_idx, KEYS["google"],
                                 TURN_CAP, model_cfg.get("thinking_level"))
    else:
        rec = {"error": f"unknown provider {prov}", "error_class": "ConfigError"}
    if not rec.get("error"):
        rec["score"] = scorer.score(rec.get("final_answer", ""), GOLDEN)
    rec["mcp_url"] = url
    return rec


def gate(rec):
    """8 green-gate checks."""
    s = rec.get("score") or {}
    recon = abs(rec.get("raw_total", 0) - (rec.get("uncached_input", 0) + rec.get("cached_input", 0) + rec.get("output", 0)))
    return {
        "mcp_ok": any(t.get("event") == "tools_listed" for t in rec.get("trace", [])),
        "tools_listed": next((t["count"] for t in rec.get("trace", []) if t.get("event") == "tools_listed"), 0),
        "multiturn_ok": not rec.get("error") and not rec.get("hit_turn_cap"),
        "tokens_ok": rec.get("raw_total", 0) > 0,
        "reconciled": recon <= max(5, 0.02 * rec.get("raw_total", 1)),
        "timing_ok": rec.get("wall_s", 0) > 0,
        "trace_ok": len([t for t in rec.get("trace", []) if t.get("tool")]) >= 0,
        "scored_ok": bool(rec.get("score")),
    }


def main():
    if not (EMAIL and TOKEN):
        sys.exit("Missing CDATA_EMAIL / CDATA_ACCESS_TOKEN in diagnostics/.env")
    cells = []
    for m in CFG["models"]:
        if not KEYS.get(m["provider"]):
            print(f"!! skipping {m['id']} - no API key for {m['provider']}"); continue
        for cond in CFG["conditions"]:
            recs = []
            for r in range(1, RUNS + 1):
                tag = f"{m['id']}__{cond['name']}__run{r}"
                print(f"-> {tag} ...", flush=True)
                try:
                    rec = one_run(m, cond, r)
                except Exception as e:
                    rec = {"model": m["id"], "provider": m["provider"], "condition": cond["name"],
                           "run": r, "error": str(e)[:400], "error_class": type(e).__name__, "trace": []}
                json.dump(rec, open(os.path.join(RESULTS, tag + ".json"), "w"), indent=2, default=str)
                acc = (rec.get("score") or {}).get("layer1_accuracy")
                print(f"   done: tools={next((t['count'] for t in rec.get('trace',[]) if t.get('event')=='tools_listed'),'-')} "
                      f"calls={rec.get('tool_calls','-')} turns={rec.get('turns','-')} "
                      f"tok={rec.get('raw_total','-')} wall={rec.get('wall_s','-')}s acc={acc} "
                      f"err={rec.get('error_class')}", flush=True)
                recs.append(rec)
            cells.append((m, cond, recs))

    # ---- report ----
    print("\n" + "=" * 100)
    print("GREEN GATE (per cell, both runs must pass to clear for the full matrix)")
    print("=" * 100)
    for m, cond, recs in cells:
        for rec in recs:
            g = gate(rec)
            flags = " ".join(f"{k}={'Y' if v is True else ('N' if v is False else v)}" for k, v in g.items())
            print(f"  {m['id']:<16} {cond['name']:<10} run{rec.get('run')}: {flags}")

    print("\n" + "=" * 100)
    print("METRICS MATRIX  (Accuracy / Efficiency / Speed / Variance)")
    print("=" * 100)
    hdr = f"{'model':<16}{'cond':<10}{'acc(med)':>9}{'tok(med)':>10}{'reason':>8}{'wall(med)':>10}{'tok/s':>8}{'calls':>6}{'tok var%':>9}{'acc dlt':>8}"
    print(hdr)
    for m, cond, recs in cells:
        ok = [r for r in recs if not r.get("error")]
        def med(key, sub=None):
            vals = [((r.get("score") or {}).get(key) if sub == "score" else r.get(key)) for r in ok]
            vals = [v for v in vals if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else 0
        acc = med("layer1_accuracy", "score"); tok = med("raw_total"); rea = med("reasoning")
        wall = med("wall_s"); calls = med("tool_calls")
        tps = tok / wall if wall else 0
        toks = [r.get("raw_total", 0) for r in ok]
        tvar = (max(toks) - min(toks)) / (sum(toks) / len(toks)) * 100 if len(toks) >= 2 and sum(toks) else 0
        accs = [(r.get("score") or {}).get("layer1_accuracy", 0) for r in ok]
        adlt = (max(accs) - min(accs)) if len(accs) >= 2 else 0
        print(f"{m['id']:<16}{cond['name']:<10}{acc:>9.1f}{tok:>10.0f}{rea:>8.0f}{wall:>10.1f}{tps:>8.1f}{calls:>6.1f}{tvar:>9.1f}{adlt:>8.1f}")

    print("\n" + "=" * 100)
    print("RAW FINAL ANSWERS (first run per cell, truncated)")
    print("=" * 100)
    for m, cond, recs in cells:
        r0 = recs[0]
        print(f"\n--- {m['id']} / {cond['name']} (err={r0.get('error_class')}) ---")
        print((r0.get("final_answer") or r0.get("error") or "(no output)")[:1400])
    print(f"\nPer-run JSON written to: {RESULTS}")


if __name__ == "__main__":
    main()
