#!/usr/bin/env python3
"""Export raw per-run data (no aggregation) from the matrix JSONs -> results/runs_raw.csv.

One row per run, across every task. Read-task runs carry answer-correctness columns; action-task
runs carry end-state columns (what landed in REVIEW_QUEUE) plus the write-target safety check.
Columns not applicable to a run's task are left blank rather than zero, so "no data" and "zero"
stay distinguishable.

Scores are recomputed from each run's saved artifact (answer text, or the REVIEW_QUEUE snapshot),
so a scorer fix applies retroactively without re-running any models.
"""
import csv, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import scorer, tasks as tasklib

MATRIX = os.path.join(HERE, "results", "matrix")

COMMON = ["task", "model", "effort", "provider", "condition", "run", "correctness", "outcome",
          "cost_usd", "cost_per_correct"]
READ_COLS = ["trajectory", "productive_calls", "redundant_calls", "irrelevant_calls",
             "set_recall", "set_precision", "set_f1", "overlap_at_50", "rank_corr",
             "set_tolerated_boundary", "status_accuracy",
             "eligibility_accuracy", "eligibility_flag_accuracy", "eligibility_reason_accuracy",
             "trend_accuracy", "sessions_accuracy",
             "field_presence_ok", "poor_health_filter_ok", "sort_order_ok", "rows_returned"]
ACTION_COLS = ["rows_written", "accounts_written", "action_precision", "action_recall", "action_f1",
               "correct_accounts", "missed_accounts", "unauthorized_rows", "unauthorized_accounts",
               "duplicate_rows", "reason_accuracy",
               "write_target_ok", "offtarget_write_attempts", "offtarget_rows_landed",
               "offtarget_tables", "source_tables_changed", "source_integrity_checked"]
RUN_COLS = ["uncached_input", "cached_input", "output_tokens", "reasoning_tokens", "total_tokens",
            "reasoning_mode", "thinking_chars", "temperature_requested", "temperature_applied",
            "turn_cap", "wall_s", "model_time_s", "mcp_time_s",
            "tool_calls", "turns", "hit_turn_cap", "stop_reason", "error"]
COLS = COMMON + READ_COLS + ACTION_COLS + RUN_COLS


def main():
    TASKS = tasklib.load_tasks()
    recs = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(os.path.join(MATRIX, "*.json"))}

    rows = []
    for tag, r in sorted(recs.items()):
        task_id = tasklib.task_of(r)
        task = TASKS.get(task_id)
        s = tasklib.rescore(r, TASKS)
        l1 = s.get("layer1_accuracy")
        cost = r.get("cost_usd")
        row = {
            "task": task_id, "model": r.get("model"), "effort": r.get("effort"), "provider": r.get("provider"),
            "condition": r.get("condition"), "run": r.get("run"),
            "correctness": l1, "outcome": tasklib.outcome(r, s, task),
            "cost_usd": cost,
            "cost_per_correct": round(cost / (l1 / 100), 4) if (cost is not None and l1) else None,
        }
        if tasklib.is_action(task):
            for k in ACTION_COLS:
                v = s.get(k)
                if isinstance(v, list):
                    v = ",".join(v)
                elif isinstance(v, dict):   # e.g. {"DIM_ACCOUNT": 1}
                    v = ";".join(f"{tbl}+{n}" for tbl, n in sorted(v.items()))
                row[k] = v
        else:
            tj = scorer.trajectory_score(r) if r.get("condition") == "optimized" else {}
            row.update({"trajectory": tj.get("trajectory"), "productive_calls": tj.get("productive"),
                        "redundant_calls": tj.get("redundant"), "irrelevant_calls": tj.get("irrelevant")})
            for k in READ_COLS[4:]:
                row[k] = s.get(k)
        row.update({
            "uncached_input": r.get("uncached_input"), "cached_input": r.get("cached_input"),
            "output_tokens": r.get("output"), "reasoning_tokens": r.get("reasoning"),
            "total_tokens": r.get("raw_total"), "reasoning_mode": r.get("reasoning_mode"),
            "thinking_chars": r.get("thinking_chars"),
            "temperature_requested": r.get("temperature_requested"),
            "temperature_applied": r.get("temperature_applied"), "turn_cap": r.get("turn_cap"),
            "wall_s": r.get("wall_s"), "model_time_s": r.get("model_time_s"),
            "mcp_time_s": r.get("mcp_time_s"), "tool_calls": r.get("tool_calls"),
            "turns": r.get("turns"), "hit_turn_cap": r.get("hit_turn_cap"),
            "stop_reason": r.get("stop_reason"), "error": r.get("error_class"),
        })
        rows.append(row)

    out = os.path.join(HERE, "results", "runs_raw.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    by_task = {}
    for r in rows:
        by_task[r["task"]] = by_task.get(r["task"], 0) + 1
    print(f"wrote {out}  ({len(rows)} runs: " + ", ".join(f"{k}={v}" for k, v in sorted(by_task.items())) + ")")


if __name__ == "__main__":
    main()
