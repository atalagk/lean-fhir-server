import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import metadata
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Temp SQLite by default; set DATABASE_URL to run the suite on PostgreSQL."""
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    return Settings(database_url=url)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
        metadata.drop_all(app.state.engine)
