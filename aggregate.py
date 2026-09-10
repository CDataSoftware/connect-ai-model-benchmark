#!/usr/bin/env python3
"""Aggregate the matrix into matrix.csv + a printed summary, grouped by task x model x condition.

Headline for the READ tasks = correctness (Layer-1) + COST PER CORRECT ANSWER (cost / correctness),
which combines accuracy and cost into one deployment-relevant number. The rated trajectory measure
is retired (it was noise on a task with a known-optimal path); objective trajectory efficiency is
reported as a diagnostic.

For the ACTION task (A1) correctness is action F1 + reason accuracy, and the headline safety numbers
-- unauthorized rows, duplicate rows, off-target write attempts -- are reported SEPARATELY rather
than blended, since averaging a safety violation into an accuracy score hides exactly the thing the
governance claim is about.

Run after the matrix completes.
"""
import csv, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import scorer, tasks as tasklib

MATRIX = os.path.join(HERE, "results", "matrix")


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else 0.0


def rate(xs):
    """Mean of a boolean/0-1 series, 0 when empty."""
    xs = [x for x in xs if x is not None]
    return (sum(1 for x in xs if x) / len(xs)) if xs else 0.0


def load(dirpath):
    out = {}
    if not os.path.isdir(dirpath):
        return out
    for fn in sorted(os.listdir(dirpath)):
        if fn.endswith(".json"):
            out[fn[:-5]] = json.load(open(os.path.join(dirpath, fn)))
    return out


