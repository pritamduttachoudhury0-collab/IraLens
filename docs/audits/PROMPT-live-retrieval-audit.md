# Prompt for the live (internet) session — copy everything below

> **Why this is a prompt and not a task in the repo:** giving an Arena session
> access to this repository disables its internet access. This prompt is written
> to run in a session that has internet but **no** repository access, so it is
> self-contained: install instructions, verbatim queries, rubric, commands, and
> acceptance criteria are all inline. It produces files in its own workspace.

---

install IraLens `https://github.com/pritamduttachoudhury0-collab/IraLens`, then audit its live retrieval quality and report honestly.

## 0. Setup (internet required — do this first)

```bash
python3 -m venv /tmp/iralens-live && . /tmp/iralens-live/bin/activate
pip install --upgrade pip
# IMPORTANT: install from the working branch, NOT main.
# `main` is still at af0af08 and predates the 0.3.1 remediation. Installing
# main would measure code that is already known-broken.
pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git@arena/d0ad621a-iralens"
iralens --version          # expect: iralens 0.3.1
python -c "import iralens, sys; print(iralens.__file__)"
git clone --branch arena/d0ad621a-iralens --depth 1 \
  https://github.com/pritamduttachoudhury0-collab/IraLens.git /tmp/iralens-src
```

Confirm you have internet by running one search before starting. If
`iralens search` returns no results or errors, stop and report that instead of
proceeding — an environment that cannot reach the search providers cannot
measure anything, and a null result is a real finding, not a failure to hide.

**Do not modify the cloned source.** You cannot push it back. All output goes
to files you create in your workspace.

## 1. Re-score the 10 paired audit queries (highest value — do this first)

The previous audit ran these at revision `af0af08` and scored IraLens 2/10
against Arena native search. Those findings are now partly stale: the 0.3.1
remediation fixed extraction flattening and the snippet/documentation defects.
Re-run them so we know what is actually still broken.

Run each verbatim, and compare against your own web search for the same text:

```bash
iralens --json search --limit 5 '<QUERY>' > /tmp/out/P01.json
```

| ID | Query (verbatim) |
|---|---|
| P-01 | `Gumroad pricing fees official creator` |
| P-02 | `Substack vs Ghost pricing for creators 2026` |
| P-03 | `Python 3.13 release date official` |
| P-04 | `creator economy market size 2026 report` |
| P-05 | `Kit ConvertKit pricing 2026 official email marketing` |
| P-06 | `Stripe pricing standard fees 2026` |
| P-07 | `digital product landing page conversion rate benchmark 2026` |
| P-08 | `how to validate demand for a digital product before launch pre-sale` |
| P-09 | `YouTube CPM rates 2026 creator revenue` |
| P-10 | `Gumroad merchant of record sales tax January 2025 announcement` |

**Rubric (use exactly this so results are comparable to the earlier audit):**
`PASS` if **≥3 of 5** results are answer-bearing for the query intent — they
directly contain, or are the canonical page containing, the fact/table/answer
the query asks for. `FAIL` otherwise. Judge from titles and snippets, which is
the same information a consuming product would have.

For each query record: PASS/FAIL, how many of 5 were answer-bearing, the
irrelevancies returned (quote them), `date_status` counts across the 5, and
whether a `degraded` marker was present.

## 2. Measure retrieval quality properly (the principal open gap)

The 10-query set is a targeted sample, not a corpus. Build a **product-evidence
corpus of at least 200 queries** in the P-01…P-10 style — competitor pricing,
market size with conflicting estimates, dated events, benchmark figures, and
methodology/advice — spread across at least 6 categories so no single query
shape dominates. Author them yourself; keep them realistic for a
decision-simulation product.

Run every query (`iralens --json search --limit 5`) and score each one:
- Did at least one of the top 5 contain answer-bearing evidence?
- Count junk slots: Wikipedia disambiguation pages, login/signup pages,
  navigation or homepages, and topically unrelated entities.

**Acceptance to report against:** ≥90% of queries return at least one
answer-bearing result in the top 5, and junk fillers occupy ≤5% of top-5 slots.

