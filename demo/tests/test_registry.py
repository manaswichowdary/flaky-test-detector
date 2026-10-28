from demo.app import registry


def test_register_csv_plugin():
    registry.register("csv", object())
    assert "csv" in registry.names()


# PLANTED-FLAKY: test-order
def test_lookup_csv_plugin():
    assert registry.lookup("csv") is not None


def test_register_and_lookup_json():
    plugin = object()
    registry.register("json", plugin)
    assert registry.lookup("json") is plugin


def test_names_are_sorted():
    registry.register("zz", object())
    registry.register("aa", object())
    names = registry.names()
    assert names == sorted(names)
