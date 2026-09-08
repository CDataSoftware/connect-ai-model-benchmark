#!/usr/bin/env python3
"""Load the synthetic warehouse CSVs into DIM_ACCOUNT / TELEMETRY_EVENTS via Connect AI's
REST Query API.

Run setup/warehouse.sql against your warehouse first to create the tables (your warehouse's
own console runs that DDL fine, or run it through the same Connect AI connection if you'd
rather). Then run this script to load the rows.

Needs .env: CDATA_EMAIL, CDATA_ACCESS_TOKEN, and optionally WAREHOUSE_CONNECTION
(defaults to the connection name used throughout this repo: Warehouse_System) and
WAREHOUSE_SCHEMA (defaults to WH_DATA, matching setup/warehouse.sql).

Run:  python load_warehouse.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "synthetic-data")

sys.path.insert(0, HERE)


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            os.environ[k] = v


load_env(os.path.join(HERE, ".env"))
load_env(os.path.join(HERE, "..", ".env"))

from connect_ai_client import ConnectAIClient, insert_batch  # noqa: E402

CONNECTION = os.environ.get("WAREHOUSE_CONNECTION", "Warehouse_System")
SCHEMA = os.environ.get("WAREHOUSE_SCHEMA", "WH_DATA")

DIM_COLS = ["ACCOUNT_SK", "CRM_ACCOUNT_ID", "ACCOUNT_NAME", "ACTIVE_SEATS",
            "PLAN_CODE", "MONTHLY_JOBS_L90", "REGION_CODE"]
EVT_COLS = ["EVENT_SK", "ACCOUNT_SK", "EVENT_TYPE", "SESSION_COUNT", "FEATURE_FLAG",
            "EVENT_TIMESTAMP", "INGESTED_AT", "APP_VERSION", "REGION_CODE"]

NUMERIC_COLS = {"ACCOUNT_SK", "ACTIVE_SEATS", "MONTHLY_JOBS_L90", "EVENT_SK", "SESSION_COUNT"}


def read_csv(name):
    with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_typed_row(raw, cols):
    row = {}
    for c in cols:
        v = raw[c]
        row[c] = int(v) if c in NUMERIC_COLS and v != "" else v
    return row


def main():
    client = ConnectAIClient()
    dim_table = f"{CONNECTION}.{SCHEMA}.DIM_ACCOUNT"
    evt_table = f"{CONNECTION}.{SCHEMA}.TELEMETRY_EVENTS"

    dim_rows = [to_typed_row(r, DIM_COLS) for r in read_csv("warehouse_dim_account.csv")]
    evt_rows = [to_typed_row(r, EVT_COLS) for r in read_csv("warehouse_telemetry_events.csv")]
    print(f"CSV rows -> DIM_ACCOUNT {len(dim_rows)}, TELEMETRY_EVENTS {len(evt_rows)}")

    existing = client.count(dim_table)
    if existing:
        print(f"{dim_table} already has {existing} rows -- skipping DIM_ACCOUNT load. "
              f"To reload, clear the table first.")
    else:
        sent = insert_batch(client, dim_table, DIM_COLS, dim_rows)
        print(f"DIM_ACCOUNT: inserted {sent} rows via Connect AI")

    existing = client.count(evt_table)
    if existing:
        print(f"{evt_table} already has {existing} rows -- skipping TELEMETRY_EVENTS load.")
    else:
        sent = insert_batch(client, evt_table, EVT_COLS, evt_rows)
        print(f"TELEMETRY_EVENTS: inserted {sent} rows via Connect AI")

    print("\n=== VERIFY ===")
    print(f"DIM_ACCOUNT      rows={client.count(dim_table)}")
    print(f"TELEMETRY_EVENTS rows={client.count(evt_table)}")


if __name__ == "__main__":
    main()
