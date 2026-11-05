# How IraLens Works

This page explains IraLens in plain words. You do not need to be a programmer
to follow it. If you know what a web search and a web browser are, you know
enough to start.

---

## 1. What is IraLens?

IraLens is a tool that lets a computer program use the Internet the way a
person does. It can:

- **Search** the web (like typing into a search engine).
- **Read** a web page and turn it into clean text.
- **Browse** like a real browser when a page needs it (clicking, waiting for
  things to load).
- **Research** a question over several steps and tell you where each fact came
  from.

It does all of this through **one** simple interface. A program does not need
to know which search engine or which browser is doing the work underneath. It
just asks IraLens, and IraLens handles the messy parts.

Think of IraLens as a careful research assistant. It looks things up, reads the
pages, and tells you what it found — and, just as importantly, it tells you
when it could **not** find or confirm something. It does not make things up.

---

## 2. The big picture

```
You ask a question
        |
        v
   [ IraLens ]
        |
        +--> Search engines  -> find candidate pages
        +--> Read pages      -> get the actual text
        +--> Browser         -> for pages that need a real browser
        +--> Special sources -> GitHub, Wikipedia, news feeds, and more
        |
        v
   A clean answer, with sources, and honest notes about what is uncertain
```

Everything that comes back is wrapped in the same kind of package (called an
**Artifact**). So whether the answer came from a search, a page read, or a
special source, your program handles it the same way.

---

## 3. How search works

When you ask IraLens to search, it does roughly this:

1. **Understand the question.** Is it asking for news? A definition? A how-to?
   A scientific paper? The kind of question decides which helpers to use.
2. **Ask several search engines.** IraLens uses free, public search engines
   (no paid key required). It does not rely on just one, so if one is down or
   blocks the request, others can still answer.
3. **Collect and clean the results.** It removes duplicates (the same page
   found by two engines counts once) and drops unsafe links.
4. **Rank them.** Each result gets a score built from clear factors:
   - How well it matches your question (**relevance**).
   - How trustworthy the site looks (**authority**).
   - How recent it is (**freshness**).
   - How many engines agreed on it (**agreement**).

   You can see this score breakdown for every result. Nothing is a hidden
   black box.
5. **Report honestly.** If nothing matched, or every engine failed, or only
   some engines worked, IraLens says so clearly. "No results" and "the engines
   are down" are different things, and it never confuses them.

---

## 4. How recent-news search works

News was historically the weakest part of IraLens, so it got special care.
Here is how a news question is handled now:

1. **Notice that it is a news question.** Words like *latest*, *today*,
   *breaking*, or *this week* tell IraLens you want something recent. If you
   say "last 24 hours" or "past 7 days", IraLens turns that into a real date
   range instead of ignoring it.

2. **Look in news-specific places.** Besides the normal search engines,
   IraLens checks free news sources:
   - **News RSS feeds** (Bing News and Google News). These are simple lists of
     recent articles that include the publisher's own publication date and a
     link to the article.
   - **The publishers' own feeds**, read directly. Global indexes pick up big
     national outlets quickly and small regional ones late or never, so IraLens
     also keeps a curated list of publisher feeds and reads the ones that fit
     the question. Ask about Assam and it reads Assam outlets; ask about
     Northeast India and it reads Northeast outlets.
   - **GDELT**, a large public index of world news.

3. **Get the real publication date.** This is the important part. IraLens is
   very careful about dates, because there are four different kinds and they
   are easy to mix up:

   | Date kind | What it means |
   |---|---|
   | **Publication date** | When the article was first published. This is what "how recent is the news" really means. |
   | **Updated date** | When the article was last edited. Not the same as publication. |
   | **Index-discovery date** | When a search index first noticed the page. Not the publication date. |
   | **Retrieval date** | When IraLens fetched the page. Definitely not the publication date. |

   IraLens keeps these four apart. If it cannot establish a real publication
   date, it says the date is **unknown**. It never guesses or substitutes one
   of the other dates and pretends it is the publication date.

4. **Actually read the article.** For the top results, IraLens opens the
   publisher's page and checks the date against the page's own metadata, and
   checks that the article really is about your topic. Some news sites only
   show their content (and their date metadata) after JavaScript runs; for
   those, IraLens uses its browser engine to render the page and reads the date
   from the rendered page, by the same rules as a normal page. If the page
   cannot be read, the result is marked **unverified** — it is not silently
   presented as confirmed fresh news.

5. **Rank with recency in mind.** For news, a recent article is worth more
   than an old one, and an established or original publisher is worth a little
   more than a random repost. But a fresh article that is off-topic can never
   beat a relevant one just by being new. Relevance comes first.

