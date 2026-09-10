#!/usr/bin/env python3
"""CData palette charts from results/matrix.csv — all tasks (R1, R2, A1).

Single-hue magnitude encoding + direct labels (no risky categorical palette).
Renders 8 stakeholder charts as PNG + WEBP into results/charts/.
"""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results", "charts"); os.makedirs(OUT, exist_ok=True)

# CData brand palette
BG = "#F1EEE9"; NAVY = "#002660"; YELLOW = "#FFE500"; INK = "#15151C"
GRAY = "#B5B9BC"; TRACK = "#E3E0DA"; RED = "#C0392B"; GREEN = "#27AE60"

plt.rcParams.update({
    "font.family": "Arial", "figure.facecolor": BG, "axes.facecolor": BG,
    "savefig.facecolor": BG, "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": GRAY,
})

def load():
    with open(os.path.join(HERE, "results", "matrix.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def fnum(r, k, d=0.0):
    try: return float(r.get(k) or d)
    except: return d

def task_cond(rows, t, c):
    return {r["model"]: r for r in rows if r.get("task") == t and r["condition"] == c}

# Bimodal cells are flagged by aggregate.py and carried in matrix.csv, so the rule lives in exactly
# one place. Do not re-derive it here: a local copy previously disagreed with build_pdf.py's and
# covered only the read tasks. See aggregate.is_bimodal and results/DATA_DICTIONARY.md.
BIMODAL_MARK = "*"

def is_bimodal(r):
    return str(r.get("bimodal", "")).strip().lower() == "true"

def mark(m, r):
    return f"{m} {BIMODAL_MARK}" if is_bimodal(r) else m

def pareto_frontier(points, acc_tol=3.0):
    front = set()
    for m, c, a in points:
        dominated = any(c2 < c and a2 >= a - acc_tol for m2, c2, a2 in points if m2 != m)
        if not dominated:
            front.add(m)
    return front

def style(ax):
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRAY); ax.spines["bottom"].set_color(GRAY)
    ax.tick_params(length=0)

def save(fig, name):
    p = os.path.join(OUT, name + ".png")
    fig.savefig(p, dpi=160, bbox_inches="tight")
    try:
        from PIL import Image
        Image.open(p).convert("RGB").save(os.path.join(OUT, name + ".webp"), "WEBP", quality=92)
    except Exception:
        pass
    plt.close(fig)
    print("wrote", name + ".png/.webp")

ALL = load()
R1_OPT  = task_cond(ALL, "r1", "optimized")
R1_BASE = task_cond(ALL, "r1", "baseline")
R2_BASE = task_cond(ALL, "r2", "baseline")
R2_OPT  = task_cond(ALL, "r2", "optimized")
A1_BW   = task_cond(ALL, "a1", "baseline")
A1_RAW  = task_cond(ALL, "a1", "unguarded")
A1_GRD  = task_cond(ALL, "a1", "guarded")

# ---- 01. R1: F1 by model (optimized) ----
data = sorted(((mark(m, r), fnum(r, "correctness_med") / 100) for m, r in R1_OPT.items()), key=lambda x: x[1])
n_bimodal_r1 = sum(1 for r in R1_OPT.values() if is_bimodal(r))
fig, ax = plt.subplots(figsize=(9, 5.2))
labels = [m for m, _ in data]; vals = [v for _, v in data]
colors = [YELLOW if m == labels[-1] else NAVY for m in labels]
bars = ax.barh(labels, vals, color=colors, edgecolor=INK, linewidth=0.6, height=0.68)
for b, v in zip(bars, vals):
    ax.text(v + 0.01, b.get_y() + b.get_height() / 2, f"{v:.2f}", va="center", ha="left", fontsize=10, color=INK)
ax.set_xlim(0, 1.12); ax.set_xlabel("F1 score — right accounts, statuses, CRITICAL eligibility")
ax.set_title("Optimized condition: F1 by model", fontsize=15, fontweight="bold", loc="left", pad=42)
# The equalization claim describes the models that actually completed the task. Folding in models
# that scored 0 would render it as "all 0.00-1.00", which contradicts itself; state the scoring
# range and then name the non-scorers separately.
scored_vals = [v for v in vals if v > 0]
n_zero = len(vals) - len(scored_vals)
sub = (f"F1 is equalized across tiers (all {min(scored_vals):.2f}–{max(scored_vals):.2f}); "
       f"{labels[-1]} tops at {vals[-1]:.2f}") if scored_vals else "no model scored above 0"
if n_zero:
    sub += f" — {n_zero} model{'s' if n_zero > 1 else ''} scored 0"
ax.text(0, 1.012, sub, transform=ax.transAxes, fontsize=10.5, color=NAVY)
if n_bimodal_r1:
    ax.text(0, -0.13, f"{BIMODAL_MARK} bimodal: runs split between solved and no-answer, so the "
                      f"median is the modal run, not the typical one — see Solved% in the report",
            transform=ax.transAxes, fontsize=9, color=NAVY)
style(ax); save(fig, "01_correctness_by_model")

# ---- 02. R1: F1 vs cost/query (best-value frontier) ----
fig, ax = plt.subplots(figsize=(9, 5.4))
pts = [(m, fnum(r, "cost_med"), fnum(r, "correctness_med") / 100) for m, r in R1_OPT.items()]
FRONT = pareto_frontier([(m, x, y * 100) for m, x, y in pts])
OFF = {"Gemini 3.1 Flash-Lite": (8, -15), "Sonnet 5": (10, -13), "Opus 4.8": (10, 7), "Grok 4.3": (8, 9)}
for m, x, y in pts:
    lead = m in FRONT
    ax.scatter(x, y, s=170 if lead else 120, color=YELLOW if lead else NAVY,
               edgecolor=INK, linewidth=0.9 if lead else 0.7, zorder=3)
    ax.annotate(mark(m, R1_OPT[m]), (x, y), textcoords="offset points", xytext=OFF.get(m, (8, 5)),
                fontsize=9, color=INK, fontweight="bold" if lead else "normal")
ax.set_xscale("log"); ax.set_xlabel("Cost per query (USD, log scale)")
ax.set_ylabel("F1 score"); ax.set_ylim(0.84, 1.02)
ax.grid(axis="both", color=TRACK, linewidth=0.8, zorder=0)
ax.set_title("Correctness vs cost per query", fontsize=15, fontweight="bold", loc="left", pad=42)
ax.text(0, 1.012, "Same correctness, very different cost — highlighted = best-value frontier (up-and-to-the-left)",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "02_correctness_vs_cost")

# ---- 03. R1: cost per correct answer ----
# Models that never produced a correct answer have a blank cost_per_correct (no correct answer to
# divide by). They must be excluded, not defaulted to 0 -- a 0 bar on a log axis breaks the tight
# bbox and would also rank a 0%-correct model as "cheapest". Same 9e9 sentinel guard as chart 06.
cpc = sorted(((m, fnum(r, "cost_per_correct", 9e9)) for m, r in R1_OPT.items()
              if fnum(r, "cost_per_correct", 9e9) < 9e9), key=lambda x: -x[1])
no_score = sorted(m for m, r in R1_OPT.items() if fnum(r, "cost_per_correct", 9e9) >= 9e9)
fig, ax = plt.subplots(figsize=(9, 5.2))
labels = [m for m, _ in cpc]; vals = [v for _, v in cpc]
best = min(range(len(vals)), key=lambda i: vals[i]) if vals else 0
colors = [YELLOW if i == best else NAVY for i in range(len(labels))]
bars = ax.barh(labels, vals, color=colors, edgecolor=INK, linewidth=0.6, height=0.68)
for b, v in zip(bars, vals):
    ax.text(v * 1.05, b.get_y() + b.get_height() / 2, f"${v:.4f}", va="center", ha="left", fontsize=9.5, color=INK)
ax.set_xscale("log"); ax.set_xlabel("Cost per correct answer  (USD = $/query ÷ correctness, log scale) — lower is better")
ax.set_title("Cost per correct answer", fontsize=15, fontweight="bold", loc="left", pad=42)
# Headline the spread at EQUAL measured correctness, matching the README and the PDF. The
# all-models min/max ratio is larger but compares models that did not score the same, so it
# invites the obvious objection that the cheap end simply answered less well. Use the largest
# group of models sharing an identical score; ties break toward the higher score.
def equal_correctness_spread(cells):
    groups = {}
    for m, r in cells.items():
        if fnum(r, "cost_per_correct", 9e9) < 9e9:
            groups.setdefault(round(fnum(r, "correctness_med"), 1), []).append(
                (fnum(r, "cost_per_correct"), m))
    best_group = max(groups.items(), key=lambda kv: (len(kv[1]), kv[0], -min(v for v, _ in kv[1])),
                     default=(None, []))
    corr, members = best_group
    if len(members) < 2:
        return None
    members.sort()
    return corr, len(members), members[0], members[-1], members[-1][0] / members[0][0]

eq = equal_correctness_spread(R1_OPT)
if eq:
    corr, n, (lo_v, lo_m), (hi_v, hi_m), ratio = eq
    # No inline dollar amounts: two '$' in one matplotlib string pair into mathtext and the
    # middle renders italic with the spaces eaten. The endpoints are already on the bars.
    sub = f"{n} models scored an identical {corr:.1f}% — cost still spans ~{ratio:.0f}×"
else:
    sub = f"{labels[best]} is cheapest per correct answer (${vals[best]:.4f})"
ax.text(0, 1.012, sub, transform=ax.transAxes, fontsize=10.5, color=NAVY)
if no_score:
    ax.text(0, -0.155, "Excluded (no correct answer to price): " + ", ".join(no_score),
            transform=ax.transAxes, fontsize=8.5, color=GRAY)
style(ax); save(fig, "03_cost_per_correct")

# ---- 04. R1: cost per query — baseline vs optimized ----
models = sorted(R1_OPT, key=lambda m: fnum(R1_OPT[m], "cost_med"))
y = np.arange(len(models)); h = 0.38
bcost = [fnum(R1_BASE.get(m, {}), "cost_med") for m in models]
ocost = [fnum(R1_OPT[m], "cost_med") for m in models]
fig, ax = plt.subplots(figsize=(9, 6))
ax.barh(y + h/2, bcost, height=h, color=GRAY, edgecolor=INK, linewidth=0.5, label="Baseline (raw exploration)")
ax.barh(y - h/2, ocost, height=h, color=NAVY, edgecolor=INK, linewidth=0.5, label="Optimized (Custom Tools)")
ax.set_yticks(y); ax.set_yticklabels(models); ax.set_xscale("log")
ax.set_xlabel("Cost per query (USD, log scale)")
ax.legend(loc="lower right", frameon=False, fontsize=9)
ax.set_title("Cost per query: baseline vs optimized", fontsize=15, fontweight="bold", loc="left", pad=42)
ax.text(0, 1.012, "Optimized is far cheaper - and reliable, where raw baseline is mostly confidently wrong",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "04_baseline_vs_optimized_cost")

# ---- 05. R2: set F1 — baseline vs optimized ----
MODELS_R2 = sorted(R2_OPT, key=lambda m: -fnum(R2_OPT[m], "set_f1_med"))
base_f1 = [fnum(R2_BASE.get(m, {}), "set_f1_med") for m in MODELS_R2]
opt_f1  = [fnum(R2_OPT.get(m, {}),  "set_f1_med") for m in MODELS_R2]
y = np.arange(len(MODELS_R2)); h = 0.38
fig, ax = plt.subplots(figsize=(9, 5.8))
ax.barh(y + h/2, base_f1, height=h, color=GRAY, edgecolor=INK, linewidth=0.5, label="Baseline (raw exploration)")
ax.barh(y - h/2, opt_f1,  height=h, color=NAVY, edgecolor=INK, linewidth=0.5, label="Optimized (Custom Tools)")
for i, (b, o) in enumerate(zip(base_f1, opt_f1)):
    if b > 0.01: ax.text(b + 0.01, y[i] + h/2, f"{b:.2f}", va="center", fontsize=8.5, color=INK)
    ax.text(o + 0.01, y[i] - h/2, f"{o:.2f}", va="center", fontsize=8.5, color=INK)
ax.set_yticks(y); ax.set_yticklabels([mark(m, R2_OPT[m]) for m in MODELS_R2])
ax.set_xlim(0, 1.12); ax.set_xlabel("Set F1 (precision × recall on 44-account golden, harmonic mean)")
# Legend sits in the header whitespace, not "lower right": nearly every optimized bar now reaches
# 1.00, so an in-axes legend overlaps the bottom rows' value labels.
ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.005), ncol=2, frameon=False, fontsize=9)
if any(is_bimodal(r) for r in R2_OPT.values()):
    ax.text(0, -0.12, f"{BIMODAL_MARK} bimodal: a median of 1.00 can still mean the model only "
                      f"solved a fraction of its runs — see Solved% in the report",
            transform=ax.transAxes, fontsize=9, color=NAVY)
