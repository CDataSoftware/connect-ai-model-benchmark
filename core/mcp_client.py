"""SSE-aware MCP JSON-RPC client for Connect AI (baseline general + optimized toolkit)."""
import base64, json, time, uuid
import requests


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, base_url, email, token, timeout=90):
        self.base_url = base_url.rstrip("/")
        creds = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.timeout = timeout

    def _rpc(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params is not None:
            payload["params"] = params
        # Retry transient network faults (DNS/connection drop, read timeout) with backoff so a
        # brief wifi blip mid-trajectory doesn't kill the whole run.
        for attempt in range(5):
            try:
                r = requests.post(self.base_url, headers=self.headers, data=json.dumps(payload), timeout=self.timeout)
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == 4:
                    raise MCPError(f"MCP {method} network error after retries: {str(e)[:200]}")
                time.sleep(min(3 * (2 ** attempt), 30))
        if r.status_code >= 400:
            raise MCPError(f"MCP {method} HTTP {r.status_code}: {r.text[:300]}")
        body = r.text
        if "text/event-stream" in r.headers.get("Content-Type", "") or "data:" in body:
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            raise MCPError(f"MCP {method}: no data line in SSE body")
        return json.loads(body)

    def initialize(self):
        return self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "benchmark-client", "version": "0.1.0"}})

    def list_tools(self):
        res = self._rpc("tools/list", {})
        if "error" in res:
            raise MCPError(f"tools/list error: {res['error']}")
        return res.get("result", {}).get("tools", [])

    def call_tool(self, name, arguments):
        res = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if "error" in res:
            return {"error": res["error"]}
        return res.get("result", {})

    @staticmethod
    def result_text(result, max_chars=8000):
        """Flatten a tools/call result to text, truncated."""
        if isinstance(result, dict) and "error" in result:
            body = json.dumps(result)
        else:
            parts = []
            for block in (result.get("content", []) if isinstance(result, dict) else []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            body = "\n".join(parts) if parts else json.dumps(result)
        if len(body) > max_chars:
            body = body[:max_chars] + " ...[truncated]"
        return body
