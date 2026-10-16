# Self-hosted SearXNG for IraLens (recommended for reliability)

SearXNG is a metasearch front end: one JSON API in front of several upstream
engines, self-hosted by you, with no paid key required by IraLens. It removes
dependence on a shared public SearXNG host, but it is **not an independent web
index**: the upstream engines you enable still receive queries and may rate
limit or block the instance. It does not guarantee availability or permission
for any particular traffic level.

Upstream docs: <https://docs.searxng.org/>. Check SearXNG's and each upstream
engine's terms of use for your jurisdiction and use case; self-hosting does not
change what the upstream engines permit.

## 1. Run it

```bash
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cd deploy/searxng
docker compose up -d            # http://127.0.0.1:8888
curl -s 'http://127.0.0.1:8888/search?q=test&format=json' | head -c 200
```

`settings.yml` enables the JSON API (`search.formats: [html, json]`). Compose
requires `SEARXNG_SECRET` and injects it at runtime; keep the value out of
version control and do not expose this standalone instance outside loopback
without separate access controls.

## 2. Tell IraLens about it

Two environment variables, both explicit:

```bash
# 1) select only the private instance (IraLens does not bundle public instances)
export IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"

# 2) permission for the SEARCH TRANSPORT to reach a private address.
#    Without this the SSRF policy blocks loopback/LAN targets, which is
#    correct for page reads and intentional for search too.
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

The frozen benchmark's redundancy gate needs **2+ configured backends above
80% solo success**. A self-hosted SearXNG URL is one backend as measured by
IraLens, even when it aggregates several upstream providers; the built-in
DuckDuckGo and Bing backends are counted separately. Whether those sources
answer reliably, and whether their terms permit a given use, still needs
measurement and review.

## 4. Without Docker

Any SearXNG deployment works (`pip install searxng` in its own venv, a systemd
unit, or a container on another machine on your LAN). Only two things matter:
the JSON format is enabled, and
`IRALENS_SEARXNG_INSTANCES` + `IRALENS_ALLOWED_PRIVATE_HOSTS` point at it.
