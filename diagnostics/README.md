# Pre-flight diagnostic

Before the full 9-model matrix, the harness was validated end-to-end on a small
smoke test — **2 models × 2 conditions** (one Anthropic + one non-Anthropic model,
baseline + optimized) — to prove the plumbing works: MCP auth against both endpoints,
the multi-turn client-side tool loop, per-provider token capture, golden comparison,
and the Layer-2 judge.

The JSON files in this folder are the raw outputs of that diagnostic run (produced by
`../smoke_run.py`). They are kept as evidence that the harness was verified before the
real matrix — they are **not** part of the reported results (those live in `../results/`).

Only the **optimized**-condition runs are published here. The baseline-condition runs
walked the raw connection schemas via `getSchemas`/`getColumns` and their traces
captured live catalog/connection names from the test tenant, so those two files are
kept locally only rather than published.
