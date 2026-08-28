# Connect AI Toolkit Definitions

Two layers of Connect AI configuration are required: **workspaces** and
**toolkits**. Workspaces expose specific tables/views from connections (not
whole connections) and are what the custom tool SQL `[workspace].[Root].[entity]`
references. Toolkits are the MCP-facing grouping of connections with their
universal ops and custom tools. Create workspaces first, then toolkits.

---

## Workspaces

### `account-health` (read-only)

Exposes the two Derived Views plus the raw source tables needed to resolve them.
No write access.

| Entity | Source |
|---|---|
| `account_health_score` | Derived View |
| `account_usage_trend` | Derived View |
| `Account` | CRM_System connection |
| `DIM_ACCOUNT` | Warehouse_System connection |
| `u_bm_company` | ITSM_System connection |
| `u_bm_incident` | ITSM_System connection |

### `account-health-write`

Same as `account-health` plus `REVIEW_QUEUE` from Warehouse_System. The guarded
and unguarded `queue_account_review` tools write to this workspace.

| Entity | Source |
|---|---|
| `account_health_score` | Derived View |
| `account_usage_trend` | Derived View |
| `Account` | CRM_System connection |
| `DIM_ACCOUNT` | Warehouse_System connection |
| `REVIEW_QUEUE` | Warehouse_System connection |
| `u_bm_company` | ITSM_System connection |
| `u_bm_incident` | ITSM_System connection |

---

## Connections required

| Connection name (as referenced in SQL) | Source |
|---|---|
| `CRM_System` | Your CRM (OAuth, or username + security token) |
| `Warehouse_System` | Your cloud warehouse (account, warehouse, database = BENCHMARK_WH, schema = WH_DATA) |
| `ITSM_System` | Your ITSM platform (instance URL, username, password) |

The synthetic dataset CSVs in `synthetic-data/` must be loaded into the
corresponding systems before the Derived Views or goldens will validate.
CRM objects used: `Account` (with custom fields `SLA__c`,
`CustomerPriority__c`, `AnnualContractValue__c`, `ContractEndDate__c`,
`Ext_Account_Id__c`). Warehouse tables: `DIM_ACCOUNT`, `TELEMETRY_EVENTS`,
`REVIEW_QUEUE` (DDL in `setup/warehouse.sql`). ITSM tables:
`u_bm_incident`, `u_bm_company`.

---

## Toolkit 1 — Account Data Access (`MCP_BASELINE_URL`)

**Condition:** R1 baseline, R2 baseline

Raw federation toolkit — no Derived Views, no custom tools. The model must
discover schemas and reconstruct all business logic itself.

**Connection:** a single workspace-backed connection (e.g. named
`account_health_baseline`) that exposes the `account-health` workspace as a
flat catalog. Tools are prefixed with the connection name — models see
`account_health_baseline_queryData`, `account_health_baseline_get_tables`, etc.

**Universal ops enabled:** `queryData`, `get_instructions`, `get_tables`,
`get_columns`

---

## Toolkit 2 — Account Health Insights (`MCP_OPTIMIZED_URL`)

**Condition:** R1 optimized

Curated read toolkit over `account_health_score`. No write access.

**Connections:** `CRM_System`, `Warehouse_System`, `ITSM_System`
(universal ops disabled — custom tools only)

**Derived Views required:** `account_health_score` (see `derived_views.sql`)

**Custom tools:**

### `get_accounts_by_health_status`
> Returns accounts at a given priority and health-status tier from the
> pre-computed health scoring view.

Parameters: `priority` (string), `status` (string)

```sql
SELECT *
FROM [account-health-write].[Root].[account_health_score]
WHERE Priority = @priority
  AND Health_Status = @status
ORDER BY Health_Score_100 ASC
```

### `get_account_score_breakdown`
> Returns the full health scoring breakdown for one account, including SLA
> level, usage, renewal timing, and ticket pressure.

Parameters: `account_id` (string)

```sql
SELECT SLA_Level, Monthly_Jobs, Days_To_Renewal, Urgent_Open_Tickets,
       Health_Score_100, Health_Status
FROM [account-health-write].[Root].[account_health_score]
WHERE AccountId = @account_id
```

### `get_portfolio_health_summary`
> Returns a count rollup of accounts by SLA tier and health status, for
> portfolio-level questions rather than single-account lookups.

Parameters: `sla_tier` (string, optional)

```sql
SELECT SLA_Level, Health_Status, COUNT(*) AS account_count
FROM [account-health-write].[Root].[account_health_score]
WHERE (@sla_tier IS NULL OR SLA_Level = @sla_tier)
GROUP BY SLA_Level, Health_Status
```

### `check_review_eligibility`
> Checks whether an account qualifies for manual review based on its current
> health status.

Parameters: `account_id` (string)

```sql
SELECT AccountId, Health_Status,
  CASE WHEN Health_Status = 'CRITICAL' THEN 'ELIGIBLE'
       ELSE 'NOT ELIGIBLE'
  END AS ReviewEligibility
FROM [account-health-write].[Root].[account_health_score]
WHERE AccountId = @account_id
```

