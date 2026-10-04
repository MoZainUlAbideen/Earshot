"""Smoke test: does the package install and import at all?"""

import earshot


def test_package_imports():
    assert earshot.__name__ == "earshot"


def test_entry_point_is_callable():
    assert callable(earshot.main)
