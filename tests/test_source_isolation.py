# -*- coding: utf-8 -*-
"""The source registry is shared: one test must not change it for the next.

This is the regression that broke CI on 2026-10-18. A de-duplication test
installed a fake search engine on the module-level `web-search` source
*in place*, and a later live test then ran against that fake: it "found"
https://a.example/shared, tried to open it, and failed on a host that does
not exist. The suite passed locally only because the live tests skip
without a network, and CI runs them.
"""


def test_a_source_object_is_put_back_after_a_test_mutates_it(pristine_sources):
    from iralens.sources import get_source

    source = get_source("web-search")
    before = source._engine

    source._engine = "a fake engine"
    assert get_source("web-search")._engine == "a fake engine"

    pristine_sources.restore()
    assert get_source("web-search")._engine is before


def test_the_registry_carries_no_fake_engine_between_tests():
    from iralens.sources import get_source

    engine = get_source("web-search")._engine
    assert getattr(engine, "name", None) != "fake", \
        "the registry still carries a fake engine from another test"
