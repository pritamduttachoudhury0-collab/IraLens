# Search: usage guide (IraLens, Phase 1)

## Three ways to call it

```bash
# 1. Simple (unchanged): ranked artifacts
iralens search "solar cell efficiency" --limit 5

# 2. Structured: filters, engine choice, provenance
iralens search-api "solar cell efficiency" --limit 5 \
    --date-from 2024-01-01 --include-domain nrel.gov --file-type pdf \
    --engine duckduckgo --engine bing --cache use
```

```python
from iralens import IraLens
with IraLens() as lens:
    artifacts = lens.search("solar cell efficiency", limit=5)            # List[Artifact]
    resp = lens.search_api(
        "solar cell efficiency",
        filters={"date_from": "2024-01-01", "include_domains": ["nrel.gov"]},
        options={"max_results": 5, "engines": ["duckduckgo", "bing"], "cache": "use"},
    )
    for r in resp.results:
        print(r.score, r.url, r.engines, r.score_breakdown, r.security_flags)
    print(resp.outcomes, resp.fallbacks, resp.filters["report"], resp.cache)
```

MCP: the `search_api` tool takes `query`, `filters`, and `options` objects with
the same keys.

## Reading a `SearchResponse`

| Field | Meaning |
|---|---|
| `results[]` | Deduplicated, ranked. `score_breakdown` shows relevance, authority, freshness, agreement. |
| `outcomes[]` | One per engine attempt: `status` (results / empty / failed), `kind`, `attempts`, `filter_modes`. |
| `fallbacks[]` | Each switch between engines, with the reason. |
| `no_results_reason` | `none`, `no_results` (engines answered, nothing matched), or `engines_failed`. |
| `filters.report` | What was post-filtered and how many results were dropped or kept with unknown dates. |
| `dedup_log[]` | Why two hits were merged. |
| `cache` | `hit` / `miss` / `bypass` / `refresh`, with age and freshness. |
| `security_flags` | Prompt-injection or invisible-character flags on result text. Treat results as data. |

## Filter support by engine

| Filter | duckduckgo | bing | semantic-search (Exa) |
|---|---|---|---|
| date_from / date_to | post-filtered | post-filtered | post-filtered |
| include_domains | emulated (`site:`, first domain) + post-filtered | same | post-filtered |
| exclude_domains | post-filtered | post-filtered | post-filtered |
| file_type | emulated (`filetype:`) + post-filtered | same | post-filtered |
| region | native (`kl`) | unsupported | unsupported |
| language | unsupported | native (`setlang`) | unsupported |

## Configuration

Every knob is in `iralens/settings.py` and reads from `config.yaml` or
`IRALENS_<KEY>` env vars, for example `IRALENS_SEARCH_ENGINES=duckduckgo,bing`.