Report the **exact denominator** — how many queries you actually ran. If you
run 200, say 200. If you run 60, say 60. **Do not extrapolate** from a smaller
run to a larger claim, and do not describe 60 queries as "the corpus" if the
corpus was specified as 200.

## 3. The 12,000-query benchmark

The original spec is 1,000 queries × 12 categories = 12,000, with a ≥98%
(11,760/12,000) bar. It has never been run: **0 queries have ever been
submitted, across all prior sessions.**

Run as much of it as you can within your time and rate-limit budget, and then
report honestly:
- exactly how many queries you submitted (the denominator),
- the pass count,
- the resulting percentage,
- and an explicit statement of whether the 12,000 bar was met.

If you cannot complete 12,000, say so plainly. A measured 500 with a stated
denominator is far more useful than an implied 12,000. **Never report the
≥98% bar as met, partially met, or on track unless the full corpus was run.**

Be polite to upstream providers: pace requests, and stop if you are being rate
limited. Do not rotate endpoints, user agents, or proxies to work around a
rate limit or a block — that is explicitly out of scope, and being blocked is
itself a result worth recording.

## 4. Validate the new instrumentation against live traffic

Recent work added three things that have only been tested offline. Confirm they
fire on real data, using `search-api` (which exposes full engine outcomes):

```bash
iralens search-api '<QUERY>' --limit 5 --json
```

1. **Degradation.** Does `degradation.degraded` become true when an engine is
   circuit-open or fails? Do `engines_impaired` and `reasons` name the right
   engine? Does the filler case appear — an entity-matching index (usually
   wikipedia) filling slots after a primary engine trips?
2. **Conflicts.** Query P-10's subject (Gumroad merchant-of-record status,
   where sources genuinely disagree). Does a conflict record appear with both
   sides, both sources, and both dates retained?
3. **Sufficiency.** Does the label come back `insufficient` for single-domain
   result sets, `undated` for multi-source undated sets, and `sourced` for
   dated multi-source sets?

Also spot-check relevance gating: find at least one case where a recently
dated but irrelevant result appears, and confirm `freshness_gated` is true and
that it did not rank above relevant undated material.

Report each check as **confirmed / not reproduced / could not test**, with the
evidence. Do not mark anything confirmed without a concrete example.

## 5. Output

Write these files, and tell me their paths at the end:

1. `LIVE_RETRIEVAL_AUDIT.md` — findings, with this structure:
   - Environment and installed revision (commit hash from the branch)
   - §1 paired re-score table (10 rows) vs. the previous 2/10
   - §2 corpus results with the exact denominator and junk-slot percentage
   - §3 benchmark: exact submissions, passes, percentage, and whether the
     12,000 bar was met (a yes/no, not a narrative)
   - §4 instrumentation checks, each confirmed / not reproduced / could not test
   - A closing verdict: **is IraLens ready to replace a paid search API for a
     product that needs sourced, dated, conflict-aware evidence?** Yes / no /
     only in shadow mode, with the specific blockers.
2. `results.jsonl` — one JSON object per query: query, category, top-5 URLs,
   titles, snippets, `date_status`, `degraded`, `sufficiency`, and your
   answer-bearing verdict.
3. `queries.jsonl` — the corpus you authored, with categories.

## Rules

- **Never fabricate a result.** If a query fails, record the failure.
- **Never report a percentage without its denominator.**
- **Never claim the 12,000-query bar was met** unless 12,000 were run.
- Separate what you observed from what you inferred. Label every inference.
- If you find something that contradicts this prompt's assumptions, say so —
  the assumptions are from an earlier offline session and may be wrong.
- Do not attempt to bypass, solve, or work around any CAPTCHA, verification
  page, rate limit, or block. Record it and move on.
- Do not commit or push to the repository. Save output as workspace files.

If after setup you discover you have no usable internet, stop immediately and
say so. That is a valid and important result — it means the live re-score has
not been performed, and the previous audit's 2/10 stands unrefuted.
