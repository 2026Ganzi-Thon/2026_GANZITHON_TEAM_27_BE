import os
import tempfile
import uuid

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["AI_PROVIDER"] = "fallback"
os.environ["BOARD_AUTO_PUBLISH"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.deps import limiter  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()


@pytest.fixture
def demo():
    return {"X-Demo-Id": str(uuid.uuid4())}
