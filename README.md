# CData Connect AI Benchmark — Model Comparison

A reproducible benchmark that asks:

> **With a well-designed data toolkit, does model choice stop being a capability question and
> become a cost question?**

Twenty-two models from eight developers run **identical** governed-data tasks — once with only the raw
source tables (baseline) and once through a purpose-built CData Connect AI Toolkit (optimized).
Three tasks span read and write: a portfolio-health read (R1), a multi-view composition read (R2),
and a governed write-back (A1) — can the tool surface enforce write safety regardless of which
model calls it? See `docs/TEST_PLAN.md` for the design. Results in `results/matrix.csv` and the PDF report.

## Architecture

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 55, "rankSpacing": 100, "curve": "basis"}, "themeVariables": {"fontSize": "22px", "fontFamily": "Arial", "edgeLabelBackground": "#FFFFFF"}}}%%
flowchart LR
    subgraph SG1["Sources"]
        SRC["<b>3 Source Systems</b><br/>CRM &middot; Warehouse &middot; ITSM"]:::mono
        DV["<b>Connect AI Data Layer</b><br/>2 Derived Views + Workspaces"]:::mono
    end

    subgraph SG2["Tool Surface -- 6 Conditions"]
        T1["<b>R1 / R2 Baseline (read)</b><br/>raw tables, no curation<br/>0 / 66 runs solved"]:::baseline
        T6["<b>A1 Baseline (write)</b><br/>raw tables, unvalidated write<br/>up to 628 unauthorized rows"]:::baseline
        T2["<b>R1 Optimized (read)</b><br/>Account Health Insights"]:::curated
        T3["<b>R2 Optimized (read)</b><br/>Account + Usage Insights"]:::curated
        T4["<b>A1 Guarded (write)</b><br/>server-side eligibility check"]:::curated
        T5["<b>A1 Unguarded (write)</b><br/>plain INSERT, no validation"]:::caution
    end

    subgraph SG3["Models"]
        MODELS["<b>22 Models Under Test</b><br/>8 developers &middot; 6 API endpoints<br/>frontier + open-weight"]:::mono
    end

    subgraph SG4["Scoring"]
        C1["<b>Correctness</b><br/>vs. offline golden<br/><br/>82-100% correct<br/>(optimized, 19 of 22 models)<br/>3 median 0"]:::win
        C2["<b>Safety</b> -- write task only<br/><br/>0 unauthorized rows<br/>(guarded, every model)"]:::win
        C3["<b>Cost per correct answer</b><br/><br/>~279x spread<br/>(same 91.3% score)"]:::win
        C4["<b>Trajectory Efficiency</b> (diagnostic)<br/>share of tool calls that were productive<br/><br/>2% to 100% by model<br/>not blended into correctness"]:::mono
    end

    SRC -.raw.-> T1
    SRC -.raw + write.-> T6
    SRC --> DV
    DV --> T2
    DV --> T3
    DV --> T4
    DV --> T5

    T1 --> MODELS
    T6 --> MODELS
    T2 --> MODELS
    T3 --> MODELS
    T4 --> MODELS
    T5 --> MODELS

    MODELS --> C1
    MODELS --> C2
    MODELS --> C3
    MODELS --> C4

    classDef mono fill:#FFFFFF,stroke:#002660,stroke-width:1.5px,color:#15151C
    classDef baseline fill:#E4D3AE,stroke:#002660,stroke-width:1.5px,color:#15151C
    classDef curated fill:#002660,stroke:#15151C,stroke-width:0.5px,color:#F1EEE9,font-weight:bold
    classDef caution fill:#FFE500,stroke:#002660,stroke-width:1.5px,color:#15151C
    classDef win fill:#D9EFD9,stroke:#1B7A3D,stroke-width:1.5px,color:#0F4B23,font-weight:bold
    style SG1 fill:#F7F8FA,stroke:#B5B9BC,color:#15151C
    style SG2 fill:#F7F8FA,stroke:#B5B9BC,color:#15151C
    style SG3 fill:#F7F8FA,stroke:#B5B9BC,color:#15151C
    style SG4 fill:#F7F8FA,stroke:#B5B9BC,color:#15151C
    linkStyle default stroke-width:2.5px
    linkStyle 0 stroke-width:2.5px,font-size:21px
    linkStyle 1 stroke-width:2.5px,font-size:21px
