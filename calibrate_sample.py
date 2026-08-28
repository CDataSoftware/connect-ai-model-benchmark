#!/usr/bin/env python3
"""Sample optimized runs for human Layer-2 (trajectory) calibration.

Emits a BLIND review packet (model name + the panel's scores hidden), a blank
answer sheet, and a private key. A human scores each trajectory 1-5 on the three
rubric dimensions -- judging the *process*, NOT whether the final answer is correct.
calibrate_score.py then measures agreement between the human and the rated panel.

The packet shows exactly what the judge saw (same trace format, same answer truncation)
so the comparison is fair. Sampling is stratified across the panel's L2 range so the
human sees high-, mid-, and low-scored trajectories (tests whether the judge discriminates).

Usage: python calibrate_sample.py [N]        # default 12
"""
import json, os, random, sys, csv
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX = os.path.join(HERE, "results", "matrix")
JUDGE = os.path.join(HERE, "results", "judge")
OUT = os.path.join(HERE, "calibration"); os.makedirs(OUT, exist_ok=True)
RUBRIC = yaml.safe_load(open(os.path.join(HERE, "config", "rubric.yaml"), encoding="utf-8"))
PROMPT = open(os.path.join(HERE, "config", "prompt.txt"), encoding="utf-8").read().strip()
DIMS = [d["name"] for d in RUBRIC["trajectory_judge"]["dimensions"]]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
random.seed(20260721)


def packet_parts(rec):
    """Same content the judge received (see judge.py packet())."""
    tools = next((t["names"] for t in rec.get("trace", []) if t.get("event") == "tools_listed"), [])
    calls = [t for t in rec.get("trace", []) if t.get("tool")]
    trace = "\n".join(f"  {i+1}. turn{c['turn']} {c['tool']}({json.dumps(c['args'])[:80]}) -> {c['result_chars']}ch"
                      for i, c in enumerate(calls)) or "  (no tool calls)"
    ans = (rec.get("final_answer") or "")[:2000]
    return tools, len(calls), rec.get("turns"), rec.get("hit_turn_cap"), trace, ans


# optimized runs that have a valid judge score
runs = []
for fn in sorted(os.listdir(MATRIX)):
    if not fn.endswith(".json"):
        continue
    rec = json.load(open(os.path.join(MATRIX, fn)))
    if rec.get("error") or rec.get("condition") != "optimized":
        continue
    jp = os.path.join(JUDGE, fn)
    if not os.path.exists(jp):
        continue
    j = json.load(open(jp))
    if j.get("error") or j.get("layer2_accuracy") is None or not j.get("panel_median"):
        continue
    runs.append((fn[:-5], rec, j))

if not runs:
    sys.exit("No judged optimized runs found. Run judge.py first.")

# stratify across the L2 range: rank by L2, split into N bands, pick one per band
runs.sort(key=lambda x: x[2]["layer2_accuracy"])
k = len(runs)
picks = runs if k <= N else [random.choice(runs[b * k // N: (b + 1) * k // N] or [runs[b * k // N]]) for b in range(N)]
random.shuffle(picks)  # blind order (so rank != L2)

anchors = "\n".join(f"- **{d['name']}** — 1 = {d['anchor_1']} · 3 = {d['anchor_3']} · 5 = {d['anchor_5']}"
                    for d in RUBRIC["trajectory_judge"]["dimensions"])
md = [
    "# Layer-2 trajectory calibration — blind review packet\n",
    "Score each item **1–5** on the three dimensions. Judge the **process / trajectory only** — "
    "NOT whether the final answer is correct (that is scored separately, deterministically). "
    "A model can reach a *wrong* answer via a clean trajectory and still score high here.\n",
    "Enter your scores in a copy of `answers_template.csv` named `answers_<yourname>.csv`, then run "
    "`python calibrate_score.py`. Do **not** open `key.json` while scoring.\n",
    f"## Rubric anchors\n{anchors}\n",
    f"## Task (identical for every item)\n> {PROMPT}\n",
    "---\n",
]
key = {}
for i, (tag, rec, j) in enumerate(picks):
    bid = f"R{i+1:02d}"
    key[bid] = {"tag": tag, "model": rec["model"], "layer2_accuracy": j["layer2_accuracy"],
                "panel_median": {d: j["panel_median"][d] for d in DIMS}}
    tools, ncalls, turns, cap, trace, ans = packet_parts(rec)
    md += [
        f"### {bid}\n",
        f"**Tools available:** {', '.join(tools)}\n",
        f"**Tool-call trace** ({ncalls} calls, {turns} turns, hit_cap={cap}):\n```\n{trace}\n```\n",
        f"**Final answer (truncated to 2000 chars):**\n```\n{ans}\n```\n",
        f"> Score **{bid}** — tool_selection: __  planning_efficiency: __  error_recovery: __\n",
        "\n---\n",
    ]

open(os.path.join(OUT, "packet.md"), "w", encoding="utf-8").write("\n".join(md))
with open(os.path.join(OUT, "answers_template.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["blind_id"] + DIMS + ["notes"])
    for bid in key:
        w.writerow([bid] + [""] * len(DIMS) + [""])
json.dump(key, open(os.path.join(OUT, "key.json"), "w"), indent=2)

l2s = [j["layer2_accuracy"] for _, _, j in picks]
print(f"Sampled {len(picks)} trajectories (of {k} judged) -> {OUT}/")
print("  packet.md             <- read + score this (blind)")
print("  answers_template.csv  <- copy to answers_<name>.csv and fill 1-5")
print("  key.json              <- private; do NOT look while scoring")
print(f"L2 range covered: {min(l2s):.0f}..{max(l2s):.0f}  (spread ensures you see good & bad paths)")
