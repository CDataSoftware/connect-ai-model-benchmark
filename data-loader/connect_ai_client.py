"""Thin client for Connect AI's REST Query API -- shared by the three loaders in this folder.

Same endpoint and auth pattern as core/verifier.py: Basic auth (email:PAT), POST {"query": sql}
to CDATA_QUERY_API, raw SQL text (no bind params -- this API takes a query string, not a
parameterized statement), and a body that returns HTTP 200 even on a failed statement (the
error lands in an "error" key, not the status code).
"""
import os
import time

import requests

DEFAULT_API = "https://cloud.cdata.com/api/query"


class ConnectAIError(RuntimeError):
    pass


class ConnectAIClient:
    def __init__(self, email=None, token=None, api_url=None, timeout=120):
        self.auth = (email or os.environ["CDATA_EMAIL"],
                     token or os.environ["CDATA_ACCESS_TOKEN"])
        self.api_url = api_url or os.environ.get("CDATA_QUERY_API", DEFAULT_API)
        self.timeout = timeout

    def query(self, sql, tries=4):
        """POST one SQL statement, retrying transient network faults with backoff.
        Returns the first result dict ({"rows": [...], "affectedRows": N, ...})."""
        last = None
        for attempt in range(tries):
            try:
                r = requests.post(self.api_url, auth=self.auth, json={"query": sql},
                                   timeout=self.timeout)
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last = e
                if attempt == tries - 1:
                    raise ConnectAIError(f"query API network error after {tries} tries: {str(e)[:200]}")
                time.sleep(min(3 * (2 ** attempt), 30))
        else:  # pragma: no cover
            raise ConnectAIError(f"query API network error: {last}")
        if r.status_code != 200:
            raise ConnectAIError(f"query API HTTP {r.status_code}: {r.text[:400]}\nSQL: {sql[:400]}")
        body = r.json()
        if "error" in body:
            raise ConnectAIError(f"query failed: {body['error'].get('message', body['error'])[:400]}\nSQL: {sql[:400]}")
        results = body.get("results") or [{}]
        return results[0]

    def rows(self, sql):
        res = self.query(sql)
        cols = [c.get("name", c) if isinstance(c, dict) else c for c in (res.get("columns") or [])]
        out = []
        for row in res.get("rows") or []:
            out.append(dict(zip(cols, row)) if cols else row)
        return out

    def count(self, table):
        res = self.query(f"SELECT COUNT(*) AS N FROM {table}")
        rows = res.get("rows") or [[0]]
        return int(float(rows[0][0]))


def sql_literal(value):
    """Render a Python value as a SQL literal. This API takes raw query text, not bind
    params, so every value has to be escaped and typed correctly here -- there is no driver
    layer doing it for us."""
    if value is None or value == "":
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("'", "''")
    return f"'{s}'"


def insert_batch(client, table, columns, rows, batch_size=200):
    """INSERT `rows` (list of dicts, keyed by `columns`) into `table` in batches of
    `batch_size` using multi-row VALUES. Returns the number of rows sent."""
    col_list = ", ".join(columns)
    sent = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        value_tuples = []
        for row in chunk:
            vals = ", ".join(sql_literal(row.get(c)) for c in columns)
            value_tuples.append(f"({vals})")
        sql = f"INSERT INTO {table} ({col_list}) VALUES {', '.join(value_tuples)}"
        client.query(sql)
        sent += len(chunk)
    return sent