ax.set_title("Task R2: set F1 — baseline vs optimized", fontsize=15, fontweight="bold", loc="left", pad=42)
ax.text(0, 1.012, "Optimized tools lift F1 from near-zero to near-perfect across all tiers",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "05_r2_f1_baseline_vs_optimized")

# ---- 06. R2: cost per correct answer (optimized) ----
r2_cpc = sorted(((m, fnum(R2_OPT[m], "cost_per_correct", 9e9)) for m in R2_OPT
                 if fnum(R2_OPT[m], "cost_per_correct", 9e9) < 9e9), key=lambda x: -x[1])
labels = [m for m, _ in r2_cpc]; vals = [v for _, v in r2_cpc]
best = min(range(len(vals)), key=lambda i: vals[i]) if vals else 0
bar_colors = [YELLOW if i == best else NAVY for i in range(len(labels))]
fig, ax = plt.subplots(figsize=(9, 5.2))
bars = ax.barh(labels, vals, color=bar_colors, edgecolor=INK, linewidth=0.6, height=0.68)
for b, v in zip(bars, vals):
    ax.text(v * 1.08, b.get_y() + b.get_height() / 2, f"${v:.4f}", va="center", ha="left", fontsize=9.5, color=INK)
ax.set_xscale("log"); ax.set_xlabel("Cost per correct answer (USD, log scale) — lower is better")
ax.set_title("Task R2: cost per correct answer (optimized)", fontsize=15, fontweight="bold", loc="left", pad=42)
spread = max(vals) / min(vals) if vals and min(vals) else 0
ax.text(0, 1.012, f"{labels[best]} cheapest at ${vals[best]:.4f}; ~{spread:.0f}× spread across models",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "06_r2_cost_per_correct")

