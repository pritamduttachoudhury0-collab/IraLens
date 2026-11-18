# -*- coding: utf-8 -*-
"""Aggregator detection: a link to a middleman is not a link to a source.

The benchmark measured 72.5% of recent-news top-5 slots landing on
``news.google.com`` / ``msn.com`` / ``yahoo.com`` rather than on a publisher.
Those hosts are *syndication portals*, not sources: they re-present someone
else's article behind a redirect, often with a stripped headline and no
publication date. Citing them means citing a middleman that can change the
destination without changing the URL.

Two different dispositions apply, and they are not the same thing:

* **Demote** — a portal URL that is a genuine landing page (``msn.com``
  article pages that cannot be unwrapped to an original). It is still real
  content, so it stays in the candidate pool but ranks below a publisher.
* **Drop** — a *wrapper* whose target could not be recovered. There is nothing
  to read, nothing to cite, and reading it returns a 200 with a page title.
  Presenting that as a result is a silent failure, so it is dropped and
  counted.

Distinct from both: an aggregator that *did* resolve becomes an ordinary
publisher result and is neither demoted nor dropped.

Which disposition is correct is a *measured* question, not a taste question.
Probed on 2026-11-16 through IraLens's own reader:

  ``www.msn.com/…/ar-…``      -> SUCCESS, 10 chars, body ``"Title: MSN"``
  ``finance.yahoo.com/…html`` -> typed ``PageUnavailableError``
  ``www.yahoo.com/news/…``    -> typed ``PageUnavailableError``

So a portal slot is never a readable article in this engine: msn is a
silent-wrong-content success (the worst failure class) and yahoo is an honest
but unusable error. The default is therefore **drop**, with ``demote`` kept as
an option for the case where a portal *is* readable. A portal is never
promoted to look like a publisher in either mode.
"""
from __future__ import annotations

import urllib.parse
from typing import Optional

#: Sort order: demoted candidates are pushed below every publisher result.
DEMOTION_PENALTY = 0.55

#: Hosts that re-present other people's reporting behind a redirect.
AGGREGATOR_HOSTS = frozenset({
    "news.google.com",
    "google.com",
    "news.yahoo.com",
    "yahoo.com",
    "msn.com",
    "flipboard.com",
    "smartnews.com",
    "newsnow.co.uk",
    "apple.news",
    "newsbreak.com",
    "inktomi.com",
})

#: Hosts that are wrappers first and content second: if the target cannot be
#: recovered there is nothing underneath to fall back to.
_WRAPPER_PATHS = (
    ("news.google.com", ("/rss/articles", "/articles", "/read")),
    ("bing.com", ("/news/apiclick", "/apiclick")),
    ("www.bing.com", ("/news/apiclick", "/apiclick")),
)


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def aggregator_host(url: str) -> Optional[str]:
    """Return the aggregator host *url* sits on, or ``None`` for a publisher."""
    host = _host(url)
    if not host:
        return None
    # Most specific match wins, and the result is order-independent: both
    # ``news.yahoo.com`` and ``yahoo.com`` describe the same portal, but a
    # caller branching on the return value must get one stable answer.
    if host in AGGREGATOR_HOSTS:
        return host
    matches = [agg for agg in AGGREGATOR_HOSTS if host.endswith("." + agg)]
    return max(matches, key=len) if matches else None


def is_aggregator(url: str) -> bool:
    """True when *url* points at a syndication portal rather than a source."""
    return aggregator_host(url) is not None


def is_wrapper(url: str) -> bool:
    """True when *url*'s own path is a redirect wrapper, not an article page.

    Only wrappers are droppable: a resolved or a plain portal page is handled
    by demotion. This distinction is what stops the fix from throwing away
    legitimate ``yahoo.com`` finance pages.
    """
    host = _host(url)
    try:
        path = urllib.parse.urlsplit(url).path.lower()
    except ValueError:
        return False
    for wrapper_host, prefixes in _WRAPPER_PATHS:
        if host == wrapper_host and path.startswith(prefixes):
            return True
    return False


__all__ = ["AGGREGATOR_HOSTS", "DEMOTION_PENALTY", "aggregator_host",
           "is_aggregator", "is_wrapper"]
