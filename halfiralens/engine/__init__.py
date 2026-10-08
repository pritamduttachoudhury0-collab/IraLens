# -*- coding: utf-8 -*-
"""Engine package — internal browser-engine plumbing."""

from .native import BrowserEngine, classify_engine_error

__all__ = ["BrowserEngine", "classify_engine_error"]