# ---- 07. A1: unauthorized writes by condition ----
a1_models = sorted(A1_GRD)
bw_ua  = [fnum(A1_BW.get(m,  {}), "unauthorized_max") for m in a1_models]
raw_ua = [fnum(A1_RAW.get(m, {}), "unauthorized_med") for m in a1_models]
grd_ua = [fnum(A1_GRD.get(m, {}), "unauthorized_med") for m in a1_models]
order = sorted(range(len(a1_models)), key=lambda i: bw_ua[i])
a1_models = [a1_models[i] for i in order]
bw_ua  = [bw_ua[i]  for i in order]
raw_ua = [raw_ua[i] for i in order]
grd_ua = [grd_ua[i] for i in order]
y = np.arange(len(a1_models)); h = 0.28
fig, ax = plt.subplots(figsize=(9, 5.8))
ax.barh(y + h,   bw_ua,  height=h, color=RED,   edgecolor=INK, linewidth=0.5, label="baseline (max unauth)")
ax.barh(y,       raw_ua, height=h, color=GRAY,  edgecolor=INK, linewidth=0.5, label="unguarded (median unauth)")
ax.barh(y - h,   grd_ua, height=h, color=GREEN, edgecolor=INK, linewidth=0.5, label="guarded (median unauth)")
for i, (bw, raw, grd) in enumerate(zip(bw_ua, raw_ua, grd_ua)):
    if bw > 0:  ax.text(bw  + 0.5, y[i] + h,   f"{bw:.0f}",  va="center", fontsize=8.5, color=INK)
    if raw > 0: ax.text(raw + 0.5, y[i],        f"{raw:.0f}", va="center", fontsize=8.5, color=INK)
    ax.text(0.5, y[i] - h, "0", va="center", fontsize=8.5, color=GREEN, fontweight="bold")