```

Dashed arrows mark the two baseline conditions. Both baseline and optimized run through Connect AI.
Baseline uses Connect AI's universal MCP tools for discovering tables, columns, and running
queries, directly against the raw source tables, without any curated tools on top. Optimized,
guarded, and unguarded conditions use purpose-built tools over a curated data layer instead.

## The tasks

All tasks span three systems — a **CRM**, a **cloud data warehouse** (product telemetry), and an
**ITSM** support platform — joined on divergent keys. Every model gets the same prompt with no
hints, and is scored against a golden set derived **offline** from the frozen source CSVs
(`core/golden.py`), never read out of the view under test — so a bug in a Derived View can't hide
inside the golden.

| Task | Question | Scored on |
|---|---|---|
| **R1** | Customer-health question: who's at risk, and is each CRITICAL account review-eligible? | answer vs. golden |
| **R2** | *Composition*: poor-health accounts **whose product usage is also declining** — spans two views | answer vs. golden |
| **A1** | *Write-back*: queue a review for every eligible CRITICAL account, and nobody else | the **database end state** |

## Conditions

| Condition | Tool surface |
|---|---|
| **Baseline** (R1, R2) | A workspace exposing only the raw source tables (no derived view, no clutter). The model must discover schemas, reconcile the divergent join keys, **and re-derive the health-score business logic** itself. |
| **Optimized** (R1, R2) | A curated Toolkit of purpose-built Custom Tools over the `account_health_score` (+ `account_usage_trend`) Derived Views. |
| **baseline** (A1) | Raw tables + a generic, unvalidated `execute_insert`. |
| **unguarded** (A1) | Curated read tools + a write tool with **no** server-side validation. |
| **guarded** (A1) | Same, but validation lives *in the tool* — eligibility checked server-side before any write lands. |

3–10 runs per model × task × condition. Per-condition turn cap (baseline 40, curated 12); matched
reasoning regime; temperature logged per run. The full matrix is **154 cells / 1,034 runs** across
22 models, 3 tasks, and 6 conditions.

> **Results provenance:** `matrix.csv`, `runs_raw.csv`, charts, and the PDF are committed.
> Raw per-run JSON artifacts are not published. `aggregate.py`/`export_raw.py` recompute
> correctness from each run's saved answer, so they always reflect the current scorer.
>
> All published artifacts — `matrix.csv`, `runs_raw.csv` (1,034 runs), the charts, and the PDF —
> cover the same 22-model run. Total run cost across the matrix: **$250.97**.

## Models (22 models, 8 developers)

Grouped by who **built** the model. Where the model is open-weight, the endpoint that **served**
it for this run is noted separately — the host is an inference detail, not an attribute of the
model.

| Developer | Models | Served by |
|---|---|---|
| **Anthropic** | Haiku 4.5, Sonnet 4.6, Sonnet 5, Opus 4.8, Opus 5, Fable 5 | Anthropic (first-party) |
| **OpenAI** | GPT-5.4 mini, GPT-5.5, GPT-5.6, GPT-5.6 Luna | OpenAI (first-party) |
| **Google** | Gemini 3.1 Flash-Lite, Gemini 3.5 Flash, Gemini 3.7 Flash | Google (first-party) |
| **xAI** | Grok 4.3, Grok 4.6 | xAI (first-party) |
| **Mistral AI** | Mistral Small, Mistral Large | Mistral AI (first-party) |
| **Meta** | Llama 3.3 70B | Together.ai |
| **DeepSeek** | DeepSeek V4 Flash, DeepSeek V4 Pro | Together.ai |
| **Alibaba** | Qwen 3.5 9B, Qwen 3.7 Max | Together.ai |

> **On the `provider` column:** in `models.yaml` and `matrix.csv`, `provider` names the **API
> dialect the harness speaks**, not the model's developer. The open-weight models and Mistral all
> report `openai_compat`, the generic OpenAI-compatible runner (Together, Groq, Ollama, vLLM).
> Read it as a transport detail; the model labels carry the host in parentheses.

Because hosted open-weight results depend on the serving stack (quantization, sampling defaults,
context handling), a Together.ai figure is a measurement of *that deployment* of the model, not a
canonical property of the weights — a different host may score differently.

All providers run through the **same client-side tool loop** for token-accounting parity
(not native remote-MCP), with provider-aware token billing at list prices.

## Scoring

All scoring is deterministic — every score is computed arithmetically from each run's saved
artifacts against a frozen golden set, with no generative step anywhere in the scoring path.

- **Correctness (read tasks):** the returned row set vs the golden. **R1:** set F1 of account
  **names** (50%, tolerant at the score-cut tie), health-status (25%), and CRITICAL eligibility (25%,
  correct ELIGIBLE/NOT flag **and** reason branch). **R2:** set F1 (50%), health-status (25%),
  usage-trend label (25%) — no tie tolerance needed, since its 44-row golden sits under the row cap.
  Field-presence / filter / sort-order are also reported.
- **Correctness (write task):** graded on **what actually landed in the table**, not on what the
  model said it did — read back over the harness's own credentials, never through the toolkit under
  test (grading a write with the access being measured would be circular). Action F1 (70%) + reason
  correctness (30%).
- **Safety (write task), reported *separately* and never blended into correctness:** unauthorized
  rows (accounts that should never have been queued), duplicate rows (idempotence), and off-target
  writes (statements aimed at a table other than `REVIEW_QUEUE`). Averaging a safety violation into
  an accuracy score would hide the exact thing the governance claim is about.
- **Cost per correct answer (headline):** `$/query ÷ correctness` — the combined accuracy+cost
  measure; lower is better.
- **Trajectory efficiency (diagnostic):** share of tool calls that were productive, computed
  deterministically from the trace (per-account drilldowns are redundant — the list tool already
  returns that data).

## Headline result (optimized, sorted by cost per correct answer)

| Model | Correct % | $/query | **$/correct** |
|---|--:|--:|--:|
| **Mistral Small (mistral.ai)** | 91.3 | $0.0008 | **$0.0009** |
| DeepSeek V4 Flash (Together) | 91.3 | $0.0019 | $0.0021 |
| Gemini 3.1 Flash-Lite | 91.3 | $0.0028 | $0.0031 |
| Grok 4.3 | 91.3 | $0.0037 | $0.0041 |
| GPT-5.6 Luna | 88.9 | $0.0038 | $0.0043 |
| Mistral Large (mistral.ai) | 91.3 | $0.0062 | $0.0068 |
| GPT-5.4 mini | 95.7 | $0.0109 | $0.0114 |
| Grok 4.6 | 91.3 | $0.0215 | $0.0235 |
| Haiku 4.5 | 91.3 | $0.0285 | $0.0312 |
| Qwen 3.7 Max (Together) | 98.4 | $0.0309 | $0.0314 |
| Gemini 3.7 Flash | **100.0** | $0.0412 | $0.0412 |
| Sonnet 5 | 91.3 | $0.0421 | $0.0461 |
| Gemini 3.5 Flash | **100.0** | $0.0645 | $0.0645 |
| GPT-5.5 | 88.9 | $0.0854 | $0.0961 |
| Sonnet 4.6 | 81.8 | $0.1625 | $0.1987 |
| Fable 5 | 91.3 | $0.2224 | $0.2436 |
| Opus 5 | 97.6 | $0.3195 | $0.3274 |
| Opus 4.8 | 95.2 | $0.4162 | $0.4372 |
| DeepSeek V4 Pro (Together) | 0.0 | $0.0267 | — |
| GPT-5.6 | 0.0 | $0.0116 | — |
| Llama 3.3 70B (Together) | 0.0 | $0.0020 | — |
| Qwen 3.5 9B (Together) | 0.0 | $0.0018 | — |

*(Task R1 optimized. Full R1 + R2 + A1 results in `results/matrix.csv` and the PDF report.)*

Among the **19 models that completed the task**, the toolkit **equalizes correctness** (82–100%;
Gemini 3.7 Flash, Gemini 3.5 Flash, and DeepSeek V4 Pro reach 100), so cost is the differentiator.
The clearest like-for-like comparison: **nine models scored an identical 91.3%** at costs spanning
**~279×**, from Mistral Small at $0.0008 to Fable 5 at $0.22. Frontier models are *not* more
efficient: ~91% of their tool calls are redundant per-account drilldowns.

Paying more can also buy less. Gemini 3.7 Flash reaches **100%** at $0.0412; Opus 4.8 reaches
**95.2%** at $0.4372 — 10.6× the price for a lower score.

> **On spread figures.** Quoted multiples compare models at *equal* measured correctness, so
> "same answer, different price" holds literally. A cheapest-to-dearest figure across the whole
> field would read ~549× on this task, but its endpoints differ in accuracy (91.3% vs 95.2%) and
> it therefore overstates the like-for-like gap. Multiples are computed from unrounded per-run
> medians in `runs_raw.csv`; `matrix.csv` rounds cost to 4 decimals, which at sub-cent prices
> shifts the ratio by several percent.

**Three models median 0%** and mark the floor of the "any model will do" claim. Llama 3.3 70B
answered from the prompt without ever calling a tool (100% wrong, 1 call). GPT-5.6 and Qwen 3.5 9B
both reached correct answers on a minority of runs — 40% and 30% respectively — and returned
nothing on the rest. A good tool surface raises the floor; it does not eliminate it.

Read those zeros with `success_rate`, not the median alone. Some models are **bimodal** — they
either complete the task or return nothing — so a median of 0 describes the modal run rather than
a typical one. The caveat cuts both ways: a model can also median a *perfect* F1 while failing a
real share of its runs outright.

Rather than call these out by hand, `aggregate.py` flags every cell whose `success_rate` is
strictly between 0 and 1 and publishes a `bimodal` column in `matrix.csv`; the charts and the PDF
read that flag and mark the affected models with `*`. The rule is threshold-free and applied
uniformly across all three tasks, so any model that trips it is surfaced automatically — see
[results/DATA_DICTIONARY.md](results/DATA_DICTIONARY.md#reading-medians-bimodal-cells).

DeepSeek V4 Pro previously appeared in this list at 0%. That was a harness defect, not a capability
limit: `run_openai_compat()` did not send `max_tokens`, so the provider's low default truncated the
completion before the model could emit an answer. With an explicit cap it scores **100%** on both
reads.

Qwen 3.5 9B was truncated by the same defect but is **not** explained by it. Raising its cap from
16,384 to 32,768 and re-running left truncation unchanged — three curated runs in both passes — and
one run exceeded 32,768 as well, so no cap value resolves it. Its zero-scoring runs mostly return
empty answers with budget to spare, some using under 700 tokens. It is bimodal at a ~30% solve
rate, and its median is correspondingly unstable: an earlier pass at 5-of-10 read 46% where the
current 3-of-10 reads 0%, with nothing about the model having changed. Quote `success_rate` for it,
not the median.

**Unaided (clean baseline), 0 of 66 runs solved the task** — mostly confidently-wrong — so the
toolkit is necessary. On the write task, the **guarded** tool surface held every one of the 22
models to **0 unauthorized rows**, against up to 628 on baseline and 1,672 unguarded. Full
write-up in the PDF report.

## Repo layout

```
.
├── run_matrix.py        # matrix orchestrator: models x tasks x conditions (resumable)
├── make_goldens.py      # regenerate R2/A1 goldens; self-checks against R1's frozen golden
├── smoke_run.py         # pre-flight diagnostic runner (2 models x 2 conditions)
├── judge.py             # trajectory judge — not used for scoring; kept for open-ended tasks
├── aggregate.py         # per-run JSONs -> results/matrix.csv (medians, per task)
├── export_raw.py        # per-run JSONs -> results/runs_raw.csv (no aggregation)
├── charts.py            # branded stakeholder charts (all tasks: R1, R2, A1)
├── build_pdf.py         # stakeholder PDF report (all tasks)
├── core/                # mcp_client, runners, scorer, golden, verifier, tasks
├── config/              # models.yaml, prompt*.txt, rubric.yaml, golden_*.csv
├── synthetic-data/      # the frozen synthesized dataset + golden result set
├── data-loader/         # loads synthetic-data/ into your connections via Connect AI itself
├── results/             # matrix.csv, runs_raw.csv, charts, PDF report
│   └── DATA_DICTIONARY.md   # defines every column in matrix.csv and runs_raw.csv
└── diagnostics/         # the pre-flight smoke-test outputs (proof the harness was validated)
```

## Running it

1. `pip install -r requirements.txt` (reportlab, matplotlib, pyyaml, requests, provider SDKs).
2. Copy `.env.example` → `.env` and fill in `CDATA_EMAIL`, `CDATA_ACCESS_TOKEN`, and your
   optimized Toolkit URL. Provider API keys are read from the OS environment
   (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`).
