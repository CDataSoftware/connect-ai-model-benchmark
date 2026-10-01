"""OAuth for MCP servers that require it, such as Connect AI Tool Servers.

Toolkit endpoints accept HTTP Basic (email + PAT). Tool Servers accept only a JWT from an
authorization-code login with PKCE, which needs a browser, so log in once before a run:

    python3 -m core.mcp_oauth login      # every configured endpoint that needs OAuth
    python3 -m core.mcp_oauth status     # which endpoints use which auth, and token state

Tokens are cached in ~/.cache/connect-ai-benchmark/mcp_oauth.json (owner-only) and refreshed
automatically. During a run, token_for() never opens a browser: it raises NeedsLogin instead.
"""
import base64, hashlib, http.server, json, os, re, secrets, sys, threading, time, urllib.parse, webbrowser
import requests

CACHE = os.path.expanduser("~/.cache/connect-ai-benchmark/mcp_oauth.json")
PORT = int(os.environ.get("MCP_OAUTH_PORT", "53682"))
LOGIN_TIMEOUT_S = 300


class NeedsLogin(RuntimeError):
    pass


def _key(url):
    """Cache key: the server address without query (?ops= filters tools, not the OAuth resource)."""
    u = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", ""))


def _load():
    try:
        return json.load(open(CACHE))
    except (OSError, ValueError):
        return {"clients": {}, "tokens": {}}


def _save(cache):
    os.makedirs(os.path.dirname(CACHE), mode=0o700, exist_ok=True)
    fd = os.open(CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cache, f, indent=1)


def challenge(response):
    """The protected-resource metadata URL if `response` is an OAuth challenge, else None."""
    if response.status_code != 401:
        return None
    m = re.search(r'resource_metadata="([^"]+)"', response.headers.get("WWW-Authenticate", ""))
    return m.group(1) if m else None


