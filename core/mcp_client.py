"""SSE-aware MCP JSON-RPC client for Connect AI (baseline general + optimized toolkit)."""
import base64, json, time, uuid
import requests


class MCPError(RuntimeError):
    pass


class MCPTimeout(MCPError):
    """A call ran past CALL_DEADLINE_S. Scored as a failed run, not retried as a harness error."""


# Total time allowed for one MCP call, response included. requests' timeout is per socket read, so a
# server-sent-event stream that stays open (keep-alives, or a connection lost without a close) can
# otherwise block a call indefinitely.
CALL_DEADLINE_S = 1800


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
        self.pending = None  # the tools/call in flight, kept if it never returns

    def _post(self, method, payload):
        # Retry transient network faults (DNS/connection drop, read timeout) with backoff so a
        # brief wifi blip mid-trajectory doesn't kill the whole run.
        for attempt in range(5):
            try:
                return requests.post(self.base_url, headers=self.headers, data=json.dumps(payload),
                                     timeout=self.timeout, stream=True)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == 4:
                    raise MCPError(f"MCP {method} network error after retries: {str(e)[:200]}")
                time.sleep(min(3 * (2 ** attempt), 30))

    def _rpc(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params is not None:
            payload["params"] = params
        deadline = time.monotonic() + CALL_DEADLINE_S
        r = self._post(method, payload)
        try:
            if r.status_code >= 400:
                raise MCPError(f"MCP {method} HTTP {r.status_code}: {self._read_body(method, r, deadline)[:300]}")
            if "text/event-stream" in r.headers.get("Content-Type", ""):
                # return the first JSON-RPC message; don't wait for the stream to close
                for line in r.iter_lines(decode_unicode=True):
                    if time.monotonic() > deadline:
                        raise MCPTimeout(f"MCP {method}: no response within {CALL_DEADLINE_S}s")
                    line = (line or "").strip()
                    if line.startswith("data:"):
                        try:
                            return json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                raise MCPError(f"MCP {method}: no data line in SSE body")
            body = self._read_body(method, r, deadline)
            if "data:" in body:
                for line in body.splitlines():
                    line = line.strip()
                    if line.startswith("data:"):
                        try:
                            return json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                raise MCPError(f"MCP {method}: no data line in SSE body")
            return json.loads(body)
        except requests.exceptions.RequestException as e:
            raise MCPError(f"MCP {method} read failed: {str(e)[:200]}")
        finally:
            r.close()

    @staticmethod
    def _read_body(method, r, deadline):
        chunks = []
        for chunk in r.iter_content(chunk_size=65536):
            if time.monotonic() > deadline:
                raise MCPTimeout(f"MCP {method}: no complete response within {CALL_DEADLINE_S}s")
            chunks.append(chunk)
        return b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")

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
        self.pending = {"tool": name, "arguments": arguments}
        res = self._rpc("tools/call", {"name": name, "arguments": arguments})
        self.pending = None
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
