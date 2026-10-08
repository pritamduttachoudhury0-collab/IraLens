"""Exercise the public CLI, Python facade, and MCP server; print one verdict per check.

Run from a checkout after ./scripts/setup.sh:
    .venv/bin/python scripts/verify_interfaces.py

Checks marked (LIVE) need api.github.com. Web-search checks report what the
network allowed; they do not assert that web research succeeded.
"""
import json
import os
import select as _sel
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
BIN = os.path.join(os.path.dirname(PY), "halfiralens")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)


def run(args, timeout=300):
    p = subprocess.run([BIN] + args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ---------------- CLI
rc, out, _ = run(["--version"])
check("cli --version", rc == 0 and "half-iralens" in out, out.strip())

rc, out, _ = run(["--json", "doctor"])
doc = json.loads(out) if rc == 0 else {}
check("cli doctor --json", rc == 0 and "sources" in doc and "browser" in doc,
      f"{len(doc.get('sources', {}))} sources, browser={doc.get('browser', {}).get('status')}")

rc, out, _ = run(["sources", "--json"])
check("cli sources --json (flag after subcommand)", rc == 0 and isinstance(json.loads(out), list))

rc, out, _ = run(["fetch", "github", "search_repos", "query=headless browser", "limit=3", "--json"])
data = json.loads(out) if rc == 0 else []
check("cli fetch github search_repos (LIVE)", rc == 0 and len(data) > 0,
      f"{len(data)} live repos, first={data[0]['url'] if data else None}")

rc, out, _ = run(["--json", "search-api", "solar panel efficiency", "--cache", "bypass"])
sa = json.loads(out) if rc == 0 else {}
check("cli search-api --json (structured, machine-readable)", rc == 0 and "outcomes" in sa,
      "engine outcomes: " + ", ".join(f"{o['engine']}={o['status']}" for o in sa.get("outcomes", [])))

rc, out, _ = run(["--json", "research", "solar panel efficiency 2025", "--rounds", "1"])
rr = json.loads(out) if rc == 0 else {}
check("cli research --json (public web path)", rc == 0 and "stop_reason" in rr,
      f"stop_reason={rr.get('stop_reason')} sources={len(rr.get('sources', []))} (web engines blocked here)")

# ---------------- Python facade (run in a child process with the venv)
py_code = r'''
import json
from halfiralens import HalfIraLens
from halfiralens.errors import HalfIraLensError
out = {}
with HalfIraLens() as h:
    try:
        r = h.search("solar panel efficiency", limit=3)
        out["search"] = ["list", len(r)]
    except HalfIraLensError as e:
        out["search"] = ["raised", e.error_type]
    sa = h.search_api("solar panel efficiency", options={"cache": "bypass"})
    out["search_api"] = ["SearchResponse", len(sa.results), [o.status for o in sa.outcomes]]
    rep = h.research("solar panel efficiency 2025", options={"max_rounds": 1})
    out["research"] = ["ReportJSON", json.loads(json.dumps(rep.to_dict()))["stop_reason"]]
    repos = h.fetch("github", "search_repos", query="headless browser", limit=2)
    out["fetch_github_live"] = ["repos", len(repos)]
    ver = h.doctor() if hasattr(h, "doctor") else None
    out["doctor"] = ["dict", len(ver or {})]
print(json.dumps(out))
'''
p = subprocess.run([PY, "-c", py_code], capture_output=True, text=True, timeout=600,
                   env={**os.environ, "PYTHONPATH": ROOT})
if p.returncode == 0:
    res = json.loads(p.stdout.strip().splitlines()[-1])
    check("python search() returns List[Artifact] or raises typed error", res["search"][0] in ("list", "raised"),
          str(res["search"]))
    check("python search_api() returns SearchResponse", res["search_api"][0] == "SearchResponse",
          str(res["search_api"]))
    check("python research() returns JSON-serializable report", res["research"][0] == "ReportJSON",
          str(res["research"]))
    check("python fetch(github) LIVE", res["fetch_github_live"][1] > 0, str(res["fetch_github_live"]))
else:
    check("python facade run", False, p.stderr[-400:])

# ---------------- MCP over stdio

mcp = subprocess.Popen([BIN, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, bufsize=1)


def rpc(msg, wait=240):
    mcp.stdin.write(json.dumps(msg) + "\n")
    mcp.stdin.flush()
    if msg.get("id") is None:
        return None
    ready, _, _ = _sel.select([mcp.stdout], [], [], wait)
    if not ready:
        return {"_timeout": True}
    return json.loads(mcp.stdout.readline())


init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "gate", "version": "1"}}})
check("mcp initialize", init and init.get("result", {}).get("serverInfo", {}).get("name") == "half-iralens")
rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
tl = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
names = [t["name"] for t in tl["result"]["tools"]]
check("mcp tools/list", len(names) == 49 and {"search", "search_api", "research", "source_fetch", "doctor"} <= set(names),
      f"{len(names)} tools")


def call(i, name, args):
    r = rpc({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}, wait=300)
    if not r or r.get("_timeout"):
        return True, {"timeout": True}
    res = r["result"]
    return res.get("isError", False), json.loads(res["content"][0]["text"])


err, body = call(3, "source_fetch", {"source": "github", "op": "search_repos",
                                     "params": {"query": "headless browser", "limit": 2}})
check("mcp source_fetch github (LIVE)", not err, f"{len(body) if isinstance(body, list) else body}")
err, body = call(4, "research", {"question": "solar panel efficiency 2025", "options": {"max_rounds": 1}})
check("mcp research returns structured report", not err and "stop_reason" in body,
      f"stop_reason={body.get('stop_reason')} (web engines blocked here)")
err, body = call(5, "search_api", {"query": ""})
check("mcp bad input -> invalid_input", err and body.get("error") == "invalid_input", str(body)[:120])
err, body = call(6, "doctor", {})
check("mcp doctor", not err and "sources" in body)
mcp.stdin.close()
try:
    mcp.wait(timeout=20)
except subprocess.TimeoutExpired:
    mcp.kill()
check("mcp exits cleanly on stdin close", mcp.returncode == 0, f"rc={mcp.returncode}")

passed = sum(ok for _, ok in results)
print(f"\nSUMMARY {passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
