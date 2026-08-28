#!/usr/bin/env python3
"""Pre-flight: verify the four R2/A1 toolkits before building the harness around them.

Connectivity + tools/list only (read-only, safe to run anytime). The write-behavior
checklist (guarded validates / dedupes, raw doesn't, baseline is table-scoped) is
NOT run here - those calls mutate the shared REVIEW_QUEUE table and are driven separately,
one at a time, with explicit confirmation before each write.

Usage: python diagnostics/verify_toolkits.py
Needs (layered - OS env wins, falls back to these files for whatever's missing):
  ../.env                                                      (CDATA_EMAIL, CDATA_ACCESS_TOKEN)
  ../Benchmarks vs docs and research/toolkit_URLs.env
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from core.envutil import load_env
from core.mcp_client import MCPClient, MCPError

load_env(os.path.join(ROOT, ".env"))

# toolkit_URLs.env has trailing "(display name)" comments after each URL, so it isn't
# strict KEY=VALUE - parse it locally instead of asking the shared loader to special-case it.
TOOLKIT_ENV = os.path.join(ROOT, "Benchmarks vs docs and research", "toolkit_URLs.env")
def load_toolkit_urls(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\r\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1); k = k.strip()
        url = v.strip().split()[0]  # drop the trailing "(Display Name)" comment
        out[k] = url
    return out

if not os.path.exists(TOOLKIT_ENV):
    sys.exit(f"Missing {TOOLKIT_ENV} - can't find the four toolkit URLs.")
urls = load_toolkit_urls(TOOLKIT_ENV)

TOOLKITS = [
    ("r2-optimized", "Account and Usage Insights", "MCP_R2_OPTIMIZED_URL"),
    ("a1-guarded", "Account Review Automation", "MCP_A1_GUARDED_URL"),
    ("a1-unguarded", "Account Review Automation (Direct)", "MCP_A1_UNGUARDED_URL"),
    ("a1-baseline", "Account Data Access - Write", "MCP_A1_BASELINE_URL"),
]

EMAIL = os.environ.get("CDATA_EMAIL"); TOKEN = os.environ.get("CDATA_ACCESS_TOKEN")
if not EMAIL or not TOKEN:
    sys.exit("Missing CDATA_EMAIL / CDATA_ACCESS_TOKEN (set in .env or the OS environment).")

def main():
    print("=" * 78)
    ok_count = 0
    for codename, display, envkey in TOOLKITS:
        url = urls.get(envkey)
        print(f"\n{display}  ({codename})")
        if not url:
            print(f"  MISSING: {envkey} not found in toolkit_URLs.env"); continue
        print(f"  url: {url}")
        try:
            cl = MCPClient(url, EMAIL, TOKEN)
            cl.initialize()
            tools = cl.list_tools()
        except MCPError as e:
            print(f"  CONNECT FAILED: {e}"); continue
        ok_count += 1
        print(f"  connected OK - {len(tools)} tool(s):")
        for t in tools:
            params = list((t.get("inputSchema") or t.get("input_schema") or {}).get("properties", {}).keys())
            print(f"    - {t['name']}({', '.join(params)})")
    print("\n" + "=" * 78)
    print(f"{ok_count}/{len(TOOLKITS)} toolkits reachable.")
    if ok_count < len(TOOLKITS):
        print("Fix connectivity before running the write-behavior checklist.")
    else:
        print("All four reachable. Next: the write-behavior checklist (guarded validates,")
        print("raw doesn't, dedupe, table-scope) - run those explicitly, one at a time.")

if __name__ == "__main__":
    main()
