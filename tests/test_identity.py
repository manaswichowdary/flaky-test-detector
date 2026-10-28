from datetime import datetime, timedelta

from sqlalchemy import select

from flakydetect import db
from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings
from flakydetect.identity import IdentityResolver, function_name, load_alias_map, make_resolver
from flakydetect.junit import PASSED, TestResult
from flakydetect.pytest_plugin import fingerprint_source

T0 = datetime(2026, 1, 1)


def r(test_id, fp=None):
    props = {"fingerprint": fp} if fp else {}
    return TestResult(test_id=test_id, classname="", name=test_id, status=PASSED, properties=props)


class Repo:
    def __init__(self, engine, resolver):
        self.engine, self.resolver, self.n = engine, resolver, 0

    def run(self, *results):
        self.n += 1
        meta = RunMeta(run_id=str(self.n), sha="s", completed_at=T0 + timedelta(hours=self.n))
        ingest(self.engine, "o/r", meta, list(results), resolver=self.resolver)

    def tests(self):
        with self.engine.connect() as conn:
            return {row.test_id: row.id for row in conn.execute(select(db.tests))}

    def history(self, test_pk):
        with self.engine.connect() as conn:
            return conn.execute(
                select(db.test_results.c.raw_test_id).where(db.test_results.c.test_pk == test_pk)
            ).scalars().all()


def test_moved_file_keeps_history(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    repo.run(r("tests.test_cart::test_total"), r("tests.test_cart::test_add"))
    repo.run(r("tests.shop.test_cart::test_total"), r("tests.shop.test_cart::test_add"))
    tests = repo.tests()
    assert set(tests) == {"tests.shop.test_cart::test_total", "tests.shop.test_cart::test_add"}
    assert repo.history(tests["tests.shop.test_cart::test_total"]) == [
        "tests.test_cart::test_total", "tests.shop.test_cart::test_total"]


def test_renamed_function_with_same_body_is_linked(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    repo.run(r("m::test_old_name", fp="f1"), r("m::test_other", fp="f2"))
    repo.run(r("m::test_new_name", fp="f1"), r("m::test_other", fp="f2"))
    assert set(repo.tests()) == {"m::test_new_name", "m::test_other"}


def test_old_id_reappearing_maps_to_same_test(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    repo.run(r("a::test_x"))
    repo.run(r("b::test_x"))
    repo.run(r("a::test_x"))  # an old branch still uses the old path
    assert list(repo.tests()) == ["b::test_x"]


def test_explicit_map_overrides_heuristic(engine):
    # Two disappeared candidates share the name, so the heuristic would refuse to link;
    # the explicit map decides.
    repo = Repo(engine, IdentityResolver("heuristic", {"new::test_x": "old2::test_x"}))
    repo.run(r("old1::test_x"), r("old2::test_x"))
    repo.run(r("new::test_x"))
    tests = repo.tests()
    assert "new::test_x" in tests and "old1::test_x" in tests and "old2::test_x" not in tests


def test_map_strategy_does_not_guess(engine):
    repo = Repo(engine, IdentityResolver("map", {}))
    repo.run(r("a::test_x"))
    repo.run(r("b::test_x"))
    assert set(repo.tests()) == {"a::test_x", "b::test_x"}


def test_none_strategy_keeps_ids_separate(engine):
    repo = Repo(engine, IdentityResolver("none", {"b::test_x": "a::test_x"}))
    repo.run(r("a::test_x", fp="f"))
    repo.run(r("b::test_x", fp="f"))
    assert set(repo.tests()) == {"a::test_x", "b::test_x"}


def test_unrelated_tests_never_merged(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    # A new test while the old one still runs: not a rename.
    repo.run(r("a::test_x"))
    repo.run(r("a::test_x"), r("b::test_x"))
    assert set(repo.tests()) == {"a::test_x", "b::test_x"}


def test_same_name_different_body_not_merged(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    repo.run(r("a::test_init", fp="f1"))
    repo.run(r("b::test_init", fp="f2"))
    assert set(repo.tests()) == {"a::test_init", "b::test_init"}


def test_ambiguous_name_not_merged(engine):
    repo = Repo(engine, IdentityResolver("heuristic"))
    repo.run(r("a::test_init"), r("b::test_init"))
    repo.run(r("c::test_init"))
    assert "c::test_init" in repo.tests() and len(repo.tests()) == 3


def test_long_gone_test_not_linked(engine):
    repo = Repo(engine, IdentityResolver("heuristic", window_runs=2))
    repo.run(r("a::test_x"))
    for _ in range(3):
        repo.run(r("other::test_y"))
    repo.run(r("b::test_x"))
    assert {"a::test_x", "b::test_x"} <= set(repo.tests())


def test_function_name():
    assert function_name("pkg.mod.Cls::test_a[1-2]") == "test_a[1-2]"
    assert function_name("org.example.CacheTest::evictsOldest()") == "evictsOldest"


def test_load_alias_map(tmp_path):
    f = tmp_path / "test_aliases.toml"
    f.write_text('[aliases]\n"new::t" = "old::t"\n')
    assert load_alias_map(f) == {"new::t": "old::t"}
    assert load_alias_map(tmp_path / "missing.toml") == {}


def test_make_resolver_reads_settings(tmp_path):
    f = tmp_path / "a.toml"
    f.write_text('[aliases]\n"n" = "o"\n')
    res = make_resolver(Settings(rename_strategy="map", aliases_file=str(f)))
    assert res.strategy == "map" and res.alias_map == {"n": "o"}


def test_fingerprint_ignores_name_docstring_and_comments():
    a = 'def test_a():\n    """doc"""\n    x = 1  # c\n    assert x == 1\n'
    b = "def test_b():\n    x = 1\n    assert x == 1\n"
    c = "def test_a():\n    x = 2\n    assert x == 2\n"
    assert fingerprint_source(a) == fingerprint_source(b)
    assert fingerprint_source(a) != fingerprint_source(c)
    assert fingerprint_source("not python (") is None


def test_plugin_writes_fingerprint_into_junit(pytester):
    pytester.makepyfile("def test_one():\n    assert 1\n")
    xml = pytester.path / "out.xml"
    pytester.runpytest("-p", "no:asyncio", "-p", "flakydetect.pytest_plugin", f"--junitxml={xml}")
    from flakydetect.junit import parse_file
    [res] = parse_file(xml)
    assert len(res.properties["fingerprint"]) == 16
