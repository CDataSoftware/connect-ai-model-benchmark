#!/usr/bin/env python3
"""Matrix runner - models x tasks x conditions x runs.

Resumable: each run saves its own JSON immediately; a completed (error-free) run is skipped on
re-launch, so a sleep/crash/rate-limit just needs a re-run to resume. Dispatches by provider,
computes provider-aware cost + reconciliation, records everything. Judging + charts are separate
post-hoc steps.

Tasks are declared in config/models.yaml (see the `tasks:` block). Read tasks are graded from the
model's answer text; the action task (A1) is graded on what actually landed in REVIEW_QUEUE, read
back over the harness's own credentials -- never through the toolkit under test.

  python3 run_matrix.py                 # everything enabled in the config
  python3 run_matrix.py --task r2       # one task (repeatable)
  python3 run_matrix.py --model gpt-5.5 # one model  (repeatable)
  python3 run_matrix.py --dry-run       # print the plan and exit
  python3 run_matrix.py --skip-price-check   # run despite a live-price mismatch
  python3 run_matrix.py --effort medium --model claude-opus-5-5   # one model at one level
  python3 run_matrix.py --task a1 --model grok-4.3 --runs 1 --out-dir results/smoke
                                        # cheap end-to-end smoke test, kept out of the real matrix
"""
import argparse, json, os, sys, time
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import mcp_client, pricing, runners, scorer, verifier
from core.envutil import load_env
load_env(os.path.join(HERE, ".env"))

CFG = yaml.safe_load(open(os.path.join(HERE, "config", "models.yaml"), encoding="utf-8"))
RESULTS = os.path.join(HERE, "results", "matrix"); os.makedirs(RESULTS, exist_ok=True)
DEFAULT_TURN_CAP = CFG.get("turn_cap", 12)
TEMPERATURE = CFG.get("temperature")
EMAIL = os.environ["CDATA_EMAIL"]; TOKEN = os.environ["CDATA_ACCESS_TOKEN"]

KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY", "xai": "XAI_API_KEY"}
DISPATCH = {"anthropic": runners.run_anthropic, "openai": runners.run_openai,
            "google": runners.run_gemini, "xai": runners.run_grok,
            "openai_compat": runners.run_openai_compat}

def cost(rec, m):
    ir = m["price_input_per_mtok"]; cr = m.get("price_cached_input_per_mtok", ir * 0.1); orr = m["price_output_per_mtok"]
    out = rec["output"] + (rec["reasoning"] if rec["provider"] == "google" else 0)  # gemini thoughts additive
    return round((rec["uncached_input"] * ir + rec["cached_input"] * cr + out * orr) / 1e6, 6)

# Reasoning levels each API accepts. "default" sends no reasoning setting, so the provider's own
# default applies. A model with thinking_budget (manual thinking) or on an openai_compat endpoint
# supports only "default" unless its entry lists effort_levels.
EFFORT_LEVELS = {"anthropic": ("default", "low", "medium", "high", "xhigh", "max"),
                 "openai": ("default", "low", "medium", "high", "xhigh"),
                 "xai": ("default", "low", "medium", "high", "xhigh"),
                 "google": ("default", "low", "medium", "high"),
                 "openai_compat": ("default",)}

def levels_supported(m):
    if m.get("effort_levels"):
        return tuple(m["effort_levels"])
    if m.get("thinking_budget"):
        return ("default",)
    return EFFORT_LEVELS[m["provider"]]

def levels_configured(m):
    e = m.get("effort", "default")
    return list(e) if isinstance(e, (list, tuple)) else [e]

def kwargs_for(m, level):
    lvl = None if level == "default" else level
    if m["provider"] in ("openai", "xai"):
        return {"reasoning_effort": lvl}
    if m["provider"] == "google":
        return {"thinking_level": lvl}
    if m["provider"] == "anthropic":
        return {"thinking_budget": m.get("thinking_budget"), "effort": lvl}
    if m["provider"] == "openai_compat":
        kw = {"base_url": m["base_url"]}
        if lvl:
            kw["reasoning_effort"] = lvl
        if m.get("stream"):
            kw["use_stream"] = True
        if m.get("max_tokens"):          # optional per-model cap; runner default otherwise
            kw["max_tokens"] = m["max_tokens"]
        return kw
    return {}

