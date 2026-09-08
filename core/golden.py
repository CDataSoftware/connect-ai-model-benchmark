"""Offline golden model: rebuilds every task's expected answer from the frozen CSVs.

Why this exists: the goldens must be derivable *independently of the deployed views*, otherwise a
bug in a Derived View is invisible (the optimized toolkit would score 100 for reproducing a wrong
answer -- the contamination class of failure for any benchmark that reads its golden from the view under test). So the health-score
and usage-trend formulas are reimplemented here against `synthetic-data/*.csv` and cross-checked
against both the frozen `config/golden_result.csv` (R1) and the live views (see make_goldens.py
--verify). Validated 2026-07-27: reproduces R1's 50 rows exactly (scores, statuses, urgent-ticket
counts, eligibility flags and reason branches all 0 mismatches) and the deployed
`account_usage_trend` distribution exactly (452 DECLINING / 477 GROWING / 271 STABLE over 1200).

Formulas (frozen reference date 2026-07-16, matching the deployed views):
  health = 100 + sla_bonus - usage_penalty - renewal_penalty - urgent_penalty
  bands: <40 CRITICAL, <60 AT_RISK, <80 MONITOR, else HEALTHY
  eligibility (priority-ordered, first match wins): renewal 0-90d, ACV >= 250k, urgent >= 5
  usage trend: cur90 < 0.8*prev90 -> DECLINING; > 1.2*prev90 -> GROWING; else STABLE

Population note: the deployed `account_health_score` view is filtered to **High-priority accounts
only** (450 rows), so every task's golden is scoped the same way. Confirmed live 2026-07-27.
"""
import collections
import csv
import datetime
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "synthetic-data")

REF_DATE = datetime.date(2026, 7, 16)
SLA_BONUS = {"Platinum": 10, "Gold": 5, "Silver": 0, "Bronze": -5}
ACV_THRESHOLD = 250000.0
POOR = ("AT_RISK", "CRITICAL")

# usage-trend windows: current 90 days back from the reference date, prior 90 before that
CUR_FROM = "2026-04-17"
PREV_FROM = "2026-01-17"

REASON_RENEWAL = "Renewal within 90 days"
REASON_ACV = "ACV above review threshold"
REASON_URGENT = "High urgent ticket volume"
REASON_NONE = "No escalation trigger met"


def _band(score):
    if score < 40:
        return "CRITICAL"
    if score < 60:
        return "AT_RISK"
    if score < 80:
        return "MONITOR"
    return "HEALTHY"


def _eligibility(days_to_renewal, acv, urgent):
    """(is_eligible, reason). Branch order matters -- it defines the golden's reason string."""
    if 0 <= days_to_renewal <= 90:
        return True, REASON_RENEWAL
    if acv >= ACV_THRESHOLD:
        return True, REASON_ACV
    if urgent >= 5:
        return True, REASON_URGENT
    return False, REASON_NONE


