import pytest

from demo.app import registry


@pytest.fixture
def clean_registry():
    saved = dict(registry._PLUGINS)
    registry._PLUGINS.clear()
    yield registry
    registry._PLUGINS.clear()
    registry._PLUGINS.update(saved)


# FIXES: test-order (demo/tests/test_registry.py::test_lookup_csv_plugin)
def test_lookup_csv_plugin(clean_registry):
    """ROOT_CAUSE
    Cause: the test looked up the "csv" plugin but relied on test_register_csv_plugin to have
    registered it earlier in the same session.
    Symptom: KeyError: 'csv' whenever the suite ran in a different order or the test ran alone.
    Fix: each test arranges its own state. It registers the plugin it needs, on a registry
    the fixture empties before and restores after.
    """
    plugin = object()
    clean_registry.register("csv", plugin)
    assert clean_registry.lookup("csv") is plugin
