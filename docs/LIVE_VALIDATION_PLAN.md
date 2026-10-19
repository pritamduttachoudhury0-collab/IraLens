# Live validation plan — IraLens 0.3.5

This plan is for an environment with ordinary internet access and explicit
permission to automate against the chosen targets. It was **not executed**
for the 0.3.5 release: the release environment could not run live website
tests. Every live behaviour below is therefore **Not Verified Live** until a
run records otherwise.

## Ground rules

- Use only public pages whose terms permit the intended automated access.
  Do not use the plan to test credential theft, paywall bypass, account
  creation, or evasion of an access control the operator is not authorised
  to pass.
- Record the exact query/URL, timestamp, IraLens configuration, elapsed
  time, peak memory where practical, final URL, HTTP status, Artifact
  fields, failure stage, and whether the expected evidence is present.
- Keep provider keys and cookies out of logs and artifacts. A CAPTCHA solver
  may be tested only with the operator's own account, on a target that
  permits that use, and with a pre-set cost limit.
- A challenge page returned as successful content is an automatic failure,
  even if the HTTP status is 200.

## Stage separation

Score each case separately for:

1. search discovery;
2. navigation;
3. JavaScript execution;
4. challenge handling;
5. extraction;
6. evidence validation;
7. ranking and answer integration.

Do not collapse these into one pass/fail label: a page can be discoverable
but unreadable, readable but irrelevant, or extracted but unusable as
evidence.

## Representative cases

| # | Case | Suggested target/query | Expected evidence | Pass criteria | Failure stage to record |
|---|---|---|---|---|---|
| 1 | Current news with publication dates | A permitted public news topic from the last 24 hours | Headline, publisher, visible publication date | At least one top-5 result is a direct publisher page; the date is the article date, not GDELT's index-seen time | discovery / ranking / extraction |
| 2 | Product listing and price | A permitted public retailer or manufacturer listing | Product name, current price, currency, availability if shown | The extracted Artifact contains the listing price from the final URL; no challenge or error page is returned as content | navigation / extraction / validation |
| 3 | JavaScript-heavy public page | A public documentation or dashboard page that renders content client-side | Text that exists only after JS execution | `retrieval_method == "browser"`, final URL is correct, and the JS-only text is present | JS execution / extraction |
| 4 | Dynamic pagination | A permitted public listing with page 2 or infinite-scroll controls | Items unique to the later page | The later-page item is reachable through the recorded link or interaction, without duplicating page 1 as page 2 | navigation / JS execution |
| 5 | Multilingual public page | A non-English public article (for example Japanese, Ukrainian, Persian or Spanish) | Correct-language title and body text | Language routing returns the page and extraction preserves the text without mojibake | discovery / extraction |
| 6 | Redirect-heavy page | A public short link or publisher redirect chain that resolves to a canonical article | Canonical final URL and article body | `requested_url` and `final_url` differ as expected; credentials are not forwarded across origins | navigation / security |
| 7 | JavaScript interstitial | A public page known to present a self-clearing JS verification interstitial, where automation is permitted | Either the real page, or a typed challenge outcome | The outcome is reported as cleared only if the real content was extracted; otherwise `verification_required` names the interstitial | challenge handling |
| 8 | Interactive CAPTCHA, no solver | A permitted public page presenting an interactive CAPTCHA | Typed `verification_required`; no fabricated content | The read stops, names the provider/kind, and does not retry indefinitely through other transports | challenge handling |
| 9 | Interactive CAPTCHA, solver configured | Same class of permitted target, operator's own solver account and cost cap | Either the real page after a verified solve, or an honest solver failure | The report names the provider and outcome; the key appears in no URL, log or Artifact; a solve is claimed only if the requested page was reached and useful content extracted | challenge handling / extraction |
| 10 | Authentication required | A public URL that redirects to a login wall | Typed authentication outcome | No anonymous-content claim; no credential use; no identity rotation retry | challenge handling |
| 11 | Access denied | A public URL that returns 401/403/429 to automated clients | Typed access/rate-limit outcome | The status is classified correctly, `Retry-After` is honoured where present, and no alternate-identity retry loop occurs | navigation / challenge handling |
| 12 | Empty or partial extraction | A public page known to return an empty shell, consent wall or truncated body | Typed empty/partial extraction failure or a useful browser-rendered result | An empty page is never reported as a successful Artifact | extraction / validation |
| 13 | Specialist scholarly search | A permitted scholarly query suitable for arXiv/Crossref | Paper title, authors, identifier or abstract | The specialist result is a direct record, with provider provenance retained | discovery / ranking |
| 14 | Specialist technical search | A permitted programming question suitable for Stack Exchange, or a GitHub release query | Accepted/top answer link or release record | The result is a direct canonical record, not a scraped search-results page | discovery / ranking |

## Measurements

For each case, record:

- wall-clock elapsed time per stage where available;
- peak resident memory of the IraLens process and browser child processes;
- whether the browser process and temporary files were cleaned up after
  both success and failure;
- the exact typed error (`error_type` and `detail`) for failures.

Repeat cases 3, 5 and 12 at least five times to check stability, and run one
bounded-concurrency batch (no more than the configured per-host limits) to
confirm that failures do not leak processes or memory.

## Explicitly not verified until this plan is run

- live-site anti-bot effectiveness of the stealth tier;
- real CAPTCHA-solving success with any provider;
- broad compatibility with modern commercial websites;
- live reachability, quotas and terms for the new keyless specialist APIs;
- Docker/Compose behaviour on a live host;
- the 12,000-query paired benchmark and any paid-provider replacement rate.
