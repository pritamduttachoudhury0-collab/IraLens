#!/usr/bin/env python3
"""Offline structural checks on the frozen query holdout (not search quality).

This intentionally does not call external engines. It reports only whether
queries yield lexical terms and the deterministic intent distribution; it
cannot measure top-5 relevance, so no proxy P@5 is produced.
"""
from __future__ import annotations
import collections
import json
from pathlib import Path
from iralens.search.intent import classify_query
from iralens.search.language import detect_language
from iralens.search.querytext import term_set

FILE = Path(__file__).with_name("queries_holdout.jsonl")
rows = [json.loads(line) for line in FILE.read_text(encoding="utf-8").splitlines() if line]
assert len(rows) == 300 and len({r['query_id'] for r in rows}) == 300
empty = [r['query_id'] for r in rows if not term_set(r['query'])]
by_cat = collections.Counter(r['category'] for r in rows)
intents = collections.Counter(classify_query(r['query']).answer_type for r in rows)
scripts = collections.Counter(detect_language(r['query']).script for r in rows)
print(f"queries={len(rows)} ids_unique={len(set(r['query_id'] for r in rows))}")
print(f"categories={dict(sorted(by_cat.items()))}")
print(f"empty_term_sets={len(empty)} ids={','.join(empty) if empty else 'none'}")
print(f"intent_counts={dict(sorted(intents.items()))}")
print(f"script_counts={dict(sorted(scripts.items()))}")
print("top-5 quality / lexical proxy / human relevance: NOT MEASURED (no live search or human grading)")
