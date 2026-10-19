# Stealth mode

Stealth mode makes the bundled browser engine present **one coherent browser
identity** instead of a mixture of Python-client and browser signals. It is a
consistency measure, not an anti-bot guarantee: it does not solve CAPTCHAs, and
a challenge page is still reported rather than passed off as content.

Off by default. Turn it on per install or per process:

```bash
iralens configure stealth=true
IRALENS_STEALTH=true iralens open https://example.com --browser
```

`iralens doctor` reports whether it is active (or that it is configured but
has not started yet), and `iralens-browser-engine mcp --stealth` announces it
on stderr together with its limits.

## What changes

Two layers are set from a single profile, so they cannot describe different
machines.

### HTTP requests

Every request the engine makes — the navigation itself and each subresource it
fetches — carries the header set a desktop Chrome sends, in Chrome's order:

```
Host, Connection, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform,
Upgrade-Insecure-Requests, User-Agent, Accept, Sec-Fetch-Site, Sec-Fetch-Mode,
Sec-Fetch-User, Sec-Fetch-Dest, Referer, Cookie, Content-Type,
Accept-Encoding, Accept-Language
```

* `Sec-Fetch-*` is derived per request: `navigate`/`document` with
  `Sec-Fetch-User: ?1` for a navigation, `no-cors`/`script` for a classic
  script, `cors`/`empty` for fetch and XHR. `Sec-Fetch-Site` is computed from
  the initiator (`none`, `same-origin`, `same-site`, `cross-site`).
* `Referer` follows Chrome's default `strict-origin-when-cross-origin` policy:
  the full URL for same-origin, the origin for cross-origin, nothing on an
  https→http downgrade.
* `Origin` is sent on CORS requests and non-GET navigations, never on a
  same-origin GET.
* `sec-ch-ua` brands are generated with the same deterministic GREASE algorithm
  Chrome uses, which the engine also uses for
  `navigator.userAgentData.brands`. Header and JavaScript agree by
  construction, for any Chrome major version.
* The client hints are gated exactly as Chrome gates them: sent to HTTPS and
  to loopback origins (`localhost`, `127.0.0.1`, `::1`, `*.localhost`), and
  omitted on a plain-HTTP public origin, where no stock Chrome sends them.
* `Accept` matches the resource type (`text/html,…` for documents,
  `text/css,*/*;q=0.1` for stylesheets, `*/*` for scripts and fetch).
* `Accept-Encoding` lists only encodings this install can actually decode
  (`gzip, deflate`, plus `br`/`zstd` when a decoder library is present), and
  the response is decoded accordingly.

Subresources reach the wire only for external `<script src=…>` elements; this
engine does not issue `fetch`/XHR requests from page JavaScript, so the
`cors`/`empty` classification is exercised by tests and by future subresource
types rather than by ordinary pages today.

### The page's `navigator`

The engine already derives its navigator surfaces from internal hooks. Stealth
feeds those hooks, which is the same mechanism the upstream Rust tier uses —
nothing is monkey-patched, and `Function.prototype.toString` is not replaced:

| Surface | Stealth value |
|---|---|
| `navigator.userAgent` | the profile UA (default: Chrome 145 on Windows 10 x64) |
| `navigator.userAgentData.brands` | the same brands as `sec-ch-ua` |
| `navigator.userAgentData.platform` / `platformVersion` | `Windows` / `15.0.0` (or derived from the UA) |
| `navigator.platform` | `Win32` (or derived from the UA) |
| `navigator.language` / `languages` | the profile locale, default from the environment |
| `navigator.hardwareConcurrency` | one value from a realistic pool, **pinned for the engine instance** |
| `navigator.deviceMemory` | one value from `{4, 8}`, pinned the same way |
| `screen.width/height/avail*` | the configured screen (default 1920×1080, avail height −40 px) |
| `navigator.geolocation` | the configured point, if one is configured |

The CPU and memory values are re-asserted after page initialization because the
engine reseeds its pools on every navigation; a real browser does not change its
CPU count on reload.

## Configuration

| Key | Meaning | Default |
|---|---|---|
| `stealth` | enable the mode | off |
| `user_agent` / `stealth_user_agent` | identity UA; platform strings and client hints are re-derived from it | Chrome 145 / Windows 10 x64 |
| `stealth_language` | BCP-47 locale for `navigator.language` and `Accept-Language` | `LANG`/`LC_ALL`, else `en-US` |
| `stealth_screen` | `WIDTHxHEIGHT` for `screen` | `1920x1080` |
| `stealth_geolocation` | `latitude,longitude` reported by `navigator.geolocation` | unset (engine placeholder) |

The engine accepts the same values as flags (`--stealth`, `--user-agent`,
`--language`, `--screen`, `--geolocation`) for `mcp`, `fetch`, and `scrape`.

`OBSCURA_GEOLOCATION` is honoured as a fallback, matching the upstream
contract.

## What stealth does not do

These are reported in every MCP handshake under
`engineCapabilities.stealth.limitations`:

* **No TLS or HTTP/2 impersonation.** The handshake is Python's and the host
  TLS stack's. Chrome's JA3/JA4 and HTTP/2 SETTINGS fingerprint are not
  reproduced. This is the largest remaining gap, and the reason stealth is not
  claimed to defeat a serious bot-management product.
* **No WebGL.** The engine has no GL context, so `canvas.getContext('webgl')`
  returns `null`. Fabricating strings without a real driver is a worse tell
  than an honest absence, so stealth leaves it alone. The same is true of the
  2D context: `canvas.getContext('2d')` is `null`, so canvas-text and
  font-metric probes fail rather than return a fabricated hash.
* **No timezone emulation.** `Intl.DateTimeFormat().resolvedOptions().timeZone`
  and `getTimezoneOffset()` report the host's zone, because V8 resolves them
  from the process environment. That is what Chrome on this host would report
  too; forcing a different zone would mean patching `Intl`, which is exactly
  the kind of page-visible lie this tier avoids. Set `TZ` in the parent
  process if the host zone should change.
* **No font differentiation.** The engine reports the same metrics for every
  font family.
* **No TCP/IP shaping.** SYN options remain the host kernel's.
* **No CAPTCHA solving, and no challenge solving.** A detected challenge is
  still surfaced as `verification_required`.

Measured expectations from the audit that motivated this work are in the
`KNOWN_LIMITATIONS.md` entry on stealth.

## Verifying it

The wire behaviour is covered by `tests/test_stealth_mode.py`, which records
raw HTTP requests from a socket (so header order and casing are asserted, not
inferred) and reads back the same engine's `navigator` values. A quick manual
check, which also prints the page probe as JSON:

```bash
python -m pytest tests/test_stealth_mode.py -q
.venv/bin/python scripts/verify_stealth.py
```

`scripts/verify_stealth.py` follows the audit's method: it serves a page from a
raw socket on loopback, records the bytes of every request, and checks that the
document request carries Chromium's header order, that the client-hint brands
equal `navigator.userAgentData.brands`, that `Accept-Language` equals
`navigator.languages`, and that the capability payload reports `applied: true`
together with the limits above. It needs no network access.
