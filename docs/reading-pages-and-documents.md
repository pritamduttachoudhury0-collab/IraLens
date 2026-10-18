# Reading pages and documents

How IraLens turns a URL — web page or PDF — into clean text, what can go
wrong on the way, and the exact error vocabulary you will see.

## The reading chain

`read()` and `open()` share one backend chain:

1. **Specialized source reader** first (GitHub, YouTube, RSS, …) when the URL
   matches a platform IraLens knows.
2. **Fast static read**: a direct, SSRF-safe fetch with local stdlib
   extraction for HTML, and `pypdf` extraction for PDFs.
3. **Local browser render** (optional engine): automatically for thin or
   JavaScript-shell pages in `mode="auto"`; always with `mode="browser"`.
4. **Remote reader** (`r.jina.ai`): off by default; opt in with
   `IRALENS_READ_REMOTE_READER_ENABLED=1` and it is tried only after the
   direct local read fails. URLs sent to it are shared with that third party.

When the static read and the browser fallback both fail, the error you get is
the *primary* static error with the fallback failure attached as a hint.

```bash
iralens read https://example.com --max-chars 8000       # fast static read
iralens open https://github.com/owner/repo              # source-aware open
iralens --json read https://arxiv.org/pdf/1706.03762    # JSON artifact
```

```python
from iralens import IraLens
with IraLens() as lens:
    page = lens.read("https://example.com")                    # static
    app  = lens.read("https://spa.example/app", mode="browser")  # rendered
print(page.title, page.content[:200], page.metadata.get("truncated"))
```

MCP: the `read` tool takes `url`, optional `mode` (`auto` / `static` /
`browser`), and `max_chars`.

## Titles and main-content selection

A static read reports two things: the page's `title` and its `content`.

- **`title` is metadata, not an excerpt.** It comes from the page's `<title>`,
  falling back to the first `<h1>`. It is bounded to 200 characters, so a page
  whose blocks did not separate can never contribute its whole body as the
  title. `metadata.title_source` says where it came from (`html`, `markdown`
  or `browser`), and `metadata.title_truncated` records that it was clipped.
- **The content budget never applies to the title.** `max_chars` bounds
  `content` only.
- **Main-content selection is one conversion, not two.** When a page has a
  recognisable container (`<main>`, `<article>`, `role="main"`, or a known
  content `id`/`class`) holding at least half the page's prose and at least
  200 visible characters, that region is isolated and converted to Markdown
  exactly once. Otherwise the whole page is converted. Headings, paragraphs,
  lists, links, tables and Unicode survive the isolation; `<script>`,
  `<style>`, `<noscript>`, `<nav>`, `<footer>` and `<aside>` do not.
- `metadata.total_chars` is always reported (it equals `len(content)` when
  nothing was cut), so a partial read can be compared against the real page.

## Budgets and limits

- 5 MB response cap for pages, 20 MB for PDFs (`read_max_bytes`,
  `read_pdf_max_bytes`).
- 60 s overall read deadline with slow-drip protection
  (`read_total_timeout_seconds`), plus per-socket timeouts.
- `max_chars` is enforced on every backend; the artifact's metadata says
  `truncated: true` when the text was cut. The cache stores the full text and
  truncates when serving.
- Static reads are cached for one hour (`cache_page_ttl_seconds`); browser
  and authenticated reads are never cached. See `docs/search.md` for the
  cache table.
- Local extraction is honestly lossy: no DOM, no CSS layout, no JavaScript.
  Tables become pipe rows and widgets disappear. Use `mode="browser"` when
  fidelity matters.

## Read errors you can rely on

A failed read raises a typed error (CLI exit 2) with a specific reason:

| reason | meaning |
|---|---|
| `http_404` | server said not found |
| `http_403` | forbidden — commonly a bot check |
| `http_429` | rate limited by the site |
| `soft_404` | 200 response whose body looks like a not-found page (heuristic) |
| `antibot_challenge` | challenge page detected |
| `response_too_large` | exceeded the size cap |
| `slow_response_deadline` | overall deadline hit (slow drip) |
| `redirect_loop` | redirect chain refused (loop or >5 hops) |
| `blocked_by_security_policy` | SSRF guard refused the URL |
| `pdf_support_missing` | `pypdf` not installed |
| `pdf_encrypted` | password-protected PDF |
| `pdf_no_text` | image-only PDF (a scan) without OCR tools |
| `pdf_ocr_support_missing` | scan, and `pdftoppm`/`tesseract` are not installed |
| `pdf_parse_failed` | corrupt PDF |

## PDFs and scanned documents

- PDFs are detected by magic bytes (a MIME mismatch or a `/pdf/` path still
  resolves) and extracted with `pypdf`. Text is normalized for ligatures,
  soft hyphens, BOMs, and line-wrap hyphenation.
- When `pypdf` returns sparse text, an installed `pdftotext` retries locally
  before OCR.
- Scanned (image-only) PDFs are OCRed **locally** when `pdftoppm` and
  `tesseract` are installed — bounded by subprocess deadlines and a five-page
  default cap, never uploaded. Without the tools you get the typed
  `pdf_ocr_support_missing` error, and `iralens doctor` names what is
  missing. Opt out with `IRALENS_PDF_OCR_ENABLED=false`.
- One unreadable page never loses the document; encrypted files raise
  `pdf_encrypted` instead of being mislabeled as scans.
