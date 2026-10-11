# Self-hosted SearXNG for IraLens (recommended for reliability)

SearXNG is a metasearch front end: one JSON API in front of several upstream
engines, self-hosted by you, with no paid key. For IraLens it is the cheapest
way to get **independent, non-rate-limited engine redundancy**: your own
instance is not shared with anyone, so it does not rate-limit you the way a
public instance does.

Upstream docs: <https://docs.searxng.org/>. Check SearXNG's and each upstream
engine's terms of use for your jurisdiction and use case; self-hosting does not
change what the upstream engines permit.

## 1. Run it

```bash
cd deploy/searxng
docker compose up -d            # http://127.0.0.1:8888
curl -s 'http://127.0.0.1:8888/search?q=test&format=json' | head -c 200
```

`settings.yml` here enables the JSON API (`search.formats: [html, json]`) and
sets a random `secret_key` placeholder — replace it before exposing the
instance to anything but localhost.

## 2. Tell IraLens about it

Two environment variables, both explicit:

```bash
# 1) which instances to use (yours first; public ones optional fallbacks)
export IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"

# 2) permission for the SEARCH TRANSPORT to reach a private address.
#    Without this the SSRF policy blocks loopback/LAN targets, which is
#    correct for page reads and intentional for search too (D-082).
export IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"
```

Then:

```bash
.venv/bin/iralens doctor                       # searxng should be listed
.venv/bin/iralens --json search-api "rust async runtime" --engine searxng
```

Notes:

* The allowlist is an **exact** `host` or `host:port` match; a bare host allows
  any port on that host. Link-local addresses (`169.254.0.0/16`, `fe80::/10` —
  the cloud-metadata range) are refused even when allowlisted.
* It only applies to the search transport. Page reads (`read`, `open`) keep the
  strict public-URL policy, so a private URL that appears in page content is
  still blocked.
* Keep `IRALENS_SEARXNG_COOLDOWN_SECONDS` (default 600) as is unless you run
  several instances; a failing instance is skipped, not retried in a loop.

## 3. Why this matters for the benchmark

The redundancy threshold needs **2+ engines above 80% solo success**. A single
self-hosted SearXNG instance gives you one such engine and removes the public
instances' shared rate limits; pair it with the built-in DuckDuckGo and Bing
backends for the second and third. When your instance aggregates several
upstream engines, treat it as one engine in the metrics — that is what the
benchmark counts.

## 4. Without Docker

Any SearXNG deployment works (`pip install searxng` in its own venv, a systemd
unit, or a container on another machine on your LAN). Only two things matter:
the JSON format is enabled, and
`IRALENS_SEARXNG_INSTANCES` + `IRALENS_ALLOWED_PRIVATE_HOSTS` point at it.