def done(path):
    if not os.path.exists(path): return False
    try:
        return not json.load(open(path)).get("error")
    except Exception:
        return False

def load_task(t):
    """Attach the task's prompt text and golden rows, failing loudly if either is missing."""
    t = dict(t)
    for key, name in (("prompt_file", "prompt"), ("golden_file", "golden")):
        path = os.path.join(HERE, t[key])
        if not os.path.exists(path):
            hint = " -- run `python3 make_goldens.py` first" if name == "golden" else ""
            raise SystemExit(f"task {t['id']}: missing {t[key]}{hint}")
        t[name] = (open(path, encoding="utf-8").read().strip() if name == "prompt"
                   else scorer.load_golden(path))
    return t

def score_run(task, rec, queue_rows):
    """Grade one run according to its task's scoring mode."""
    mode = task["scoring"]
    if mode == "answer":
        return scorer.score(rec.get("final_answer", ""), task["golden"])
    if mode == "answer_r2":
        return scorer.score_r2(rec.get("final_answer", ""), task["golden"])
    if mode == "action":
        s = scorer.score_action(queue_rows, task["golden"])
        # did it write anywhere it wasn't supposed to? invisible in REVIEW_QUEUE itself, so this
        # comes off the trace (only the generic universal tools can go off-target)
        s.update(scorer.write_target_score(rec))
        return s
    raise SystemExit(f"task {task['id']}: unknown scoring mode {mode!r}")