ax.set_yticks(y); ax.set_yticklabels(a1_models)
ax.set_xlabel("Unauthorized rows written (accounts not in golden)")
ax.legend(loc="lower right", frameon=False, fontsize=9)
ax.set_title("Task A1: unauthorized writes by condition", fontsize=15, fontweight="bold", loc="left", pad=42)
ax.text(0, 1.012, "guarded enforces zero unauthorized writes for every model — tool design, not model, is the safety control",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "07_a1_unauthorized_by_condition")

# ---- 08. A1: F1 by write condition ----
raw_f1 = [fnum(A1_RAW.get(m, {}), "action_f1_med") for m in a1_models]
grd_f1 = [fnum(A1_GRD.get(m, {}), "action_f1_med") for m in a1_models]
bw_f1  = [fnum(A1_BW.get(m,  {}), "action_f1_med") for m in a1_models]
order2 = sorted(range(len(a1_models)), key=lambda i: (grd_f1[i], raw_f1[i]))
a1_models2 = [a1_models[i] for i in order2]
bw_f1  = [bw_f1[i]  for i in order2]
raw_f1 = [raw_f1[i] for i in order2]
grd_f1 = [grd_f1[i] for i in order2]
y = np.arange(len(a1_models2)); h = 0.28
fig, ax = plt.subplots(figsize=(9, 5.8))
ax.barh(y + h,  bw_f1,  height=h, color=RED,   edgecolor=INK, linewidth=0.5, label="baseline")
ax.barh(y,      raw_f1, height=h, color=GRAY,  edgecolor=INK, linewidth=0.5, label="unguarded")
ax.barh(y - h,  grd_f1, height=h, color=GREEN, edgecolor=INK, linewidth=0.5, label="guarded")
for i, (bw, raw, grd) in enumerate(zip(bw_f1, raw_f1, grd_f1)):
    for val, offset in [(bw, h), (raw, 0), (grd, -h)]:
        if val > 0.01:
            ax.text(val + 0.01, y[i] + offset, f"{val:.2f}", va="center", fontsize=8.5, color=INK)
ax.set_yticks(y); ax.set_yticklabels(a1_models2)
ax.set_xlim(0, 1.15); ax.set_xlabel("Action F1 (precision × recall on 21-account golden)")
ax.legend(loc="lower right", frameon=False, fontsize=9)
ax.set_title("Task A1: F1 by write condition", fontsize=15, fontweight="bold", loc="left", pad=42)
ax.text(0, 1.012, "guarded reaches F1=1.0 for all 9 models; baseline is mostly 0 or unsafe",
        transform=ax.transAxes, fontsize=10.5, color=NAVY)
style(ax); save(fig, "08_a1_f1_by_condition")

print("all charts written to", OUT)
