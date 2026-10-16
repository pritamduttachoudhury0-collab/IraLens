# Self-hosted service operations

This guide covers the optional single-host Docker deployment. The API and
Compose files are code, not a tested production image or managed service: build
and exercise them on the target host before relying on them. There is no
availability/SLA commitment.

## Start the local stack

From the repository root, install Docker Engine and the Compose plugin, then
set secrets outside source control:

```bash
export IRALENS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose up --build -d
```

The API is published on `127.0.0.1:8000` by default. Compose requires a
service key of at least 32 characters; the generated example above exceeds
that. For another host port, set `IRALENS_PORT`. The SearXNG service is
reachable only on the Compose
network; Compose does not publish its port to the host. Do not change that
unless you have a deliberate network and access-control plan. The API requires
`IRALENS_API_KEY` even on loopback in this deployment. Put a TLS-terminating
reverse proxy in front of it before remote access, and add edge rate limits.

Smoke-check the health route and an authenticated request (these do not prove
upstream search availability):

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS -H "Authorization: Bearer $IRALENS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"IraLens local deployment","max_results":3}' \
  http://127.0.0.1:8000/v1/search
```

Search success is only established by an actual result with successful engine
outcomes. An HTTP health response merely shows that the API process answers.
The private SearXNG in this compose file still forwards queries to upstream
search providers according to its configuration.

## State, logs, and monitoring

- `iralens-state` stores the local SQLite cache and durable per-engine circuit
  state. Keep the named volume on local storage, back it up consistently, and
  restore it only to a compatible single-node deployment. It records source
  host/engine labels and counters, not search queries or page content.
- `searxng-cache` is a separate cache volume. Do not put either volume in Git
  or an untrusted/shared network filesystem.
- `docker compose logs --tail=200 iralens searxng` shows service logs. IraLens
  deliberately avoids logging query strings, request bodies, API tokens, and
  upstream response bodies. Still restrict log access and retention.
- `GET /health` is unauthenticated liveness. Authenticated `GET /metrics`
  exposes process-local Prometheus counters/histograms with bounded labels;
  they reset when the API restarts. Authenticated `GET /v1/health/search`
  snapshots persistent engine circuit states/cooldowns and reports whether the
  local state store is available, without making upstream requests. Scrape both
  on a private monitoring network, and alert on sustained
  request failures, open circuits, elevated latency, or a missing scrape.
- Monitoring has not been operated for seven consecutive days in this workspace.
  Configure alert delivery, escalation, retention, and recovery checks before
  claiming service monitoring.

## Maintenance and incident response

1. Before an upgrade, review IraLens dependencies, the SearXNG image release,
   configuration changes, and applicable upstream provider policies. Pin and
   validate the image version you deploy; the example Compose file is intended
   as a convenient starting point, not a reproducible supply-chain lock.
2. Back up `iralens-state` and record deployed image identifiers. Test restore
   procedures on a separate host. Never include API keys, browser state,
   `.env`, cache contents, or the state volume in source-control artifacts.
3. If a provider blocks requests or source terms are unclear, stop or disable
   that backend rather than rotating public instances to evade a restriction.
   Review source policies and contact the provider as appropriate.
4. After recovery, observe per-engine outcomes, persistent circuit status, and
   independent successful searches. A green liveness check alone is not
   evidence that search is working.

See [`API.md`](API.md), [`SEARCH.md`](SEARCH.md),
[`RELIABILITY.md`](RELIABILITY.md), and [`SOURCE_POLICY.md`](SOURCE_POLICY.md).

## GitHub Actions daily watch

`.github/workflows/daily-watch.yml` runs at most two serialized read canaries
per scheduled day against this project's GitHub HTML/API pages. The
`workflow_dispatch` trigger is for manual verification. Every run uploads
`daily-watch-<run_id>` with a `daily-watch.json` record (pass/fail, method,
latency, content length, typed error); a failed read fails the job *but still
uploads the JSON*. Artifacts have 30-day retention. No page text is retained.
This is not a search/60-page/PDF acceptance result. GitHub only registers
schedule/manual dispatch after this workflow is merged to **main**; a workflow
on an unmerged branch cannot be assumed live. To verify after merge:

```bash
gh workflow run daily-watch.yml --ref main
gh run list --workflow daily-watch.yml --limit 3
gh run download <run-id> --name daily-watch-<run-id> --dir /tmp/iralens-watch
```

Inspect the JSON and conclusion rather than treating a green job as a general
web-availability claim. If GitHub rejects the manual trigger before merge,
record it as **not run**, not a pass. See `docs/READ_FAILURES.md` for missing
audit data and triage rules.
