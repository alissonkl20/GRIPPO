import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from grippo.api.app import create_app
from tests.conftest import subject, write

pytestmark = pytest.mark.security


@pytest.fixture
def client():
    return TestClient(create_app("secret-token", use_llm=False))


def test_token_required(client, repo):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/repo/state", params={"cwd": str(repo)}).status_code == 401
    bad = {"X-Grippo-Token": "wrong"}
    assert client.get("/api/v1/repo/state", params={"cwd": str(repo)}, headers=bad).status_code \
        == 401


def test_no_raw_shell_endpoints(client):
    headers = {"X-Grippo-Token": "secret-token"}
    for path in ("/api/v1/shell/git", "/api/v1/shell/run"):
        assert client.get(path, headers=headers).status_code in (404, 405)
        assert client.post(path, headers=headers, json={"argv": ["git", "clean", "-fdx"]}
                           ).status_code in (404, 405)


def test_turn_then_decide(client, repo):
    headers = {"X-Grippo-Token": "secret-token"}
    write(repo, "a.txt", "a")
    response = client.post("/api/v1/agent/turn", headers=headers,
                           json={"message": "faça commit", "cwd": str(repo)}).json()
    assert response["kind"] == "confirm" and response["preview"]["needs_confirmation"]
    assert subject(repo) == "chore: init"
    bogus = client.post("/api/v1/agent/decide", headers=headers,
                        json={"plan_id": "nope", "decision": "confirm"})
    assert bogus.status_code == 404
    done = client.post("/api/v1/agent/decide", headers=headers,
                       json={"plan_id": response["plan_id"], "decision": "confirm"}).json()
    assert done["status"] == "done" and subject(repo) != "chore: init"
    again = client.post("/api/v1/agent/decide", headers=headers,
                        json={"plan_id": response["plan_id"], "decision": "confirm"})
    assert again.status_code == 404