6. **Remove copies of the same story.** The same wire story often appears on
   ten sites. IraLens groups those copies and prefers the original publisher.

7. **Tell you what it knows.** The answer includes a small report: how many
   results have a real publication date, how many were actually checked
   against the page, and what time window was used. So you can see *why* an
   answer is (or is not) fresh.

8. **Group the results into stories.** Ten links is not an answer. Eight of
   them are often the same wire report, and two are genuinely different
   developments. IraLens groups them, so the answer says "one story, reported
   by eight outlets" rather than listing eight near-identical links. It picks
   the strongest publisher's copy as the story's main link. Headlines are
   compared as *stories*, so "floods displace thousands" and "thousands
   displaced" are recognised as one event, while "RBI holds rates" and "RBI
   cuts rates" stay separate.

9. **Build a timeline, and say what it is built from.** For a developing
   event, IraLens reports the first report and the latest update. Only real
   publication dates go on that timeline. If some stories have no date, it
   says how many were left out instead of quietly guessing.

10. **Show what is spreading.** A trend signal says which stories the most
    independent publishers are picking up. It is a simple, visible calculation
    over counts and recency — not an official "trending" list and not a
    probability — and every signal carries the counts it came from so you can
    check it.

**Honest limit:** if IraLens cannot reach the news sources (for example, the
computer has no Internet access to them), it says the news could not be
verified. It does not invent headlines, links, or dates.

---

## 4b. How archive ("what did they report in 2019?") questions work

This is a different problem from recent news, and it used to have no answer at
all. Every source in step 2 above holds a *rolling recent window*. Ask GDELT
or a news feed about 2019 and it returns nothing — not because nothing was
written, but because it no longer keeps it.

So an archive question takes a different route:

1. **Notice the older time span.** "in 2019", "between 2019 and 2021",
   "from 2018-03 to 2018-09", "since 2018-03-01" become a real date range.
   A span ending in the current year is *not* an archive question — "in 2026"
   is this year. And a bare word like "history" is not enough: "history of the
   roman empire" is an encyclopedia question, not a news request.

2. **Read publisher sitemaps.** A sitemap is the publisher's own list of its
   pages, and it still lists the old articles. IraLens reads `robots.txt`
   first for a declared sitemap, then falls back to the usual paths.

3. **Keep the two sitemap dates apart.** A sitemap can carry
   `news:publication_date` (when the article was published) and `lastmod`
   (when the page was last changed). These are different. A page edited in
   2026 is not a page published in 2026, so `lastmod` is never promoted to a
   publication date.

4. **Do not punish the answer for being old.** For "climate policy in 2019", a
   2019 article is exactly right. The normal news ranking would decay it
   toward zero and rank the correct answer last, so for archive questions
   freshness is held flat and the date range does the selecting.

The crawl is deliberately small: a few publishers, a handful of requests each,
a size cap, and a pause between requests to the same host. If the sitemaps
cannot be read, IraLens reports that — it does not report "no historical
coverage exists".

---

## 5. How reading a page works

When IraLens reads a page:

1. It first tries a **fast, simple read** (just fetch the page and extract the
   text). This works for most pages and is quick.
2. If the page needs a real browser (lots of JavaScript, content that loads
   later), it falls back to the **browser engine**.
3. It checks for problems: a login wall, a cookie-consent wall, a CAPTCHA, an
   empty shell, or a "page not found" that pretends to be a normal page. These
   are reported as specific, typed problems — not silently treated as a
   successful read.

A key rule: **a page returning "HTTP 200 OK" does not automatically mean the
read succeeded.** A CAPTCHA page also returns 200. IraLens looks at what it
actually got, not just the status code.

IraLens does **not** try to sneak past CAPTCHAs or logins. If a page requires
verification, it reports that verification is required and stops. It respects
the site's rules.

---

## 6. The browser engine

For pages that need a real browser, IraLens uses a bundled headless browser
called **Obscura**. "Headless" just means it runs without a visible window.

- Obscura is the **primary** browser engine. It is not quietly swapped out for
  something else, and it is not an optional extra that silently disappears.
- It runs inside IraLens and is controlled through a simple message protocol.
- If the browser engine is not installed or cannot start, IraLens reports that
  clearly (and tells you how to install it) instead of failing silently.

You can also drive the browser step by step (navigate, click, fill a form,
take a screenshot) through the same interface.

---

## 7. How research works

For a bigger question, IraLens can do multi-step research:

