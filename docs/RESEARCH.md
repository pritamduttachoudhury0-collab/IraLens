# Research: usage guide (Full IraLens, Phase 3)

## Call it

```bash
halfiralens research "What efficiency did solar panels reach in 2025?" \
    --rounds 3 --min-sources 3 --read-top 3
halfiralens --json research "..."        # full report as JSON, including trace
```

```python
from halfiralens import HalfIraLens
with HalfIraLens() as hil:
    report = hil.research("What efficiency did solar panels reach in 2025?",
                          options={"max_rounds": 3, "min_sources": 3, "read_top_n": 3})
    print(report.render_text())
    for st in report.statements:
        print(st["status"], st["confidence"], st["text"], st["source_ids"])
```

MCP: the `research` tool takes `question` and an `options` object with the same keys.

## Options

| key | default | bounds | meaning |
|---|---|---|---|
| `max_rounds` | 3 | 1–10 | research rounds |
| `max_queries` | 12 | 1–50 (also capped by `research_max_queries`) | search calls in total |
| `min_sources` | 3 | 1–20 | distinct domains needed for full coverage |
| `max_results` | 8 | 1–50 | results per search call |
| `read_top_n` | 3 | 0–20 | pages read per round (static reader) |

Unknown option keys are rejected with the list of allowed keys.

## How a run proceeds

1. **Expand.** Round 1 uses the question and its reformulations. Later rounds
   query contested subjects, with "evidence" appended.
2. **Search.** Each query goes through `search_api` with `reformulate=False`, so
   the planner controls the queries. Failures are recorded, not dropped.
3. **Read.** The top unread sources are fetched with the static reader. Read
   failures fall back to snippets and are reported.
4. **Analyze.** Claims are sentences that mention question terms. Contradiction
   candidates are compared across domains. Statements are built per subject cluster.
5. **Stop.** One of the reasons below. The loop stops as soon as one applies.

| `stop_reason` | meaning |
|---|---|
| `coverage_reached` | distinct domains / `min_sources` ≥ `research_coverage_threshold` (0.7) |
| `budget_exhausted` | `max_queries` spent |
| `no_new_sources` | a round added no sources, and some sources exist |
| `search_failed` | a round added no sources, every search failed or was skipped |
| `max_rounds` | the round limit was reached |

## Reading the report

- `statements[]`: `status` is `corroborated` (2+ domains), `single_source`, or
  `contested` (a contradiction candidate involves it). `confidence` is a
  transparent formula: `(base + per_domain × (domains−1)) × mean source score`,
  minus a penalty when contested. The formula and its constants are in
  `research/synthesis.py` and `Settings`.
- `contradictions[]`: heuristic candidates only (`confidence: "heuristic"`). A
  "numeric mismatch" can be two different measurements. Verify before relying on it.
- `sources[]`: each with `read_status` (`read`, `read_failed`, `snippet_only`),
  score breakdown, the queries and rounds that found it, and flags.
- `claims[]`: sentence-level claims with the source they came from.
- `provenance`: graph with nodes (question, query, source, claim, statement,
  contradiction) and edges (issued, returned, states, supports, conflicts_with).
- `limitations[]`: always includes "no language model was used", plus anything
  that weakened the run (failed searches, snippet-only sources, unread pages).
- `trace`: every search response, every expander decision, every page read
  (truncated to `research_page_chars`), and the date used for recency.

## Replay

`ResearchPlanner.replay(report.to_dict(), settings)` rebuilds the report from its
trace, with no network access. It reuses the recorded expander decisions,
search responses, and page text, so it returns the same statements,
contradictions, and stop reason. The test suite checks this.

## Optional LLM adapters

`research/llm.py` provides `LLMExpander` and `LLMSynthesizer`. Both take a plain
`complete(prompt) -> str` callable. HalfIraLens ships no model and no key.
- The expander falls back to the deterministic one on bad JSON or no usable queries.
- The synthesizer's proposal is rejected if it cites any claim id that does not
  exist. Scoring stays deterministic.
- Source text goes into the prompt inside `<untrusted>` tags, with an instruction
  to treat it as data.

Pass an adapter to `ResearchPlanner(..., expander=..., synthesizer=...)` directly.
The facade does not take one yet.

## Privacy note

Research reads use the static reader, which sends each top URL to the third-party
reader service (`r.jina.ai`). The page URLs leave the machine. Search queries go
to the search engines. The browser engine is not used for reads in research.
