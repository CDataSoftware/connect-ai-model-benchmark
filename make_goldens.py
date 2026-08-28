#!/usr/bin/env python3
"""Regenerate the benchmark goldens from the frozen CSVs, with a self-check against R1's frozen golden.

  python3 make_goldens.py            # self-check R1, write R2 + A1 goldens
  python3 make_goldens.py --verify   # additionally cross-check against the LIVE Derived Views

The R1 self-check is the trust anchor: `core/golden.py` must reproduce the already-frozen
`config/golden_result.csv` byte-for-byte before its R2/A1 output is believable. It does not
overwrite R1's golden -- that file is frozen.
"""
import csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import golden

R1_PATH = os.path.join(HERE, "config", "golden_result.csv")
R2_PATH = os.path.join(HERE, "config", "golden_result_r2.csv")
A1_PATH = os.path.join(HERE, "config", "golden_action_a1.csv")


def selfcheck_r1(accounts):
    with open(R1_PATH, newline="", encoding="utf-8") as f:
        frozen = list(csv.DictReader(f))
    mine = golden.golden_r1(accounts)
    if len(mine) != len(frozen):
        return [f"row count {len(mine)} != frozen {len(frozen)}"]
    problems = []
    for got, want in zip(mine, frozen):
        for k in golden.R1_COLUMNS:
            if str(got[k]) != str(want[k]):
                problems.append(f"{want['AccountName']}.{k}: {got[k]!r} != frozen {want[k]!r}")
    return problems


def verify_live(accounts):
    """Cross-check the offline model against the deployed views (needs .env credentials)."""
    import requests
    for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
        line = line.rstrip("\r\n")
        if line and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'\"")
    auth = (os.environ["CDATA_EMAIL"], os.environ["CDATA_ACCESS_TOKEN"])

    def q(sql):
        r = requests.post("https://cloud.cdata.com/api/query", auth=auth,
                          json={"query": sql}, timeout=180)
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"]["message"][:300])
        return body["results"][0]["rows"]

    checks = []
    hi = [a for a in accounts if a["Priority"] == "High"]
    checks.append(("account_health_score rows", len(hi),
                   int(q("SELECT COUNT(*) FROM CData.DerivedViews.account_health_score")[0][0])))
    crit_elig = [a for a in hi if a["Health_Status"] == "CRITICAL" and a["Review_Eligible"] == "ELIGIBLE"]
    checks.append(("CRITICAL+ELIGIBLE", len(crit_elig),
                   int(q("SELECT COUNT(*) FROM CData.DerivedViews.account_health_score "
                         "WHERE Health_Status='CRITICAL' AND Review_Eligible='ELIGIBLE'")[0][0])))
    declining = [a for a in accounts if a["Usage_Trend"] == "DECLINING"]
    checks.append(("account_usage_trend DECLINING", len(declining),
                   int(q("SELECT COUNT(*) FROM CData.DerivedViews.account_usage_trend "
                         "WHERE Usage_Trend='DECLINING'")[0][0])))
    print("\nLive view cross-check:")
    ok = True
    for name, offline, live in checks:
        match = "OK " if offline == live else "!! "
        ok &= offline == live
        print(f"  {match}{name}: offline={offline} live={live}")
    return ok


def main():
    accounts = golden.build_accounts()
    print(f"Built {len(accounts)} accounts from frozen CSVs "
          f"(reference date {golden.REF_DATE}).")

    problems = selfcheck_r1(accounts)
    if problems:
        print(f"\n!! R1 self-check FAILED ({len(problems)} mismatches) -- refusing to write "
              f"R2/A1 goldens, since the shared formula is wrong:")
        for p in problems[:15]:
            print(f"   {p}")
        return 1
    print("OK  R1 self-check: reproduces config/golden_result.csv exactly (50 rows).")

    r2 = golden.golden_r2(accounts)
    n = golden.write_csv(R2_PATH, r2, golden.R2_COLUMNS)
    capped = " (CAPPED at 50 -- check tie handling)" if n == 50 else ""
    print(f"OK  wrote {os.path.relpath(R2_PATH, HERE)}: {n} rows{capped}")

    a1 = golden.golden_a1(accounts)
    n = golden.write_csv(A1_PATH, a1, golden.A1_COLUMNS)
    print(f"OK  wrote {os.path.relpath(A1_PATH, HERE)}: {n} rows")

    if "--verify" in sys.argv:
        if not verify_live(accounts):
            print("\n!! live cross-check mismatch -- investigate before running the matrix")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
