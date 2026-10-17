# -*- coding: utf-8 -*-
"""Deterministic query intent + jurisdiction classification (defect D2).

The benchmark showed procedure/statistic/error questions being answered by
encyclopaedia hubs because nothing distinguished *what kind of answer* was
asked for, or *where* it should come from ("solar rooftop net metering
Assam" returned a US utility). This module is a small lexical classifier —
no model, no network — that produces:

- ``answer_type``: procedure | statistic | current_event | error |
  comparison | definition | entity_lookup | general
- jurisdiction preferences: official-domain suffixes/domains implied by a
  place named in the query (general gazetteer, not benchmark queries).

It never hard-filters; it only boosts/demotes in ranking and adds one
site-biased reformulation variant, so a wrong classification degrades
gracefully to the old behaviour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Tuple

_ERROR_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|not working|cannot|can't|unable to|"
    r"fix|debug|crash|segfault|timeout after)\b|\bERR_[A-Z_]+|\bE\d{3,}\b", re.IGNORECASE)
_PROCEDURE_RE = re.compile(
    r"^(how (to|do|can|should) )|\b(register|registration|apply|application|licence|license|"
    r"certificate|renew|renewal|claim|eligibility|documents required|process|steps|form|"
    r"enrol|enroll|admission|complaint|appeal|refund|withdraw|transfer|mutation|permit)\b",
    re.IGNORECASE)
_STATISTIC_RE = re.compile(
    r"\b(rate|growth|share|revenue|sales|production|capacity|statistics|forecast|"
    r"market size|inflation|gdp|exports|imports|issuance|shipments|leasing|vacancy|"
    r"penetration|aUM|premium|deficit|reserves|turnover|index of)\b", re.IGNORECASE)
_CURRENT_RE = re.compile(r"\b(latest|recent|today|news|winner|results?|election|summit|launch)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\b(vs\.?|versus|compare|comparison|difference between|better|which is cheaper)\b", re.IGNORECASE)
_DEFINITION_RE = re.compile(
    r"^(what (is|does|are)|meaning of|define |definition of|who (is|was|wrote|invented|discovered|painted)|"
    r"capital of|currency of|national \w+ of|how many|how much|when (was|did)|where is)",
    re.IGNORECASE)
_YEAR_RE = re.compile(r"\b20\d{2}\b")

_INDIAN_STATES = (
    "andhra pradesh", "arunachal pradesh", "Assam", "Bihar", "chhattisgarh", "Goa", "Gujarat",
    "haryana", "himachal pradesh", "jharkhand", "karnataka", "Kerala", "madhya pradesh",
    "maharashtra", "manipur", "meghalaya", "mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "sikkim", "Tamil Nadu", "telangana", "Tripura", "Uttar Pradesh", "uttarakhand", "West Bengal",
    "Delhi", "jammu and kashmir", "Ladakh", "puducherry",
)

# place phrase -> (label, preferred official suffixes, specific domains)
_COUNTRIES = {
    "india": ("India", (".gov.in", ".nic.in", ".in"), ()),
    "united states": ("United States", (".gov",), ()), "usa": ("United States", (".gov",), ()),
    "united kingdom": ("United Kingdom", (".gov.uk",), ()), "uk": ("United Kingdom", (".gov.uk",), ()),
    "australia": ("Australia", (".gov.au",), ()), "Canada": ("Canada", (".gc.ca", ".canada.ca"), ()),
    "new zealand": ("New Zealand", (".govt.nz",), ()), "Singapore": ("Singapore", (".gov.sg",), ()),
    "ireland": ("Ireland", (".gov.ie",), ()), "south africa": ("South Africa", (".gov.za",), ()),
    "uae": ("UAE", (".gov.ae",), ()), "Germany": ("Germany", (".de",), ()),
    "France": ("France", (".gouv.fr",), ()), "Japan": ("Japan", (".go.jp",), ()),
}


@dataclass(frozen=True)
class QueryIntent:
    answer_type: str = "general"
    jurisdiction_label: str = ""
    jurisdiction_domains: Tuple[str, ...] = ()
    preferred_suffixes: Tuple[str, ...] = field(default_factory=tuple)


def classify_query(question: str) -> QueryIntent:
    text = " ".join((question or "").split())
    low = text.lower()
    label, suffixes, domains = "", (), ()
    for state in _INDIAN_STATES:
        if state in low:
            label = state.title()
            slug = state.replace(" ", "")
            suffixes = (".gov.in", ".nic.in")
            domains = (f"{slug}.gov.in", "gov.in")
            break
    if not label:
        for phrase, (lab, suf, dom) in _COUNTRIES.items():
            if re.search(rf"\b{re.escape(phrase)}\b", low):
                label, suffixes, domains = lab, suf, dom
                break
    if not label and re.search(r"\bindian\b", low):
        label, suffixes = "India", (".gov.in", ".nic.in", ".in")

    if _ERROR_RE.search(text):
        kind = "error"
    elif _COMPARISON_RE.search(low):
        kind = "comparison"
    elif _PROCEDURE_RE.search(low):
        kind = "procedure"
    elif _CURRENT_RE.search(low) or _YEAR_RE.search(low):
        kind = "current_event" if _CURRENT_RE.search(low) else "statistic"
    elif _STATISTIC_RE.search(low):
        kind = "statistic"
    elif _DEFINITION_RE.search(low):
        kind = "definition" if not low.startswith("how many") else "entity_lookup"
    else:
        kind = "general"
    # A dated statistic ("GDP growth 2026") is a statistic, not news.
    if kind == "current_event" and _STATISTIC_RE.search(low):
        kind = "statistic"
    return QueryIntent(answer_type=kind, jurisdiction_label=label,
                       jurisdiction_domains=tuple(domains), preferred_suffixes=tuple(suffixes))


def intent_multiplier(url: str, intent: QueryIntent) -> float:
    """Ranking multiplier for a URL under an intent (1.0 = neutral)."""
    from .urls import domain_of
    host = domain_of(url or "")
    if not host:
        return 1.0
    mult = 1.0
    if intent.jurisdiction_domains and any(
            host == d or host.endswith("." + d) for d in intent.jurisdiction_domains):
        mult *= 1.30
    elif intent.preferred_suffixes and any(host.endswith(s) for s in intent.preferred_suffixes):
        mult *= 1.20
    kind = intent.answer_type
    if kind == "procedure" and (".gov" in host or host.endswith(".nic.in")):
        mult *= 1.20
    elif kind == "error" and (host in ("stackoverflow.com", "github.com")
                              or host.startswith(("docs.", "developer."))):
        mult *= 1.20
    elif kind == "statistic" and (".gov" in host or "statistics" in host or host.startswith("data.")):
        mult *= 1.15
    return mult