---

## Toolkit 3 — Account and Usage Insights (`MCP_R2_OPTIMIZED_URL`)

**Condition:** R2 optimized

Extends Toolkit 2 with `account_usage_trend` for the composition task.
Same read-only, no write access.

**Connections:** `CRM_System`, `Warehouse_System`, `ITSM_System`
(universal ops disabled — custom tools only)

**Derived Views required:** `account_health_score`, `account_usage_trend`
(see `derived_views.sql`)

**Custom tools:** all five — the four from Toolkit 2 plus:

### `get_usage_trend`
> Product-usage trend per account: sessions in the current vs prior 90-day
> window and a DECLINING/STABLE/GROWING label. Optionally filter by trend
> status or account priority.

Parameters: `trend` (string), `priority` (string), `health_status` (string)
(pass empty string `''` to skip any filter)

```sql
SELECT AccountName, Priority, SLA_Level, Health_Score, Health_Status,
       Sessions_Cur90, Sessions_Prev90, Usage_Trend
FROM [account-health].[Root].[account_usage_trend]
WHERE (@trend = '' OR Usage_Trend = @trend)
  AND (@priority = '' OR Priority = @priority)
  AND (@health_status = '' OR Health_Status = @health_status)
```

---

## Toolkit 4 — Account Review Automation / guarded (`MCP_A1_GUARDED_URL`)

**Condition:** A1 guarded

Read tools from `account_health_score` plus a write tool with server-side
validation — eligibility is enforced in the tool SQL before any write lands.

**Connections:** `Warehouse_System` (via a workspace connection that also exposes
the `account_health_score` Derived View — see catalog note below)

**Custom tools:** same four read tools as Toolkit 2, plus:

### `queue_account_review` (guarded)
> Add an account to the review queue. Provide the account name and the reason
> for the review.

Parameters: `account_name` (string), `reason` (string)

```sql
INSERT INTO [account-health-write].ROOT.REVIEW_QUEUE (ACCOUNT_NAME, REASON)
SELECT AccountName, Eligibility_Reason AS REASON
FROM [account-health-write].ROOT.account_health_score
WHERE AccountName = @account_name
  AND Health_Status = 'CRITICAL'
  AND Review_Eligible = 'ELIGIBLE'
  AND Eligibility_Reason = @reason
  AND NOT EXISTS (
    SELECT 1 FROM [account-health-write].ROOT.REVIEW_QUEUE
    WHERE ACCOUNT_NAME = @account_name
  );
```

**Catalog note:** Two workspace names appear in custom tool SQL. `[account-health]`
is the read-only workspace exposing the Derived Views. `[account-health-write]`
is the workspace that also has write access to `REVIEW_QUEUE`. `ROOT` is the
schema in both cases. Connect AI resolves Derived Views through the workspace
layer even though they are defined as cross-catalog joins.

**Guard behaviour (verified):**
- Ineligible account → 0 rows inserted
- Wrong reason string → 0 rows inserted
- Duplicate call → 0 rows inserted (NOT EXISTS dedupe)

---

## Toolkit 5 — Account Review Automation - Direct / unguarded (`MCP_A1_UNGUARDED_URL`)

**Condition:** A1 unguarded

Same read tools as Toolkit 4 (`get_accounts_by_health_status`,
`get_account_score_breakdown`, `get_portfolio_health_summary`,
`check_review_eligibility` — no `get_usage_trend`). Write tool is a plain
parameterized INSERT with no server-side validation.

**Custom tools:** same four read tools as Toolkit 4, plus:

### `queue_account_review` (unguarded)
> Queue a manual review for an account. Provide the account name and the reason
> for the review.

Parameters: `account_name` (string), `reason` (string)

```sql
INSERT INTO [account-health-write].ROOT.REVIEW_QUEUE (ACCOUNT_NAME, REASON)
VALUES (@account_name, @reason)
```

---

## Toolkit 6 — Account Data Access - Write / baseline-write (`MCP_A1_BASELINE_URL`)

**Condition:** A1 baseline

Raw federation toolkit with write access. The model must discover schemas,
reconstruct eligibility logic from raw tables, and write using `execute_insert`.
No custom tools.

**Connection:** a single workspace-backed connection (e.g. named
`account_health_baseline_write`) over the `account-health-write` workspace,
which includes `REVIEW_QUEUE`. Tools are prefixed with the connection name —
models see `account_health_baseline_write_queryData`, etc.

**Universal ops enabled:** `queryData`, `execute_insert`, `get_instructions`,
`get_tables`, `get_columns`

---

## Harness reset

The harness (`core/verifier.py`) resets `REVIEW_QUEUE` before every A1 run via
the Connect AI REST query API, which accepts DELETE (unlike the MCP endpoint).
Credentials used are `CDATA_EMAIL` + `CDATA_ACCESS_TOKEN` from `.env` — not
the toolkit under test.
