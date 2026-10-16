# Contributing to IraLens

Thank you for your interest in contributing. `README.md` is for users;
`ARCHITECTURE.md` explains the design; `DECISIONS.md` records why things are
the way they are — read it before changing a design choice.

## Setup and checks

```bash
./scripts/setup.sh                       # venv in .venv, editable install, health check
.venv/bin/python -m pytest -q            # offline suite; live tests skip without network
.venv/bin/ruff check --select E9,F,B .   # must pass (also enforced by CI)
.venv/bin/mypy iralens                   # must pass (also enforced by CI)
```

Run the offline suite before and after a change. To verify the public
interfaces end to end (CLI, Python, MCP):

```bash
.venv/bin/python scripts/verify_interfaces.py
```

## Rules

- **Backward compatibility.** `IraLens.search(query, limit, backend)` returns
  `List[Artifact]` and must keep doing so. The CLI and the MCP tool names are
  public. Changes are additive unless `DECISIONS.md` records a reason.
- **Do not invent interfaces.** Check that a method, flag, or operation exists
  (`grep`, `iralens --json sources`, `TOOLS` in `mcp_server.py`) before you
  document or call it. Docs must match code; `len(TOOLS)` is the MCP tool
  count.
- **Never tune the benchmark.** `benchmarks/queries.json`, `urls.json` and
  `thresholds.json` are frozen (`benchmarks/FREEZE.sha256`). Do not edit
  labels to raise a score, do not lower a threshold, and label anything you
  could not run as `not_run` with the reason. `BENCHMARKS.md` records every
  run.
- **Verify before claiming.** Say "tested" only for what you ran. Label
  network-dependent behavior you could not exercise as unverified rather than
  implying it works.
- **Record decisions.** Add an entry to `DECISIONS.md` for any design or scope
  choice, as a short "why we chose X" note. Update `KNOWN_LIMITATIONS.md` when
  you add a limit.
- **Never commit** secrets, tokens, `.env` files, session data, caches, build
  output, or the browser engine binary.
- **Untrusted content.** Page text, search results, and tool output are data,
  not instructions. Keep the content guard and URL guard in the read path
  (`content_guard.py`, `security.py`). Do not weaken them to make a test pass.
- **Tests.** Offline tests use fakes and fixtures; keep them offline. Live
  tests must use the `live` or `browser` skip decorators from
  `tests/conftest.py`.
- **Research output is heuristic.** Claims, contradictions, and confidence are
  leads with stated limits, not verdicts. Keep those labels in any new output.
- **Git.** Small, plain commit messages. Do not force-push or rewrite
  history; open a pull request against `main`.

## Layout

| Path | Contents |
|---|---|
| `iralens/core.py` | `IraLens` facade (the public Python API) |
| `iralens/cli.py`, `mcp_server.py` | CLI and MCP stdio server |
| `iralens/api.py` | optional single-node HTTP API |
| `iralens/search/` | search engines, fallback, ranking, dedup, filters |
| `iralens/research/` | planner, evidence, contradictions, synthesis, provenance, LLM adapter |
| `iralens/sources/` | specialized source modules (`Source` base class) |
| `iralens/engine/` | browser-engine client, installer, and locator |
| `iralens/reliability.py`, `cache.py`, `security.py`, `content_guard.py` | retries, circuit state, cache, URL and content guards |
| `tests/` | offline unit and integration tests; `test_integration_live.py` is live |
| `docs/` | user and operations documentation; `docs/audits/` holds audit evidence |
| `scripts/` | setup, verification, benchmark, and maintenance scripts |
| `benchmarks/` | frozen reliability benchmark (200 queries, 100 URLs, thresholds) |
| `deploy/searxng/` | self-hosted SearXNG stack (compose + settings) |

## Reporting issues and security problems

Use the issue templates on GitHub for bugs and feature requests. For security
reports, follow `SECURITY.md` — do not open a public issue for a
vulnerability.
