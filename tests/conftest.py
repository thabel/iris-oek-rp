import os
import uuid
from pathlib import Path

import pytest

from oek_harvester.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES / "wfs"


@pytest.fixture
def cfg():
    return load_config(FIXTURES / "config.fixture.toml")


@pytest.fixture
def pg():
    """A connection plus a throwaway schema name; the schema is dropped
    afterwards. Requires IRIS_TEST_DATABASE_URL (a PostGIS database)."""
    url = os.environ.get("IRIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIS_TEST_DATABASE_URL not set; skipping PostGIS integration test")
    from psycopg import sql

    from oek_harvester.db import connect

    schema = f"iris_staging_test_{uuid.uuid4().hex[:8]}"
    conn = connect(url)
    try:
        yield conn, schema
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        conn.close()
