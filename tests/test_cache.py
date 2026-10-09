"""Disk cache: TTL, cacheability, namespaces, corruption handling, key separation."""

import json

from iralens.cache import ResponseCache


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_put_then_get_returns_value_and_age(tmp_path):
    clock = Clock()
    c = ResponseCache(tmp_path, clock=clock)
    assert c.put("search", {"q": "a"}, {"x": 1})
    clock.now += 30
    value, age = c.get("search", {"q": "a"}, ttl_seconds=60)
    assert value == {"x": 1} and age == 30


def test_expired_entries_are_none_and_removed(tmp_path):
    clock = Clock()
    c = ResponseCache(tmp_path, clock=clock)
    c.put("search", {"q": "a"}, {"x": 1})
    clock.now += 61
    assert c.get("search", {"q": "a"}, ttl_seconds=60) is None
    assert list(tmp_path.glob("*.json")) == []


def test_uncacheable_writes_are_refused(tmp_path):
    c = ResponseCache(tmp_path, clock=Clock())
    assert c.put("search", {"q": "a"}, {"x": 1}, cacheable=False) is False
    assert c.get("search", {"q": "a"}, 60) is None


def test_disabled_cache_never_reads_or_writes(tmp_path):
    c = ResponseCache(tmp_path, enabled=False, clock=Clock())
    assert c.put("search", {"q": "a"}, {"x": 1}) is False
    assert c.get("search", {"q": "a"}, 60) is None


def test_keys_separate_namespace_and_payload_and_ignore_dict_order(tmp_path):
    k1 = ResponseCache.key("search", {"a": 1, "b": 2})
    k2 = ResponseCache.key("search", {"b": 2, "a": 1})
    assert k1 == k2
    assert ResponseCache.key("page", {"a": 1, "b": 2}) != k1
    assert ResponseCache.key("search", {"a": 2, "b": 2}) != k1


def test_corrupt_entry_is_a_miss_not_an_error(tmp_path):
    c = ResponseCache(tmp_path, clock=Clock())
    c.put("search", {"q": "a"}, {"x": 1})
    path = tmp_path / f"{ResponseCache.key('search', {'q': 'a'})}.json"
    path.write_text("{not json", encoding="utf-8")
    assert c.get("search", {"q": "a"}, 60) is None


def test_invalidate_by_namespace_only(tmp_path):
    c = ResponseCache(tmp_path, clock=Clock())
    c.put("search", {"q": "a"}, 1)
    c.put("search", {"q": "b"}, 2)
    c.put("page", {"url": "x"}, 3)
    assert c.invalidate("search") == 2
    assert c.get("page", {"url": "x"}, 60) is not None
    assert c.invalidate() == 1


def test_stored_files_are_plain_json(tmp_path):
    c = ResponseCache(tmp_path, clock=Clock())
    c.put("search", {"q": "a"}, {"title": "héllo"})
    raw = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert json.loads(raw)["value"] == {"title": "héllo"}
