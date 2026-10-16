# Getting started

Everything needed to install IraLens, verify the install, and see exactly
which capabilities work on your machine.

## Install

Requirements: Python 3.10+ (3.11 tested), Linux or macOS for the tested path.
Windows setup is provided (`scripts/setup.ps1`) and is untested.

```bash
git clone https://github.com/pritamduttachoudhury0-collab/IraLens.git
cd IraLens
./scripts/setup.sh          # creates .venv, installs IraLens, runs a health check
source .venv/bin/activate
iralens doctor              # what works right now, per capability
iralens install-engine      # optional: the local browser engine (JS pages, automation)
```

`pip install -e .` from the checkout works too; `./scripts/setup.sh --no-dev`
skips the test tools. The package is not yet published to PyPI.

## Prove it works

```bash
iralens search "solar cell efficiency" --limit 5     # keyless web search
iralens read "https://example.com"                   # read one page to clean text
iralens --json doctor                                # honest capability report, as JSON
.venv/bin/python scripts/verify_interfaces.py        # CLI + Python + MCP checks
python -m pytest -q                                  # offline test suite (live tests auto-skip)
```

`verify_interfaces.py` prints PASS / FAIL / BLOCKED per check: anything that
needs a service your network cannot reach is reported BLOCKED and listed as
unverified, never counted as a pass.

## Your first searches and reads

```bash
iralens search "your query" --limit 5 --json
iralens search-api "your query" --engine duckduckgo --cache bypass
iralens read https://en.wikipedia.org/wiki/Web_search_engine --max-chars 4000
iralens research "your question" --rounds 2
```

Then pick your interface: the [Python facade and CLI](../README.md#the-interface),
the [HTTP API](api.md), or the MCP server (`iralens mcp`; configuration in the
[README](../README.md#for-ai-agents)).

## Capability status

`iralens doctor` is the source of truth for *your* machine. Rough guide for a
fresh install:

| Capability | Works out of the box | Notes |
|---|---|---|
| Keyless search (DuckDuckGo, Bing, Wikipedia API) | yes* | *Live availability depends on your network and the sources' bot policies; outcomes are always reported honestly. |
| Page and PDF reading (local) | yes | Scanned-PDF OCR needs `pdftoppm` + `tesseract` installed. |
| Research mode | yes | Quality depends on reachable search sources; output is leads, not verdicts. |
| GitHub source | yes | Public data without credentials; `gh` CLI unlocks private repos and code search. |
| RSS / V2EX | yes | Public feeds only. |
| Browser engine (JS rendering, automation) | after `iralens install-engine` | Pinned release, startup-probed at install; stealth off; localhost-only control. |
| YouTube | when `yt-dlp` is installed | Bundled as a dependency; outages surface as honest health status. |
| Twitter/X, Reddit, Bilibili, XiaoHongShu, LinkedIn, Xueqiu, Boss直聘, Facebook, Instagram | with your credentials/tools | Each source reports honest health with multi-backend fallbacks; no auto-login. |
| Podcast transcription | when `groq_key` is configured | `iralens configure groq_key=...`. |
| HTTP API | yes | `iralens serve`; a non-loopback bind requires a 32+ character token. |
| Docker stack | build on your host | `docker compose up --build -d`; verify on the target host before relying on it. |

## Configuration

`~/.iralens/config.yaml` (or `IRALENS_HOME`), overridable per-key with
`IRALENS_*` environment variables:

```bash
iralens configure groq_key=...            # transcription
iralens configure stealth=true            # engine anti-detection (off by default)
iralens configure proxy=http://...        # engine proxy
```

Search/read tuning via environment, examples:

```bash
IRALENS_SEARCH_ENGINES=duckduckgo,wikipedia
IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"
IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"
IRALENS_CACHE_ENABLED=false
IRALENS_PDF_OCR_ENABLED=false
```

The full key list is in `iralens/settings.py`. Session state and caches live
in `~/.iralens/state/`.

## Next steps

- [Search](search.md) — engines, filters, reliability, caching, source policy
- [Reading pages and documents](reading-pages-and-documents.md) — modes, PDF/OCR, errors
- [Research mode](research.md) — cited reports with provenance and replay
- [API](api.md) — native and Serper-compatible HTTP endpoints
- [Deployment](deployment.md) — the self-hosted Docker stack and operations
