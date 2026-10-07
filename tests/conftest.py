import os
import sys
import tempfile
from pathlib import Path

# Baz done tès izole ANVAN n enpòte app la
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db')}"
os.environ["MOCK_MODE"] = "true"
os.environ["SESSION_SECRET"] = "test-secret-key-for-tests"
os.environ["MOCK_AUTO_OK_S"] = "1"
os.environ["CRON_SECRET"] = "cron-test"
os.environ["MERU_ADDRESS"] = "GTESTMERUADDRESSXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    """Sesyon konekte + csrf pou tout tès yo."""
    r = client.post("/api/auth/setup", json={"password": "test-pass-123"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf"]
    return {"client": client, "csrf": csrf}


def h(csrf):
    return {"X-CSRF-Token": csrf}
