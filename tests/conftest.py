import pytest
from starlette.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FINANCE_SCHEDULER", "off")
    return tmp_path


@pytest.fixture
def client(data_dir):
    from finance.web import create_app

    with TestClient(create_app()) as c:
        yield c