def probe(url, email, pat):
    """How an endpoint answers the harness's Basic credentials: 'basic', 'oauth', or 'http <code>'.
    Probing without credentials is useless -- the gateway challenges any anonymous request."""
    r = requests.post(url, auth=(email, pat), timeout=60, headers={"Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": "probe", "method": "initialize", "params": {
                          "protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "benchmark-client", "version": "0.1.0"}}})
    if challenge(r):
        return "oauth"
    return "basic" if r.status_code < 400 else f"http {r.status_code}"


def discover(url):
    meta_url = challenge(requests.post(url, json={}, timeout=60))
    if not meta_url:
        raise RuntimeError("endpoint did not return an OAuth challenge")
    pr = requests.get(meta_url, timeout=60).json()
    issuer = pr["authorization_servers"][0].rstrip("/")
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        r = requests.get(issuer + path, timeout=60)
        if r.status_code == 200:
            asm = r.json()
            break
    else:
        raise RuntimeError("no authorization-server metadata")
    return {"resource": pr.get("resource") or url, "issuer": issuer,
            "authorization_endpoint": asm["authorization_endpoint"], "token_endpoint": asm["token_endpoint"],
            "registration_endpoint": asm.get("registration_endpoint"),
            "scope": " ".join(pr.get("scopes_supported") or ["openid", "profile", "email"])}


def _client_id(cache, d, redirect_uri):
    c = cache["clients"].get(d["issuer"])
    if c and c.get("redirect_uri") == redirect_uri:
        return c["client_id"]
    if not d["registration_endpoint"]:
        raise RuntimeError("server has no dynamic client registration endpoint")
    r = requests.post(d["registration_endpoint"], json={
        "client_name": "connect-ai-model-benchmark", "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none"}, timeout=60)
    r.raise_for_status()
    cache["clients"][d["issuer"]] = {"client_id": r.json()["client_id"], "redirect_uri": redirect_uri}
    return cache["clients"][d["issuer"]]["client_id"]


def _store(cache, url, d, client_id, tok, old_refresh=None):
    cache["tokens"][_key(url)] = {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token") or old_refresh,
        "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        "token_endpoint": d["token_endpoint"], "resource": d["resource"], "client_id": client_id}


def login(url):
    """Interactive browser login for one MCP URL; caches the resulting tokens."""
    cache = _load()
    d = discover(url)
    redirect_uri = f"http://127.0.0.1:{PORT}/callback"
    client_id = _client_id(cache, d, redirect_uri)
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    auth_url = d["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256", "state": state, "scope": d["scope"], "resource": d["resource"]})

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"Login complete. You can close this tab.")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    server.timeout = LOGIN_TIMEOUT_S
    threading.Thread(target=server.handle_request, daemon=True).start()
    print("  opening the browser to log in (if it doesn't open, visit this URL):\n  " + auth_url)
    webbrowser.open(auth_url)
    deadline = time.time() + LOGIN_TIMEOUT_S
    while "code" not in got and "error" not in got and time.time() < deadline:
        time.sleep(0.5)
    server.server_close()
    if "error" in got:
        raise RuntimeError(f"login refused: {got['error'][0]}")
    if "code" not in got:
        raise RuntimeError("login timed out")
    if got.get("state", [""])[0] != state:
        raise RuntimeError("login state mismatch -- ignoring the response")

    r = requests.post(d["token_endpoint"], data={
        "grant_type": "authorization_code", "code": got["code"][0], "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier, "resource": d["resource"]}, timeout=60)
    r.raise_for_status()
    _store(cache, url, d, client_id, r.json())
    _save(cache)


def token_for(url, force_refresh=False):
    """A valid access token for `url`, refreshing if needed. Never opens a browser."""
    cache = _load()
    t = cache["tokens"].get(_key(url))
    if not t:
        raise NeedsLogin(url)
    if not force_refresh and t["expires_at"] - 60 > time.time():
        return t["access_token"]
    if not t.get("refresh_token"):
        raise NeedsLogin(url)
    r = requests.post(t["token_endpoint"], data={
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"],
        "client_id": t["client_id"], "resource": t["resource"]}, timeout=60)
    if r.status_code != 200:
        raise NeedsLogin(url)
    d = {"token_endpoint": t["token_endpoint"], "resource": t["resource"]}
    _store(cache, url, d, t["client_id"], r.json(), old_refresh=t["refresh_token"])
    _save(cache)
    return cache["tokens"][_key(url)]["access_token"]


def _configured_endpoints():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    from core.envutil import load_env
    load_env(os.path.join(here, ".env"))
    import yaml
    cfg = yaml.safe_load(open(os.path.join(here, "config", "models.yaml"), encoding="utf-8"))
    names = []
    for t in cfg.get("tasks", []):
        for c in t["conditions"]:
            if c["mcp_url_env"] not in names:
                names.append(c["mcp_url_env"])
    return [(n, os.environ[n]) for n in names if os.environ.get(n)]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    force = "--force" in sys.argv
    for name, url in _configured_endpoints():
        try:
            kind = probe(url, os.environ["CDATA_EMAIL"], os.environ["CDATA_ACCESS_TOKEN"])
        except requests.RequestException as e:
            print(f"{name:26} unreachable ({type(e).__name__})"); continue
        if kind == "basic":
            print(f"{name:26} basic auth (email + PAT) -- OK"); continue
        if kind != "oauth":
            print(f"{name:26} {kind} with email + PAT (e.g. toolkit not found) -- check the URL"); continue
        t = _load()["tokens"].get(_key(url))
        state = ("no token" if not t else
                 f"token valid {int((t['expires_at'] - time.time()) / 60)} more min" if t["expires_at"] > time.time() else
                 "token expired" + (", refreshable" if t.get("refresh_token") else ""))
        if cmd == "login" and not force and t and t["expires_at"] <= time.time():
            try:
                token_for(url)
                print(f"{name:26} OAuth -- token refreshed"); continue
            except NeedsLogin:
                pass
        if cmd == "login" and (force or not t or t["expires_at"] <= time.time()):
            print(f"{name:26} OAuth -- logging in")
            try:
                login(url)
                print(f"{name:26} OAuth -- logged in")
            except Exception as e:
                print(f"{name:26} OAuth -- login FAILED: {str(e)[:160]}")
        else:
            print(f"{name:26} OAuth -- {state}")


if __name__ == "__main__":
    main()
