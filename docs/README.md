# IraLens documentation

Start with the [README](../README.md) for the product overview and quick
start. Each guide below opens with one plain sentence on what it is for.

## Guides

| Doc | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, verify, capability status, configuration. |
| [Search](search.md) | Engines, filters, ranking, reliability, caching, and the source policy to review before deployment. |
| [Reading pages and documents](reading-pages-and-documents.md) | The read chain, PDF and scanned-document handling, budgets, and the typed error vocabulary. |
| [Research mode](research.md) | Bounded research runs with cited statements, provenance, and replay. |
| [API](api.md) | The single-node HTTP service: native endpoints, Serper compatibility, metrics, limits. |
| [Security](security.md) | The threat model and guard layers for Internet content in an AI agent. |
| [Stealth mode](stealth.md) | The engine's coherent browser identity: what changes, what does not, and how to verify it. |
| [Deployment](deployment.md) | The self-hosted Docker stack, state, monitoring, and the daily-watch canary. |

## Reference

| Doc | What it is |
|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | The system's layers, modules, and unified models. |
| [DECISIONS.md](../DECISIONS.md) | Design notes — why things are the way they are. |
| [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) | Every known limit, stated plainly, with dated verification records. |
| [BENCHMARKS.md](../BENCHMARKS.md) | The frozen acceptance benchmark and every recorded run. |
| [CHANGELOG.md](../CHANGELOG.md) | What changed, release by release. |

## Audit evidence

`docs/audits/` holds the dated audit and verification records for the
project: the [license audit](audits/license-audit.md), the
[production acceptance audit](audits/production-acceptance-audit-2026-10-09.md),
the [paid-search replacement audit](audits/paid-search-replacement-audit-2026-10-14.md),
the [page-read failure triage](audits/page-read-triage-2026-10-14.md), and the
[final release verification](audits/final-verification.md).
