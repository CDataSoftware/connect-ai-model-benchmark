# Setup — Reproducing the Data Environment

The harness code, prompts, goldens, and synthetic dataset are all in the repo.
What isn't in the repo is the Connect AI data layer the benchmark runs against.
This directory has everything needed to recreate it.

## Order of operations

1. **Load the synthetic data** into the three source systems using the CSVs in
   `synthetic-data/`. The exact schema each system expects is described in
   `toolkits.md` under "Connections required." See `data-loader/` for scripts that
   load the rows through Connect AI's own REST Query API rather than a vendor SDK.

2. **Create the warehouse database, schema, and tables** using `warehouse.sql`
   (`BENCHMARK_WH.WH_DATA` with `DIM_ACCOUNT`, `TELEMETRY_EVENTS`, and the
   `REVIEW_QUEUE` write target). `DIM_ACCOUNT` and `TELEMETRY_EVENTS` are then
   populated from the synthetic data load.

   The script creates `REVIEW_QUEUE` in `WH_DATA`. If you place it in a different
   schema — advisable on a shared warehouse account — set `REVIEW_QUEUE_TABLE` in
   your `.env` to the full path, e.g.
   `REVIEW_QUEUE_TABLE=Warehouse_System.CUSTOMER_OPS.REVIEW_QUEUE`. `core/verifier.py`
   defaults to `Warehouse_System.WH_DATA.REVIEW_QUEUE`; if the path is wrong, the write
   task fails at preflight rather than partway through the matrix.

3. **Create two Derived Views** in the Connect AI UI using the SQL in
   `derived_views.sql`: `account_health_score` and `account_usage_trend`. Both
   are cross-catalog views joining the CRM, cloud warehouse, and ITSM sources.

4. **Create two workspaces** as described in `toolkits.md` under "Workspaces":
   `account-health` (read-only Derived Views + source tables) and
   `account-health-write` (same plus `REVIEW_QUEUE`). Workspaces expose
   specific tables rather than whole connections, and are what the custom tool
   SQL `[workspace].[Root].[entity]` resolves through.

5. **Create the six toolkits** as described in `toolkits.md`, including their
   custom tools. Toolkits 1 and 6 use only universal ops (no custom SQL needed).
   Toolkits 2–5 use custom tools whose SQL is in `toolkits.md`.

6. **Copy the MCP endpoint URLs** from each toolkit into `.env` (see
   `.env.example` for the variable names).

7. **Validate:** run `python3 make_goldens.py --verify` to confirm the offline
   golden model matches the live Derived Views before running the matrix.

## Reference date

The Derived View SQL and golden formulas use `'2026-07-16'` as a frozen
reference date, matching the synthetic dataset. If you adapt this benchmark to
live data with a different reference date, update the three hardcoded dates in
`derived_views.sql` and re-run `make_goldens.py` to regenerate the goldens.