def _read(name):
    with open(os.path.join(DATA, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_accounts():
    """Every CRM account with all derived fields. Join keys mirror the real federation:
    Account.Id -> DIM_ACCOUNT.CRM_ACCOUNT_ID, Account.Name -> incidents.company_name."""
    accounts = _read("crm_accounts.csv")
    dim = {r["CRM_ACCOUNT_ID"]: r for r in _read("warehouse_dim_account.csv")}

    urgent = collections.Counter()
    for inc in _read("itsm_incidents.csv"):
        if inc["priority"] in ("Critical", "High") and inc["state"] == "Open":
            urgent[inc["company_name"]] += 1

    cur, prev = collections.Counter(), collections.Counter()
    for ev in _read("warehouse_telemetry_events.csv"):
        day = ev["EVENT_TIMESTAMP"][:10]
        sessions = int(ev["SESSION_COUNT"])
        if day >= CUR_FROM:
            cur[ev["ACCOUNT_SK"]] += sessions
        elif day >= PREV_FROM:
            prev[ev["ACCOUNT_SK"]] += sessions

    out = []
    for a in accounts:
        d = dim[a["Id"]]
        sk = d["ACCOUNT_SK"]
        jobs = int(d["MONTHLY_JOBS_L90"])
        usage_pen = 25 if jobs < 41 else (10 if jobs <= 150 else 0)

        days = (datetime.date.fromisoformat(a["ContractEndDate__c"]) - REF_DATE).days
        renewal_pen = 25 if days <= 30 else (10 if days <= 90 else 0)

        u = urgent.get(a["Name"], 0)
        urgent_pen = 25 if u >= 5 else u * 5

        score = 100 + SLA_BONUS[a["SLA__c"]] - usage_pen - renewal_pen - urgent_pen
        acv = float(a["AnnualContractValue__c"])
        eligible, reason = _eligibility(days, acv, u)

        c90, p90 = cur.get(sk, 0), prev.get(sk, 0)
        if c90 < 0.8 * p90:
            trend = "DECLINING"
        elif c90 > 1.2 * p90:
            trend = "GROWING"
        else:
            trend = "STABLE"

        out.append({
            "AccountName": a["Name"],
            "Priority": a["CustomerPriority__c"],
            "SLA_Level": a["SLA__c"],
            "Health_Score_100": score,
            "Health_Status": _band(score),
            "Urgent_Open_Tickets": u,
            "Days_To_Renewal": days,
            "Annual_Contract_Value": acv,
            "Review_Eligible": "ELIGIBLE" if eligible else "NOT ELIGIBLE",
            "Eligibility_Reason": reason,
            "Sessions_Cur90": c90,
            "Sessions_Prev90": p90,
            "Usage_Trend": trend,
        })
    return out


def _high_poor(accounts):
    """The R1/R2 population: High-priority accounts in poor health, worst-first.
    Sorted (score asc, name) -- the same deterministic order the golden was cut from."""
    rows = [r for r in accounts if r["Priority"] == "High" and r["Health_Status"] in POOR]
    return sorted(rows, key=lambda r: (r["Health_Score_100"], r["AccountName"]))


R1_COLUMNS = ["AccountName", "SLA_Level", "Health_Score_100", "Health_Status",
              "Urgent_Open_Tickets", "Review_Eligible", "Eligibility_Reason"]
R2_COLUMNS = ["AccountName", "SLA_Level", "Health_Score_100", "Health_Status",
              "Sessions_Cur90", "Sessions_Prev90", "Usage_Trend"]
A1_COLUMNS = ["AccountName", "Eligibility_Reason"]


def golden_r1(accounts=None, cap=50):
    """R1 (frozen): worst-50 High-priority poor-health accounts.

    Convention carried from the frozen golden: eligibility fields are populated only on CRITICAL
    rows and left blank elsewhere (R1's prompt only asks for eligibility on CRITICAL accounts, and
    the scorer only grades it there). The underlying view does populate them for every row."""
    rows = _high_poor(accounts or build_accounts())[:cap]
    out = []
    for r in rows:
        row = {k: r[k] for k in R1_COLUMNS}
        if r["Health_Status"] != "CRITICAL":
            row["Review_Eligible"] = ""
            row["Eligibility_Reason"] = ""
        out.append(row)
    return out


def golden_r2(accounts=None, cap=50):
    """R2: High-priority + poor health + DECLINING usage, worst-first, capped."""
    rows = [r for r in _high_poor(accounts or build_accounts()) if r["Usage_Trend"] == "DECLINING"]
    return [{k: r[k] for k in R2_COLUMNS} for r in rows[:cap]]


def golden_a1(accounts=None):
    """A1: the exact set of rows that should end up in REVIEW_QUEUE -- High-priority accounts that
    are CRITICAL *and* review-eligible, with the correct reason. Scoped to High-priority to match
    the deployed view (and A1's prompt); without that scoping the raw-table conditions would
    legitimately find 44 accounts instead of 21 and be scored as writing 23 unauthorized rows."""
    rows = [r for r in (accounts or build_accounts())
            if r["Priority"] == "High" and r["Health_Status"] == "CRITICAL"
            and r["Review_Eligible"] == "ELIGIBLE"]
    rows.sort(key=lambda r: r["AccountName"])
    return [{k: r[k] for k in A1_COLUMNS} for r in rows]


def write_csv(path, rows, columns):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return len(rows)