def main():
    TASKS = tasklib.load_tasks()
    recs = load(MATRIX)
    cells = {}
    for tag, r in recs.items():
        if r.get("error"):
            continue
        key = (tasklib.task_of(r), r["label"], r["provider"], r["condition"])
        cells.setdefault(key, []).append((tag, r))

    rows = []
    for (task_id, label, prov, cond), items in sorted(cells.items()):
        task = TASKS.get(task_id)
        scored = [tasklib.rescore(r, TASKS) for _, r in items]
        outcomes = [tasklib.outcome(r, s, task) for s, (_, r) in zip(scored, items)]
        n = len(items) or 1
        L1 = [s.get("layer1_accuracy", 0) for s in scored]
        toks = [r.get("raw_total", 0) for _, r in items]
        costs = [r.get("cost_usd") or 0 for _, r in items]
        walls = [r.get("wall_s", 0) for _, r in items]
        calls = [r.get("tool_calls", 0) for _, r in items]
        reason = [r.get("reasoning", 0) for _, r in items]

        correctness = round(med(L1), 1)
        # 6 decimals, and derived from the UNROUNDED median. At sub-cent costs a 4-decimal
        # cost_med is a large fraction of the value, and dividing an already-rounded cost
        # compounded the error -- published spread multiples then failed to reproduce from
        # runs_raw.csv (the R2 spread read 174.6x at 4dp vs 178.4x from the raw per-run data).
        cost_exact = med(costs)
        cost = round(cost_exact, 6)
        cpc = round(cost_exact / (correctness / 100), 6) if correctness > 0 else None
        row = {
            "task": task_id, "model": label, "provider": prov, "condition": cond, "runs": len(items),
            "correctness_med": correctness,
            "cost_med": cost, "cost_per_correct": cpc, "cost_med_per_1k_q": round(med(costs) * 1000, 2),
            "tokens_med": int(med(toks)), "reasoning_med": int(med(reason)),
            "wall_med": round(med(walls), 1),
            "tok_per_s": round(med(toks) / med(walls), 1) if med(walls) else 0,
            "calls_med": round(med(calls), 1),
            "correctness_range": round(max(L1) - min(L1), 1) if len(L1) > 1 else 0.0,
            "tok_var_pct": round((max(toks) - min(toks)) / (sum(toks) / len(toks)) * 100, 1)
                           if len(toks) > 1 and sum(toks) else 0.0,
        }

        if tasklib.is_action(task):
            # write-task view: correctness AND the safety numbers, kept separate on purpose
            row.update({
                "clean_rate": round(outcomes.count("clean") / n, 2),
                "unsafe_rate": round(outcomes.count("unsafe") / n, 2),
                "sloppy_rate": round(outcomes.count("sloppy") / n, 2),
                "no_write_rate": round(outcomes.count("no_writes") / n, 2),
                "wrong_set_rate": round((outcomes.count("wrong_set") + outcomes.count("wrong_reasons")) / n, 2),
                "action_f1_med": round(med([s.get("action_f1") for s in scored]), 3),
                "action_precision_med": round(med([s.get("action_precision") for s in scored]), 3),
                "action_recall_med": round(med([s.get("action_recall") for s in scored]), 3),
                "unauthorized_med": round(med([s.get("unauthorized_rows") for s in scored]), 1),
                "unauthorized_max": max([s.get("unauthorized_rows") or 0 for s in scored]),
                "duplicate_med": round(med([s.get("duplicate_rows") for s in scored]), 1),
                "reason_acc_med": round(med([s.get("reason_accuracy") for s in scored]) * 100, 1),
                "rows_written_med": round(med([s.get("rows_written") for s in scored]), 1),
                # "wrote only where it was supposed to" -- the dimension REVIEW_QUEUE can't show
                "write_target_ok_rate": round(rate([s.get("write_target_ok") for s in scored]), 2),
                "offtarget_attempts_med": round(med([s.get("offtarget_write_attempts") for s in scored]), 1),
                # rows that actually LANDED in a source table -- the cleanup number, summed across
                # the cell rather than medianed, since any nonzero total needs manual repair
                "offtarget_rows_landed_total": sum(s.get("offtarget_rows_landed") or 0 for s in scored),
            })
        else:
            row.update({
                "solved_rate": round(outcomes.count("solved") / n, 2),
                "wrong_rate": round(outcomes.count("wrong_answer") / n, 2),
                "timeout_rate": round((outcomes.count("timeout") + outcomes.count("no_answer")) / n, 2),
                # partial-credit view -- how far a run got beyond the binary solve bar
                "overlap_med": round(med([s.get("overlap_at_50") for s in scored]) * 100, 1),
                "set_f1_med": round(med([s.get("set_f1") for s in scored]), 3),
                "status_med": round(med([s.get("status_accuracy") for s in scored]) * 100, 1),
            })
            _rc = [s.get("rank_corr") for s in scored if isinstance(s.get("rank_corr"), (int, float))]
            row["rank_corr_med"] = round(statistics.median(_rc), 2) if _rc else None
            _el = [s.get("eligibility_accuracy") for s in scored if isinstance(s.get("eligibility_accuracy"), (int, float))]
            row["elig_med"] = round(statistics.median(_el) * 100, 1) if _el else None
            _tr = [s.get("trend_accuracy") for s in scored if isinstance(s.get("trend_accuracy"), (int, float))]
            row["trend_med"] = round(statistics.median(_tr) * 100, 1) if _tr else None
            # objective trajectory efficiency (diagnostic) -- only meaningful for curated toolkits
            if cond == "optimized":
                tj = [scorer.trajectory_score(r) for _, r in items]
                row["trajectory_med"] = round(med([t["trajectory"] for t in tj]), 1)
                row["redundant_med"] = round(med([t["redundant"] for t in tj]), 1)
                row["irrelevant_med"] = round(med([t["irrelevant"] for t in tj]), 1)
        rows.append(row)

    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    out_path = os.path.join(HERE, "results", "matrix.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---------------- printed summary, per task ----------------
    for task_id in sorted({r["task"] for r in rows}):
        task = TASKS.get(task_id)
        trs = [r for r in rows if r["task"] == task_id]
        print("\n" + "=" * 104)
        print(f"TASK {task_id.upper()}" + (f"  ({task['scoring']})" if task else ""))
        print("=" * 104)

        if tasklib.is_action(task):
            for cond in sorted({r["condition"] for r in trs}):
                print(f"\n  [{cond}]  correctness / SAFETY")
                print(f"  {'model':<22}{'correct%':>9}{'F1':>7}{'unauth':>8}{'dupes':>7}"
                      f"{'offtgt':>8}{'landed':>8}{'clean%':>8}{'unsafe%':>9}{'$/q':>9}")
                for r in sorted([x for x in trs if x["condition"] == cond], key=lambda x: x["model"]):
                    print(f"  {r['model']:<22}{r['correctness_med']:>9}{r['action_f1_med']:>7}"
                          f"{r['unauthorized_med']:>8}{r['duplicate_med']:>7}"
                          f"{r['offtarget_attempts_med']:>8}{r['offtarget_rows_landed_total']:>8}"
                          f"{r['clean_rate']*100:>8.0f}"
                          f"{r['unsafe_rate']*100:>9.0f}{r['cost_med']:>9.4f}")
        else:
            for cond in sorted({r["condition"] for r in trs}):
                sub = [x for x in trs if x["condition"] == cond]
                print(f"\n  [{cond}]")
                print(f"  {'model':<22}{'correct%':>9}{'$/query':>9}{'$/correct':>11}{'calls':>7}"
                      f"{'traj%':>7}{'solved%':>8}{'cVar':>6}")
                for r in sorted(sub, key=lambda x: (x["cost_per_correct"] if x["cost_per_correct"] is not None else 9e9)):
                    print(f"  {r['model']:<22}{r['correctness_med']:>9}{r['cost_med']:>9.4f}"
                          f"{(r['cost_per_correct'] or 0):>11.4f}{r['calls_med']:>7}"
                          f"{r.get('trajectory_med') or 0:>7}{r['solved_rate']*100:>8.0f}"
                          f"{r['correctness_range']:>6}")
                best = min(sub, key=lambda r: r["cost_per_correct"] if r["cost_per_correct"] is not None else 9e9)
                if best["cost_per_correct"] is not None:
                    print(f"    best cost per correct answer: {best['model']} (${best['cost_per_correct']:.4f})")

    print(f"\nWrote {out_path}  ({len(rows)} cells)")


if __name__ == "__main__":
    main()
