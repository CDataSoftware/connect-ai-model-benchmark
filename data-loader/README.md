# Data loader

Loads the frozen synthetic dataset (`synthetic-data/*.csv`) into your CRM, cloud warehouse,
and ITSM connections through **Connect AI's REST Query API**.

## Three ways to get the data in

| | What it needs | Where the instructions live |
|---|---|---|
| **A. Google Sheets, assistant-automated** | An AI assistant with Connect AI's Management MCP configured | `AGENTS.md`, Option A |
| **B. Your real CRM/warehouse/ITSM, assistant-automated where possible** | Same, plus native admin access for schema setup and any source not on Connect AI's connection allow-list (the warehouse, the ITSM) | `AGENTS.md`, Option B |
| **C. Fully manual CSV import, no Connect AI automation** | Just your own system's native import tooling | This file, below |

Pick whichever fits — none of the scripts in this folder require Management MCP; that's an
optional automation layer on top, not a dependency.

## Setup order

| | Create the schema | Load the rows |
|---|---|---|
| **Warehouse** | Run `setup/warehouse.sql` against your warehouse | `python load_warehouse.py` |
| **ITSM** | `python load_itsm.py` creates the two custom tables/columns for you | same run, right after |
| **CRM** | Create the ten custom fields on Account (list in `load_crm.py`) and grant field-level security | `python load_crm.py` |

## Running it

1. Run `setup/warehouse.sql` against your warehouse.
2. Create the CRM custom fields (see the field list in `load_crm.py`) and grant field-level
   security to the user Connect AI authenticates as.
3. Copy `.env.example` (repo root) to `.env` and fill in `CDATA_EMAIL` / `CDATA_ACCESS_TOKEN`.
   For the ITSM schema step, also set `ITSM_INSTANCE` / `ITSM_USER` / `ITSM_PASSWORD` in a
   `.env` in *this* folder (separate from the repo-root `.env` since these are your ITSM
   platform's own credentials, not Connect AI's).
4. `pip install requests`
5. `python load_warehouse.py`
6. `python load_itsm.py`
7. `python load_crm.py`

Each script checks whether a table already has rows before loading, so re-running after a
partial failure won't duplicate anything. To force a clean reload, clear the target table
first.

## Connection names

Scripts default to the connection names used throughout this repo
(`CRM_System`, `Warehouse_System`, `ITSM_System`) and can be overridden per-script via
`CRM_CONNECTION` / `WAREHOUSE_CONNECTION` / `ITSM_CONNECTION` in `.env` if you've named yours
differently.

## Manual CSV import (Option C — no Connect AI, no scripts)

Load the six `synthetic-data/*.csv` files straight into your own systems using each
platform's native import tooling. Nothing here talks to Connect AI at all — do this first,
then create your Connect AI connections against the data once it's already loaded.

This repo's connections are generic (any CRM, any cloud warehouse, any ITSM platform). The
steps below just need one concrete platform per category to give real, followable
instructions instead of vague ones — swap in whatever you actually use; the CSV columns and
target schema stay the same regardless of platform.

### CRM — for example, Salesforce

1. Create the ten custom fields on Account first (same list `load_crm.py` uses: `Tier__c`,
   `CustomerPriority__c`, `SLA__c`, `Region__c`, `ContractStatus__c`, `ContractStartDate__c`,
   `ContractEndDate__c`, `AnnualContractValue__c`, `ProductLine__c`, and an external-id field
   — e.g. `Ext_Account_Id__c`, Text(40), marked External ID + Unique) via Setup > Object
   Manager > Account > Fields & Relationships.
2. **Setup > Data Import Wizard** (or Data Loader for larger volumes) > import
   `crm_accounts.csv` into Account. Map the CSV's `Id` column to `Ext_Account_Id__c` — it's
   your own synthetic key, not the CRM's real record Id, which is system-generated and
   can't be set directly.
3. Import `crm_contacts.csv` into Contact. Map its `AccountId` column against
   `Ext_Account_Id__c` (the Import Wizard supports matching a lookup by an external ID field)
   so contacts land on the right account without needing the CRM's real internal Ids.

### Cloud data warehouse — for example, Snowflake

1. Run `setup/warehouse.sql` in a worksheet to create `DIM_ACCOUNT`, `TELEMETRY_EVENTS`, and
   `REVIEW_QUEUE`.
2. Use the web UI's **Load Data** wizard (Databases > your database > table > Load Data) to
   upload `warehouse_dim_account.csv` and `warehouse_telemetry_events.csv` directly — column
   names in the CSVs already match the DDL exactly, so the wizard's auto-mapping should need
   no adjustment. Prefer the CLI (`PUT` + `COPY INTO`) if loading from a script instead of a
   browser.

### ITSM — for example, ServiceNow

1. Create two custom tables — `u_bm_company` (fields `u_name`, `u_country`,
   `u_account_manager`) and `u_bm_incident` (fields `u_company_sys_id`, `u_company`,
   `u_state`, `u_priority`, `u_category`, `u_short_description`, `u_opened_at`,
   `u_resolved_at`, `u_assignment_group`, `u_reopen_count`) — via System Definition > Tables,
   or let `load_itsm.py`'s `ensure_schema()` create them for you even if you're loading rows
   manually afterward.
2. **System Import Sets > Load Data** > upload `itsm_companies.csv` and `itsm_incidents.csv`,
   then build a Transform Map for each mapping the CSV's bare column names (`name`, `country`,
   `company_sys_id`, `state`, ...) to the `u_`-prefixed target fields. The CSVs' own `sys_id`
   values can be preserved on import — the ITSM accepts a supplied `sys_id` rather than only
   generating its own.

Whichever platform you use, the column names in each CSV are the source of truth for what the
target schema needs to look like — cross-check against `setup/toolkits.md` if a custom-tool
query later can't find a column it expects.
