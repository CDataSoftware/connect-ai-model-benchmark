#!/usr/bin/env python3
"""Load the synthetic CRM CSVs into Account / Contact.

Two phases:

  1. NATIVE -- create the ten custom fields on Account (SLA__c, Ext_Account_Id__c, etc.)
     and grant field-level security through the CRM's own Tooling/Metadata API. Do this once,
     before running load_data().
  2. VIA CONNECT AI -- with the fields in place, load_data() does the actual row loading
     through Connect AI's REST Query API (SELECT + INSERT).

The CSV's `Id` column is our own synthetic external-id scheme, not the CRM's real record Id
(which is system-generated and can't be set). It's loaded into Ext_Account_Id__c instead, and
contacts.csv's `AccountId` (the same external-id scheme) is resolved to the CRM's real Account
Id via a lookup query before the Contact rows are inserted -- a plain column-name copy would
silently write the wrong foreign key.

Needs .env: CRM_USER, CRM_PASSWORD, CRM_TOOLING_URL (phase 1, native metadata API -- see the
comments in ensure_fields()/grant_fls() below for the exact shape expected) and CDATA_EMAIL,
CDATA_ACCESS_TOKEN (phase 2, Connect AI). Optionally CRM_CONNECTION (defaults to CRM_System).

Run:  python load_crm.py
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

from connect_ai_client import ConnectAIClient, insert_batch, sql_literal  # noqa: E402

CONNECTION = os.environ.get("CRM_CONNECTION", "CRM_System")

FIELDS = [
    ("Ext_Account_Id__c", "External Account ID", {"type": "Text", "length": 40, "externalId": True, "unique": True}),
    ("Tier__c", "Tier", {"type": "Text", "length": 40}),
    ("CustomerPriority__c", "Customer Priority", {"type": "Text", "length": 40}),
    ("SLA__c", "SLA", {"type": "Text", "length": 40}),
    ("Region__c", "Region", {"type": "Text", "length": 40}),
    ("ContractStatus__c", "Contract Status", {"type": "Text", "length": 40}),
    ("ContractStartDate__c", "Contract Start Date", {"type": "Date"}),
    ("ContractEndDate__c", "Contract End Date", {"type": "Date"}),
    ("AnnualContractValue__c", "Annual Contract Value", {"type": "Currency", "precision": 18, "scale": 2}),
    ("ProductLine__c", "Product Line", {"type": "Text", "length": 80}),
]


def read_csv(name):
    with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Phase 1 -- NATIVE. Custom field metadata + FLS. Not Connect AI.
#
# Create the fields listed below on Account through your CRM's Tooling/Metadata API (the
# field list matches setup/toolkits.md), then grant field-level security to the user Connect
# AI authenticates as. Your AI assistant (see AGENTS.md) can write this part for your specific
# CRM using the field list below -- it's a one-time step.
# ---------------------------------------------------------------------------

def ensure_schema():
    print("schema (native CRM metadata/Tooling API):")
    print(f"  Create these custom fields on Account, then grant field-level security "
          f"to the Connect AI user: {[f[0] for f in FIELDS]}")


# ---------------------------------------------------------------------------
# Phase 2 -- VIA CONNECT AI. Actual row loading.
# ---------------------------------------------------------------------------

def load_data():
    client = ConnectAIClient()
    account_table = f"{CONNECTION}.Salesforce.Account"
    contact_table = f"{CONNECTION}.Salesforce.Contact"

    accounts = read_csv("crm_accounts.csv")
    contacts = read_csv("crm_contacts.csv")

    existing_ext_ids = {r.get("Ext_Account_Id__c") for r in
                         client.rows(f"SELECT Ext_Account_Id__c FROM {account_table} "
                                     f"WHERE Ext_Account_Id__c IS NOT NULL")}
    todo = [a for a in accounts if a["Id"] not in existing_ext_ids]
    print(f"accounts: {len(todo)} to insert ({len(existing_ext_ids)} already present)")

    acc_cols = ["Ext_Account_Id__c", "Name", "Tier__c", "CustomerPriority__c", "SLA__c",
                "Region__c", "ContractStatus__c", "ContractStartDate__c", "ContractEndDate__c",
                "AnnualContractValue__c", "ProductLine__c", "Industry", "NumberOfEmployees",
                "Website"]
    acc_rows = [{
        "Ext_Account_Id__c": r["Id"], "Name": r["Name"], "Tier__c": r["Tier__c"],
        "CustomerPriority__c": r["CustomerPriority__c"], "SLA__c": r["SLA__c"],
        "Region__c": r["Region__c"], "ContractStatus__c": r["ContractStatus__c"],
        "ContractStartDate__c": r["ContractStartDate__c"], "ContractEndDate__c": r["ContractEndDate__c"],
        "AnnualContractValue__c": float(r["AnnualContractValue__c"]), "ProductLine__c": r["ProductLine__c"],
        "Industry": r["Industry"], "NumberOfEmployees": int(r["NumberOfEmployees"]),
        "Website": r["Website"],
    } for r in todo]
    if acc_rows:
        sent = insert_batch(client, account_table, acc_cols, acc_rows, batch_size=50)
        print(f"Account: inserted {sent} rows via Connect AI")

    # Resolve the real CRM Id for each of our accounts before loading contacts -- the CSV's
    # own "AccountId" is our synthetic external-id scheme, not a usable foreign key on its own.
    id_map = {r["Ext_Account_Id__c"]: r["Id"] for r in
              client.rows(f"SELECT Id, Ext_Account_Id__c FROM {account_table} "
                          f"WHERE Ext_Account_Id__c IS NOT NULL")}
    print(f"resolved {len(id_map)} account Ids for contact loading")

    linked_count = client.count(f"{contact_table} WHERE AccountId IN "
                                 f"(SELECT Id FROM {account_table} WHERE Ext_Account_Id__c IS NOT NULL)")
    if linked_count:
        print(f"{linked_count} contacts already linked to our accounts -- skipping contact load. "
              f"To reload, clear the table first.")
    else:
        con_cols = ["AccountId", "FirstName", "LastName", "Title", "Email"]
        con_rows = [{"AccountId": id_map[r["AccountId"]], "FirstName": r["FirstName"],
                     "LastName": r["LastName"], "Title": r["Title"], "Email": r["Email"]}
                    for r in contacts if r["AccountId"] in id_map]
        sent = insert_batch(client, contact_table, con_cols, con_rows, batch_size=50)
        print(f"Contact: inserted {sent} rows via Connect AI")

    print("\n=== VERIFY ===")
    print(f"Account (Ext_Account_Id__c populated): "
          f"{client.count(f'{account_table} WHERE Ext_Account_Id__c IS NOT NULL')}")
    print(f"Contact linked to our accounts: "
          f"{client.count(f'{contact_table} WHERE AccountId IN (SELECT Id FROM {account_table} WHERE Ext_Account_Id__c IS NOT NULL)')}")


def main():
    ensure_schema()
    load_data()


if __name__ == "__main__":
    main()
