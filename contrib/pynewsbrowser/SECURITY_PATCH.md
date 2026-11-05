# PyNewsBrowser: closing an SSRF hole

This folder contains a fix for a security defect found while reviewing
PyNewsBrowser for the IraLens integration. It is **not** used by IraLens at
runtime — IraLens already enforced this policy on its own fetch path, and the
news capabilities were ported as native IraLens modules. This is the same fix,
made available to the upstream project.

## The problem

PyNewsBrowser's `HttpClient` opens `httpx.AsyncClient(follow_redirects=True)`
and never inspects the URL it is asked to fetch. Two entry points take a URL
straight from the caller:

| Entry point | File |
| --- | --- |
| `GET /api/read?url=...` | `src/pynewsbrowser/webapp.py` |
| `ops learn-json --url-template ...` | `src/pynewsbrowser/newsops/learn.py` |

The README documents `serve --host 0.0.0.0`. Put those together and the app is
an open fetch proxy: a visitor can make the **server** request

```
http://169.254.169.254/latest/meta-data/iam/security-credentials/
http://127.0.0.1:6379/
http://192.168.1.10/admin
```

and read the response body back. On a cloud host the first one returns the
instance's IAM credentials.

Following redirects makes a first-URL-only check useless: an attacker's own
*public* site can answer `302 -> http://169.254.169.254/...`. Every hop has to be
re-validated.

## The fix

Three files change, plus one new module:

* **`safeurl.py`** (new) — `normalize_public_http_url` rejects non-HTTP schemes,
  embedded credentials, loopback, link-local (including the metadata address),
  RFC1918 and other non-global IP literals, single-label hosts, and
  `.local` / `.internal` names. It understands the legacy short forms `127.1`,
  `2130706433` and `0177.0.0.1`, which a dotted-quad-only check walks straight
  past. `SafeRedirects` follows redirects manually and re-validates each hop.
  Standard library only — no new dependency.
* **`http.py`** — validates the URL in `get()` and `post_form()`, and switches to
  `follow_redirects=False` + `SafeRedirects` so the guard also covers hops.
* **`webapp.py`** — `/api/read` answers `400` for an unsafe URL instead of
  fetching it.
* **`newsops/learn.py`** — a learned operation cannot be pointed at a private
  host in the first place.

## Applying it

Use a **separate virtualenv** for this. PyNewsBrowser's dev extras pull in
`pytest-asyncio`, which auto-loads as a pytest plugin and changes the test
environment of anything else installed in the same venv.

```bash
python -m venv .venv-pnb && . .venv-pnb/bin/activate
pip install -e ".[web,dev]"

cp /path/to/safeurl.py src/pynewsbrowser/safeurl.py
patch -p2 < ssrf.patch          # paths in the diff are PyNewsBrowser/src/...
cp /path/to/test_safeurl.py tests/test_safeurl.py
python -m pytest -q
```

**Install caveat seen here.** `pip install -e ".[web,dev]"` failed in this
workspace with a pip resolver assertion:
`Candidate is not for this requirement lxml[html-clean,html-clean] vs
lxml[html-clean]`. It is a resolver bug over `lxml`'s duplicated extra, not a
problem with the patch. The verification below was run with:

```bash
pip install pytest pytest-asyncio respx httpx python-dateutil typer
pip install --use-deprecated=legacy-resolver feedparser beautifulsoup4 fastapi uvicorn
pip install --no-deps -e .
```

That leaves `trafilatura` and `readability-lxml` uninstalled. They are article
extraction backends; **none of the security tests depend on them**, and all 37
tests pass without them. Install them separately if you want the full
extraction stack.

`ssrf.patch` was generated against the uploaded archive. If it does not apply
cleanly, the four changes are small enough to make by hand from the diff.

## Verification

Run in this workspace against the uploaded copy:

| | Result |
| --- | --- |
| Original test suite, patch applied | **12 passed** — no regressions |
| Original + new security tests | **37 passed** |
| New tests with **both** guard layers removed | **2 failed** — they detect the real defect |

That last row matters: a security test that passes without the fix proves
nothing. Removing only the `get()` check still passed, because `SafeRedirects`
caught it too; removing both made the tests fail with
`AssertionError: a request was attempted for a blocked target`.

## What this does not do

* It does **not** resolve DNS for every request by default. A hostname like
  `169.254.169.254.xip.io` passes text validation because it *is* a hostname.
  `resolve_is_public()` and `SafeRedirects(..., check_dns=True)` cover that
  rebinding shape; turn it on when the URL comes from an untrusted caller.
  IraLens handles this with DNS pinning in its transport layer.
* It does **not** touch CAPTCHAs, bot checks, or login walls. Those are access
  controls. IraLens detects and reports them (`verification_required`, with the
  provider and the marker that fired) and then uses a different source. It does
  not defeat them, and neither does this patch.

## Licence

`safeurl.py` is ported from IraLens. PyNewsBrowser is MIT licensed
(`Copyright (c) 2026 PyNewsBrowser contributors`); the ported news-discovery
ideas in `iralens/search/` carry the same attribution.
