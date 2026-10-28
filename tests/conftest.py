import os

import pytest
from sqlalchemy import text

from flakydetect import db

BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_POSTGRES_URL") else [])


@pytest.fixture(params=BACKENDS)
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = db.get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    else:
        eng = db.get_engine(os.environ["TEST_POSTGRES_URL"])
        db.metadata.drop_all(eng)
    db.init_db(eng)
    yield eng
    if request.param == "postgres":
        db.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def count(engine):
    def _count(table):
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    return _count