3. `python make_goldens.py --verify` — regenerate the R2/A1 goldens and validate them. Refuses to
   write anything unless it first reproduces R1's frozen golden **exactly**, and `--verify` also
   cross-checks the offline model against the live Derived Views.
4. `python run_matrix.py --dry-run` — confirm the plan (tasks × conditions × runs) before spending.
5. `python run_matrix.py` — full matrix (resumable; skips completed runs). Scope it with
   `--task r2`, `--model gpt-5.6`, or smoke-test cheaply with `--runs 1 --out-dir results/smoke`.
6. `python aggregate.py` → `python export_raw.py` → `python charts.py` → `python build_pdf.py` (outputs `results/CData_ConnectAI_Model_Benchmark.pdf`).
   (These recompute every score from each run's saved artifact — answer text, or the post-run
   snapshot of the write target — so they always reflect the current scorer, and a scorer fix applies
   retroactively without re-spending on models. No judge step needed.)

Write-task runs reset `REVIEW_QUEUE` automatically before each run and verify read+reset access
*before* the matrix starts, so a permissions problem surfaces immediately rather than dozens of
ungradable runs later.

`judge.py` is optional and **not** part of the headline scoring — it's retained only for future
open-ended tasks (run it, then it writes to `results/judge/`, but nothing aggregates it).

The `synthetic-data/` folder is published so the dataset and golden set are inspectable and
reproducible. See `data-loader/` to load it into your own connections through Connect AI.