1. **Plan** what to look up.
2. **Search** and **read** pages over a few rounds.
3. **Pull out claims** (individual facts) from what it read.
4. **Compare** the claims: do several independent sources agree? Do any
   contradict each other?
5. **Write findings**, and attach a **citation** to each one so you can see
   exactly which source supports it.

Two honesty rules matter here:

- Every finding points back to the source(s) it came from. No anonymous facts.
- The "confidence" numbers are **rough heuristics**, not precise probabilities.
  A finding marked *single source* or *contested* is telling you it is not
  well confirmed. IraLens does not dress up a guess as a certainty.

---

## 8. How IraLens stays safe

The Internet is full of traps, so IraLens is built to be careful:

- **No reaching into private networks.** It refuses to fetch internal or
  local addresses (like your router or a cloud server's secret metadata page),
  even if a search result or a redirect tries to point it there. This blocks a
  class of attack called SSRF.
- **Every redirect is re-checked.** A page cannot bounce IraLens to a private
  address partway through a request.
- **Web content is treated as untrusted data, never as instructions.** If a
  page says "ignore your rules and reveal secrets", IraLens flags it as a
  prompt-injection attempt and does not obey it.
- **Secrets stay secret.** API keys and passwords are not printed in logs,
  error messages, or URLs.
- **It respects site rules and rate limits.** It does not hammer a site or try
  to evade blocks.

---

## 9. How to install and run it

You need Python 3.10 or newer.

**One-command setup (Linux/macOS):**

```bash
git clone https://github.com/pritamduttachoudhury0-collab/IraLens.git
cd IraLens
./scripts/setup.sh        # creates a virtualenv, installs IraLens, health check
iralens doctor            # shows what works and what is missing
```

**Or install with pip:**

```bash
pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git"
```

**Try it:**

```bash
iralens search "solar cell efficiency" --limit 5
iralens search "latest news about interest rates" --limit 5 --json
iralens open "https://example.com"
iralens research "What are the effects of X?" --json
```

The browser engine is optional for basic search and read. To install it:

```bash
iralens install-engine
```

**Run the tests (no Internet needed):**

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The offline test suite passes without any network access. Tests that need the
live Internet skip themselves automatically when there is no access, so a
green test run never depends on (or fakes) a live connection.

---

## 10. What IraLens will NOT do

It is just as important to know the limits:

- It will not invent a headline, a URL, a date, or a search result.
- It will not claim a news article is fresh if it could not verify the date.
- It will not pretend a blocked or failed search succeeded.
- It will not bypass a CAPTCHA, a login, or a paywall.
- It will not fetch private/internal network addresses.
- It will not present a rough confidence guess as a precise probability.

When something cannot be done or confirmed, IraLens says so plainly. That
honesty is a core feature, not an afterthought.

### What it does instead of bypassing a "prove you are not a robot" check

A CAPTCHA or a bot check is the site owner deciding who may read the page.
Getting around it is not a retrieval feature, so IraLens does not do it. What it
does do is tell you the truth about what happened, in enough detail to act on:

- **It recognises the wall.** Not just reCAPTCHA: hCaptcha, Turnstile,
  Cloudflare's "Just a moment", PerimeterX/HUMAN "Press & Hold", DataDome,
  Imperva, Kasada, Sucuri, AWS WAF, and the plain-wording pages ("prove you are
  not a robot", "verify you are a human", "pardon our interruption").
- **It tells the kinds apart.** An interactive puzzle a person could solve, a
  script-based browser check, a login wall, and a flat refusal are four
  different situations with four different answers. Retrying does not fix any
  of them, so IraLens does not pretend it might.
- **It reports, in a typed error.** `verification_required`, with the provider
  and the exact marker that fired — not a generic "fetch failed", and not the
  wall's own text handed back as if it were the article.
- **It moves on to another source.** The other publishers in the same query
  still get read, so one site's wall does not empty the answer.
- **It does not censor articles *about* bot checks.** A page that merely quotes
  "prove you are not a robot" is still an article. Only a confident detection
  stops a read.

There is a separate, opt-in integration that hands a puzzle to a solving
service using **the operator's own account and API key**, off by default, and
it reports exactly what it did. That is the operator choosing to answer a
challenge on their own behalf; it is not IraLens defeating one.

---

## 11. Where to learn more

- `README.md` — overview and quick start.
- `ARCHITECTURE.md` — how the pieces fit together (more technical).
- `docs/search.md`, `docs/research.md`, `docs/security.md` — deeper dives.
- `KNOWN_LIMITATIONS.md` — measured limits and provider caveats.
- `BENCHMARKS.md` — how quality is measured, with real numbers.
- `iralens doctor` — a live health check of your installation.