def tag_for(task, model_id, level, cond_name, run_idx):
    safe_id = model_id.replace("/", "--") + "@" + level  # '/' in Together ids breaks file paths
    if task.get("legacy_tag"):   # R1's already-completed runs keep their original filenames
        return f"{safe_id}__{cond_name}__run{run_idx}"
    return f"{safe_id}__{task['id']}__{cond_name}__run{run_idx}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="only these task ids")
    ap.add_argument("--model", action="append", help="only these model ids")
    ap.add_argument("--runs", type=int, help="override runs-per-condition (for smoke tests)")
    ap.add_argument("--out-dir", help="write run JSON here instead of results/matrix")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--effort", help="run every selected model at this one reasoning level "
                    "(default: each model's configured effort list)")
    ap.add_argument("--skip-price-check", action="store_true",
                    help="run even if configured prices differ from live provider prices")
    args = ap.parse_args()

    results_dir = os.path.join(HERE, args.out_dir) if args.out_dir else RESULTS
    os.makedirs(results_dir, exist_ok=True)

    tasks = [t for t in CFG["tasks"] if not args.task or t["id"] in args.task]
    if not tasks:
        raise SystemExit(f"no tasks matched {args.task}")
    models = [m for m in CFG["models"] if not args.model or m["id"] in args.model]
    if not models:
        raise SystemExit(f"no models matched {args.model}")
    tasks = [load_task(t) for t in tasks]

    if args.effort:
        unsupported = [m["id"] for m in models if args.effort not in levels_supported(m)]
        if unsupported:
            raise SystemExit(f"!! --effort {args.effort} is not supported by: {', '.join(unsupported)}\n"
                             f"   narrow the run with --model, or use --effort default")
        efforts = {m["id"]: [args.effort] for m in models}
    else:
        efforts = {m["id"]: levels_configured(m) for m in models}
        bad = [f"{m['id']} ({l})" for m in models for l in efforts[m["id"]] if l not in levels_supported(m)]
        if bad:
            raise SystemExit(f"!! configured effort not supported: {', '.join(bad)}")

    floating = pricing.unpinned(models)
    if floating:
        raise SystemExit(f"!! unpinned model ids (pin an explicit version): {', '.join(floating)}")
    verified, problems, unverified = pricing.check(models)
    print(f"Prices: {len(verified)} verified live, {len(unverified)} config-only (no provider price API)")
    for p in problems:
        print(f"!! price check: {p}")
    if problems and not args.skip_price_check:
        raise SystemExit("!! fix config/models.yaml, or re-run with --skip-price-check")
    verified = set(verified)

    # skip conditions explicitly disabled in the config (e.g. guarded while its tool is broken)
    plan = []
    for t in tasks:
        for c in t["conditions"]:
            c = dict(c)
            if args.runs:
                c["runs"] = args.runs
            if not c.get("enabled", True):
                print(f"-- skipping {t['id']}/{c['name']} (disabled in config)")
                continue
            if not os.environ.get(c["mcp_url_env"]):
                print(f"!! {t['id']}/{c['name']}: {c['mcp_url_env']} not set in .env - skipping")
                continue
            plan.append((t, c))

    needs_queue = any(t["scoring"] == "action" for t, _ in plan)
    queue = None
    src_baseline = {}
    if needs_queue:
        queue = verifier.ReviewQueue()
        # fail fast: a permissions problem here would otherwise surface dozens of ungradable runs in
        try:
            src_baseline = queue.preflight()
            print(f"OK  verifier can read + reset {queue.table}")
            print(f"OK  source-table baseline: {src_baseline}")
        except verifier.VerifierError as e:
            raise SystemExit(f"!! verifier preflight failed: {e}")

    per_level = sum(c["runs"] for t, c in plan)
    n_variants = sum(len(v) for v in efforts.values())
    total = per_level * n_variants
    print(f"\nPlan: {len(models)} models, {n_variants} model-effort variants x {len(plan)} task-conditions = {total} runs")
    by_level = {}
    for mid, lv in efforts.items():
        for l in lv: by_level.setdefault(l, []).append(mid)
    for l, mids in sorted(by_level.items()):
        print(f"   effort={l:<8} {len(mids)} models")
    for t, c in plan:
        print(f"   {t['id']:<4} {c['name']:<15} runs={c['runs']:<3} cap={c.get('turn_cap', DEFAULT_TURN_CAP):<3} "
              f"scoring={t['scoring']:<10} golden={len(t['golden'])} rows")
    if args.dry_run:
        return

    n = 0
    for m in models:
        # api_key_env in models.yaml overrides the provider default (use for Together, Groq, etc.)
        # omit api_key_env entirely for keyless local servers (Ollama) — runner receives "nokey"
        keyenv = m.get("api_key_env") or KEYS.get(m["provider"])
        if keyenv and not os.environ.get(keyenv):
            print(f"!! no key for {m['id']} ({m['provider']}) - skipping", flush=True); continue
        fn = DISPATCH[m["provider"]]
        api_key = os.environ.get(keyenv, "nokey") if keyenv else "nokey"
        for level in efforts[m["id"]]:
            kw = kwargs_for(m, level)
            for task, cond in plan:
                url = os.environ[cond["mcp_url_env"]]
                cap = cond.get("turn_cap", DEFAULT_TURN_CAP)
                is_action = task["scoring"] == "action"
                for r in range(1, cond["runs"] + 1):
                    n += 1
                    tag = tag_for(task, m["id"], level, cond["name"], r)
                    path = os.path.join(results_dir, tag + ".json")
                    if done(path):
                        print(f"[{n}/{total}] skip (done) {tag}", flush=True); continue
                    print(f"[{n}/{total}] {tag} ...", flush=True)
                    queue_rows = None; src_after = None
                    try:
                        if is_action and task.get("reset_before_run", True):
                            # every action run must start from an empty table, or it inherits the
                            # previous run's rows and both runs score wrong
                            queue.reset()
                        mcp = mcp_client.MCPClient(url, EMAIL, TOKEN)
                        for attempt in range(3):
                            try:
                                mcp.initialize(); break
                            except Exception:
                                if attempt == 2: raise
                                time.sleep(5)
                        rec = fn(m["id"], mcp, task["prompt"], cond["name"], r, api_key,
                                 turn_cap=cap, temperature=TEMPERATURE, **kw)
                    except Exception as e:
                        rec = {"model": m["id"], "provider": m["provider"], "condition": cond["name"], "run": r,
                               "error": str(e)[:400], "error_class": type(e).__name__, "trace": []}

                    # Post-run bookkeeping (reading REVIEW_QUEUE + the source-table snapshot back for
                    # scoring) is a SEPARATE failure domain from the model run itself, and deliberately
                    # NOT part of the try/except above: a transient verifier-side error here (as hit
                    # live -- a 502 from CData's REST API on the snapshot query, after the model had
                    # already run to completion) must not discard a real, already-paid-for model
                    # execution. If it fails, rec's tokens/cost/trace stay intact; only the action score
                    # is marked unavailable and the run is flagged for retry via the same "error" field
                    # done() already checks, rather than wiping rec back to a bare error dict.
                    if is_action and not rec.get("error"):
                        try:
                            queue_rows = queue.rows()   # the graded artifact for this run
                            src_after = queue.snapshot_sources()
                        except Exception as e:
                            rec["error"] = f"post-run verification failed (model run itself succeeded): {str(e)[:350]}"
                            rec["error_class"] = type(e).__name__
                    rec["task"] = task["id"]
                    if is_action:
                        rec["queue_rows"] = queue_rows
                        if src_after:
                            # attribute the delta to THIS run, then re-baseline so one dirty run doesn't
                            # make every later run look dirty
                            delta = {k: src_after[k] - src_baseline.get(k, src_after[k])
                                     for k in src_after}
                            rec["source_integrity"] = {"before": dict(src_baseline), "after": src_after,
                                                       "delta": {k: v for k, v in delta.items() if v}}
                            if rec["source_integrity"]["delta"]:
                                print(f"    !! OFF-TARGET WRITE landed in source tables: "
                                      f"{rec['source_integrity']['delta']} -- verify source tables and revert any off-target writes manually", flush=True)
                            src_baseline = src_after
                    if not rec.get("error"):
                        rec["score"] = score_run(task, rec, queue_rows)
                    rec["label"] = m["label"]; rec["mcp_url"] = url; rec["effort"] = level
                    rec["cost_usd"] = cost(rec, m) if not rec.get("error") else None
                    rec["price_per_mtok"] = {"input": m["price_input_per_mtok"],
                                             "cached": m.get("price_cached_input_per_mtok", m["price_input_per_mtok"] * 0.1),
                                             "output": m["price_output_per_mtok"]}
                    rec["price_verified_live"] = m["id"] in verified
                    if rec.get("resolved_model") and rec["resolved_model"] != m["id"]:
                        print(f"    !! {m['id']} was served by {rec['resolved_model']}", flush=True)
                    json.dump(rec, open(path, "w"), indent=2, default=str)
                    s = rec.get("score") or {}
                    if is_action:
                        extra = (f"rows={s.get('rows_written','-')} f1={s.get('action_f1','-')} "
                                 f"unauth={s.get('unauthorized_rows','-')} dupes={s.get('duplicate_rows','-')} "
                                 f"offtarget={s.get('offtarget_write_attempts','-')} "
                                 f"[{scorer.classify_action_outcome(s) if s else '-'}]")
                    else:
                        extra = f"acc={s.get('layer1_accuracy','-')}"
                    print(f"    done tok={rec.get('raw_total','-')} calls={rec.get('tool_calls','-')} turns={rec.get('turns','-')} "
                          f"wall={rec.get('wall_s','-')}s cost=${rec.get('cost_usd')} {extra} err={rec.get('error_class')}", flush=True)
                    time.sleep(3)  # inter-run spacing to ease provider load

    if queue is not None:
        try:
            queue.reset()   # leave the table clean for the next operator
            print("\nREVIEW_QUEUE reset after final run.")
        except verifier.VerifierError as e:
            print(f"\n!! could not reset REVIEW_QUEUE at the end: {e}")
    print(f"\nMatrix complete. Per-run JSON in: {results_dir}", flush=True)

if __name__ == "__main__":
    main()
