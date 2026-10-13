# Search-source and legal review policy

**Status: review required before commercial or high-volume deployment.** This
file is an engineering inventory, not a legal opinion. No counsel has reviewed
the current source mix, and the restricted build environment could not verify
current upstream terms, robots policies, quotas, or jurisdiction-specific
rules. Passing a technical benchmark does not establish permission to automate
queries.

## Sources and data flows

| Source | IraLens behavior | Key/host dependency | Review required |
|---|---|---|---|
| DuckDuckGo HTML/lite | Directly requests public results pages and parses HTML. | No provider key; DDG receives the query and caller network identity. | Verify current terms, permitted automation/rate, robots/technical restrictions, caching, and result-display rules. |
| Bing HTML | Directly requests public search HTML; parser is experimental and not live-validated here. | No provider key; Microsoft receives the query and caller network identity. | Verify current Microsoft/Bing terms and any published API alternatives. |
| Wikipedia / MediaWiki API | Uses the official `list=search` API with an identifying User-Agent and ≥1 second between calls in one process. | No API key; Wikimedia receives the query. | Verify current API etiquette, rate guidance, attribution and reuse obligations for snippets/results. |
| SearXNG | Only contacted when an operator configures an instance. The bundled compose stack runs it privately. | Self-hosted or explicitly configured endpoint. SearXNG forwards requests to its configured upstream engines. | Review SearXNG and each enabled upstream engine's terms, rate limits, data handling, and deployment jurisdiction. Self-hosting does not grant upstream permission. |
| Exa bridge | Optional external `mcporter` tool, not installed or invoked by default. | User-configured account/provider. | Review the configured provider contract, retention, quotas, resale and output terms. |
| Brave / Serper / Tavily emergency fallback | Optional direct provider adapter. Not called unless both provider and key are configured. | Paid provider key is held in config/environment and sent over HTTPS. | Review provider contract, key handling, quotas, data processing, and failover disclosure. |
| Remote reader (`r.jina.ai`) | Opt-in fallback only after local direct reading fails. Off by default. | Third party receives the page URL and content request. | Review service terms, privacy/data retention and rights to submit pages. |

The keyless adapters are not an independently crawled or licensed search
index. They do not eliminate upstream terms, blocking, or privacy concerns.
Public SearXNG instances are intentionally **not** configured by default because
their operators' policies and availability vary.

## Engineering safeguards already applied

- Use official API endpoints where available (the Wikimedia supplemental
  source); send an identifying User-Agent and respect a minimum request pace.
- Persist CAPTCHA/rate-limit cooldowns so a restart does not immediately repeat
  blocked traffic; do not retry CAPTCHA or 429 responses in a tight loop.
- Do not return CAPTCHA/challenge markup as search results. Keep per-engine
  failures visible in the API response and benchmark output.
- Keep page reads local-first. Remote reading is opt-in and documented.
- Keep paid failover off by default; send its key only in a header, never a
  URL, log, health state, or normal response field. Credentialed fallback
  requests reject redirects to a different origin so headers cannot be sent to
  an unapproved redirect target. The optional Google CSE reference benchmark
  places its key in the provider-required query parameter; it does not persist
  request URLs or bodies, and it also rejects cross-origin redirects. Treat
  local process/proxy observability as sensitive while running that comparison.

These are reliability/privacy controls, not legal permission or a bypass for
anti-bot measures. Operators should not attempt to evade access controls,
CAPTCHAs, rate limits, or authentication.

## Required review before a release claiming compliant scale

A qualified reviewer should document, for each active source:

1. the current terms/API policy version and the exact permitted automated use;
2. allowed request rate, concurrency, cache duration, attribution, and display;
3. data-controller/processor roles, query/page retention, and user disclosure;
4. relevant jurisdiction, copyright/database rights, and contractual terms;
5. whether the system may be offered as a hosted service or only used privately;
6. the source's removal/disable mechanism and contact/escalation path.

Until those items are signed off, run only low-volume, operator-controlled
checks, keep the affected adapter configurable/disableable, and do not advertise
IraLens as a legally cleared or unrestricted search API. For a service that
must avoid public search-site automation, use a licensed search data/API
contract or build/operate an index over sources for which you have appropriate
rights; IraLens does not include that crawler/index today.
