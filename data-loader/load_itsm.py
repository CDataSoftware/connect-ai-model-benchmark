#!/usr/bin/env python3
"""Load the synthetic ITSM CSVs into u_bm_company / u_bm_incident.

Two phases:

  1. NATIVE -- create the two custom tables and their columns through the ITSM platform's
     own table-metadata API. ensure_schema() does this for you.
  2. VIA CONNECT AI -- with the tables in place, load_data() does the actual row loading
     through Connect AI's REST Query API (INSERT).

Needs .env: ITSM_INSTANCE, ITSM_USER, ITSM_PASSWORD (phase 1, native table-metadata API) and
CDATA_EMAIL, CDATA_ACCESS_TOKEN (phase 2, Connect AI). Optionally ITSM_CONNECTION (defaults
to ITSM_System, the connection name used throughout this repo).

Run:  python load_itsm.py
"""
import csv
import os
import sys
import time

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

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    sys.exit("pip install requests")

from connect_ai_client import ConnectAIClient, insert_batch  # noqa: E402

CONNECTION = os.environ.get("ITSM_CONNECTION", "ITSM_System")

COMPANY_COLS = [("u_name", "string", 100, "Name"), ("u_country", "string", 40, "Country"),
                ("u_account_manager", "string", 100, "Account Manager")]
INCIDENT_COLS = [("u_company_sys_id", "string", 40, "Company Sys ID"), ("u_company", "string", 100, "Company"),
                  ("u_state", "string", 40, "State"), ("u_priority", "string", 40, "Priority"),
                  ("u_category", "string", 40, "Category"), ("u_short_description", "string", 255, "Short Description"),
                  ("u_opened_at", "glide_date_time", None, "Opened At"), ("u_resolved_at", "glide_date_time", None, "Resolved At"),
                  ("u_assignment_group", "string", 40, "Assignment Group"), ("u_reopen_count", "integer", None, "Reopen Count")]


def read_csv(name):
    with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Phase 1 -- NATIVE. Table/column metadata creation. Not Connect AI.
# ---------------------------------------------------------------------------

def ensure_schema():
    inst = os.environ["ITSM_INSTANCE"].strip().rstrip("/")
    auth = HTTPBasicAuth(os.environ["ITSM_USER"], os.environ["ITSM_PASSWORD"])
    hdrs = {"Accept": "application/json", "Content-Type": "application/json"}

    def ensure_table(s, name, label):
        r = s.get(f"{inst}/api/now/table/sys_db_object",
                  params={"sysparm_query": f"name={name}", "sysparm_fields": "sys_id"}, auth=auth, headers=hdrs)
        if r.json().get("result"):
            print(f"  table {name}: exists"); return
        s.post(f"{inst}/api/now/table/sys_db_object", json={"name": name, "label": label}, auth=auth, headers=hdrs)
        print(f"  table {name}: created")

    def ensure_columns(s, table, cols):
        r = s.get(f"{inst}/api/now/table/sys_dictionary",
                  params={"sysparm_query": f"name={table}^elementISNOTEMPTY", "sysparm_fields": "element"}, auth=auth, headers=hdrs)
        have = {x["element"] for x in r.json().get("result", [])}
        added = 0
        for el, typ, ml, lbl in cols:
            if el in have:
                continue
            body = {"name": table, "element": el, "internal_type": typ, "column_label": lbl}
            if ml:
                body["max_length"] = ml
            s.post(f"{inst}/api/now/table/sys_dictionary", json=body, auth=auth, headers=hdrs)
            added += 1
        print(f"  columns on {table}: {added} added ({len(cols)} total expected)")

    print("schema (native ITSM table-metadata API):")
    with requests.Session() as s:
        ensure_table(s, "u_bm_company", "BM Company")
        ensure_columns(s, "u_bm_company", COMPANY_COLS)
        ensure_table(s, "u_bm_incident", "BM Incident")
        ensure_columns(s, "u_bm_incident", INCIDENT_COLS)
    time.sleep(2)  # metadata propagation


# ---------------------------------------------------------------------------
# Phase 2 -- VIA CONNECT AI. Actual row loading.
# ---------------------------------------------------------------------------

def load_data():
    client = ConnectAIClient()
    company_table = f"{CONNECTION}.ServiceNow.u_bm_company"
    incident_table = f"{CONNECTION}.ServiceNow.u_bm_incident"

    comps = read_csv("itsm_companies.csv")
    incs = read_csv("itsm_incidents.csv")

    comp_cols = ["sys_id", "u_name", "u_country", "u_account_manager"]
    comp_rows = [{"sys_id": r["sys_id"], "u_name": r["name"], "u_country": r["country"],
                  "u_account_manager": r["account_manager"]} for r in comps]

    inc_cols = ["sys_id", "u_company_sys_id", "u_company", "u_state", "u_priority", "u_category",
                "u_short_description", "u_opened_at", "u_resolved_at", "u_assignment_group",
                "u_reopen_count"]
    inc_rows = [{"sys_id": r["sys_id"], "u_company_sys_id": r["company_sys_id"], "u_company": r["company_name"],
                 "u_state": r["state"], "u_priority": r["priority"], "u_category": r["category"],
                 "u_short_description": r["short_description"], "u_opened_at": r["opened_at"],
                 "u_resolved_at": r["resolved_at"], "u_assignment_group": r["assignment_group"],
                 "u_reopen_count": int(r["reopen_count"])} for r in incs]

    existing = client.count(company_table)
    if existing:
        print(f"{company_table} already has {existing} rows -- skipping. To reload, clear the table first.")
    else:
        sent = insert_batch(client, company_table, comp_cols, comp_rows, batch_size=100)
        print(f"u_bm_company: inserted {sent} rows via Connect AI")

    existing = client.count(incident_table)
    if existing:
        print(f"{incident_table} already has {existing} rows -- skipping.")
    else:
        sent = insert_batch(client, incident_table, inc_cols, inc_rows, batch_size=100)
        print(f"u_bm_incident: inserted {sent} rows via Connect AI")

    print("\n=== VERIFY ===")
    print(f"u_bm_company:  rows={client.count(company_table)}")
    print(f"u_bm_incident: rows={client.count(incident_table)}")


def main():
    ensure_schema()
    load_data()


if __name__ == "__main__":
    main()
