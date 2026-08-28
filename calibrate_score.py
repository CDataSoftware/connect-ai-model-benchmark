#!/usr/bin/env python3
"""Score human Layer-2 calibration sheets against the rated panel.

Reads calibration/key.json + calibration/answers_<name>.csv (one per reviewer) and reports,
per dimension: quadratic-weighted Cohen's kappa and within-1 agreement (human vs panel median);
overall: Spearman rank correlation and MAE on the 0-100 L2 scale; plus human-human agreement
if >=2 reviewers (the ceiling). Compares to the rubric's target_agreement band. Pure-Python
(no scipy/numpy).

Usage: python calibrate_score.py
"""
import json, os, csv, glob, statistics, math
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "calibration")
RUBRIC = yaml.safe_load(open(os.path.join(HERE, "config", "rubric.yaml"), encoding="utf-8"))
DIMS = [d["name"] for d in RUBRIC["trajectory_judge"]["dimensions"]]
TARGET = RUBRIC.get("calibration", {}).get("target_agreement", [0.75, 0.90])
key = json.load(open(os.path.join(OUT, "key.json")))


def quad_weighted_kappa(a, b, cats=(1, 2, 3, 4, 5)):
    """Quadratic-weighted Cohen's kappa on ordinal 1-5 ratings."""
    idx = {c: i for i, c in enumerate(cats)}; K = len(cats); n = len(a)
    O = [[0] * K for _ in range(K)]
    for x, y in zip(a, b):
        O[idx[int(round(x))]][idx[int(round(y))]] += 1
    row = [sum(O[i]) for i in range(K)]; col = [sum(O[i][j] for i in range(K)) for j in range(K)]
    W = [[((i - j) ** 2) / ((K - 1) ** 2) for j in range(K)] for i in range(K)]
    E = [[row[i] * col[j] / n for j in range(K)] for i in range(K)]
    num = sum(W[i][j] * O[i][j] for i in range(K) for j in range(K))
    den = sum(W[i][j] * E[i][j] for i in range(K) for j in range(K))
    return (1 - num / den) if den else float("nan")


def pearson(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x)); dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx and dy else float("nan")


def spearman(x, y):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for t in range(i, j + 1):
                r[order[t]] = avg
            i = j + 1
        return r
    return pearson(ranks(x), ranks(y))


def load_sheets():
    sheets = {}
    for f in sorted(glob.glob(os.path.join(OUT, "answers_*.csv"))):
        name = os.path.basename(f)[len("answers_"):-4]
        if name == "template":
            continue
        rows = {r["blind_id"]: r for r in csv.DictReader(open(f))}
        if any((rows[b].get(DIMS[0]) or "").strip() for b in rows):
            sheets[name] = rows
    return sheets


def overall_l2(row):
    try:
        vals = [float(row[d]) for d in DIMS]
    except (ValueError, KeyError):
        return None
    return (sum(vals) / len(vals) - 1) / 4 * 100


def main():
    sheets = load_sheets()
    if not sheets:
        raise SystemExit("No filled sheets. Copy calibration/answers_template.csv -> answers_<name>.csv and fill 1-5.")
    lo, hi = TARGET
    print(f"Layer-2 calibration | target agreement band: {lo}-{hi} (weighted-kappa)\n")

    for name, rows in sheets.items():
        ids = [b for b in key if b in rows and all((rows[b].get(d) or "").strip() for d in DIMS)]
        print(f"=== reviewer '{name}' vs rated panel  (n={len(ids)}) ===")
        for d in DIMS:
            h = [float(rows[b][d]) for b in ids]; p = [key[b]["panel_median"][d] for b in ids]
            kap = quad_weighted_kappa(h, p)
            w1 = sum(1 for a, b in zip(h, p) if abs(a - b) <= 1) / len(h)
            flag = "OK" if kap >= lo else "LOW"
            print(f"  {d:20} weighted-kappa={kap:+.2f} [{flag}]   within-1={w1:.0%}")
        ho = [overall_l2(rows[b]) for b in ids]; po = [key[b]["layer2_accuracy"] for b in ids]
        mae = statistics.mean(abs(a - b) for a, b in zip(ho, po))
        print(f"  overall L2 (0-100)   spearman={spearman(ho, po):+.2f}   MAE={mae:.1f} pts\n")

    names = list(sheets)
    if len(names) >= 2:
        a, b = names[0], names[1]
        ids = [x for x in key if all((sheets[a].get(x, {}).get(d) or "").strip()
               and (sheets[b].get(x, {}).get(d) or "").strip() for d in DIMS)]
        print(f"=== inter-rater ceiling: '{a}' vs '{b}'  (n={len(ids)}) ===")
        for d in DIMS:
            ha = [float(sheets[a][x][d]) for x in ids]; hb = [float(sheets[b][x][d]) for x in ids]
            print(f"  {d:20} weighted-kappa={quad_weighted_kappa(ha, hb):+.2f}")
        print()

    print("Read-out:")
    print(f"  human-panel kappa >= {lo} on a dimension -> the judge tracks humans there (validated).")
    print("  kappa low AND human-human also low -> the construct is fuzzy; the fix is a better/objective")
    print("  rubric, not a better judge. Low human-panel but high human-human -> judge is the problem;")
    print("  drop or down-weight Layer-2 (currently 30% of the blend).")


if __name__ == "__main__":
    main()
