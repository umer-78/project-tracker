from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tracker import auth, reports
from tracker.api import create_app
from tracker.db import Database
from tracker.seed import seed


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def seeded(db):
    info = seed(db)
    return db, info


@pytest.fixture
def client(seeded):
    db, _ = seeded
    return TestClient(create_app(db))


def token_for(client, email="umer@example.com", password="password123") -> dict:
    body = client.post("/api/login", json={"email": email, "password": password}).json()
    return {"authorization": f"Bearer {body['token']}"}


# ---------------------------------------------------------------------- auth
def test_password_hashing_round_trip():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("x", "not-a-hash")
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_login_issues_a_token_and_rejects_bad_credentials(seeded):
    db, info = seeded
    assert auth.login(db, "umer@example.com", "wrong") is None
    assert auth.login(db, "nobody@example.com", "password123") is None
    user, token = auth.login(db, "UMER@example.com", info["password"])   # email is case-insensitive
    assert user["role"] == "admin"
    assert auth.user_for_token(db, token)["id"] == user["id"]
    auth.revoke(db, token)
    assert auth.user_for_token(db, token) is None


def test_expired_tokens_are_rejected_and_deleted(seeded):
    db, _ = seeded
    user, token = auth.login(db, "umer@example.com", "password123")
    db.execute("UPDATE tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE token = ?", (token,))
    assert auth.user_for_token(db, token) is None
    assert db.one("SELECT 1 FROM tokens WHERE token = ?", (token,)) is None


def test_endpoints_require_authentication(client):
    for method, path in [("get", "/api/projects"), ("get", "/api/workload"), ("get", "/api/activity")]:
        assert getattr(client, method)(path).status_code == 401


def test_viewers_cannot_write(client):
    viewer = token_for(client, "client@example.com")
    assert client.get("/api/projects", headers=viewer).status_code == 200
    response = client.post("/api/projects", json={"key": "NEW", "name": "Nope"}, headers=viewer)
    assert response.status_code == 403
    assert client.post("/api/users", json={"email": "a@b.c", "name": "A", "password": "password123"},
                       headers=viewer).status_code == 403


def test_only_admins_create_users(client):
    member = token_for(client, "ayesha@example.com")
    assert client.post("/api/users", json={"email": "x@y.z", "name": "X", "password": "password123"},
                       headers=member).status_code == 403
    admin = token_for(client)
    assert client.post("/api/users", json={"email": "x@y.z", "name": "X", "password": "password123"},
                       headers=admin).status_code == 201
    assert client.post("/api/users", json={"email": "x@y.z", "name": "X", "password": "password123"},
                       headers=admin).status_code == 409   # duplicate email


# ------------------------------------------------------------------ database
def test_foreign_keys_are_enforced(db):
    with pytest.raises(Exception):
        db.execute("INSERT INTO tasks (project_id, title) VALUES (9999, 'orphan')")


def test_status_and_priority_are_constrained(seeded):
    db, info = seeded
    with pytest.raises(Exception):
        db.execute("INSERT INTO tasks (project_id, title, status) VALUES (?, 'x', 'nonsense')",
                   (info["projects"]["WEB"],))


def test_deleting_a_project_removes_its_tasks(seeded):
    db, info = seeded
    project = info["projects"]["MOB"]
    assert db.query("SELECT id FROM tasks WHERE project_id = ?", (project,))
    db.execute("DELETE FROM projects WHERE id = ?", (project,))
    assert db.query("SELECT id FROM tasks WHERE project_id = ?", (project,)) == []


# --------------------------------------------------------------------- tasks
def test_task_lifecycle(client, seeded):
    _, info = seeded
    headers = token_for(client)
    project = info["projects"]["WEB"]
    created = client.post(f"/api/projects/{project}/tasks", headers=headers, json={
        "title": "Write the integration tests", "priority": "high", "estimate_hours": 4,
        "due_date": str(date.today() + timedelta(days=3)),
    }).json()
    assert created["status"] == "todo" and created["completed_at"] is None

    moved = client.patch(f"/api/tasks/{created['id']}", headers=headers, json={"status": "done"}).json()
    assert moved["completed_at"] is not None, "completed_at drives every report"

    reopened = client.patch(f"/api/tasks/{created['id']}", headers=headers, json={"status": "todo"}).json()
    assert reopened["completed_at"] is None, "reopening must clear the completion time"

    detail = client.get(f"/api/tasks/{created['id']}", headers=headers).json()
    assert detail["project_key"] == "WEB"
    assert any(a["action"] == "task.status" for a in detail["activity"])

    assert client.delete(f"/api/tasks/{created['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/tasks/{created['id']}", headers=headers).status_code == 404


def test_task_validation(client, seeded):
    _, info = seeded
    headers = token_for(client)
    project = info["projects"]["WEB"]
    assert client.post(f"/api/projects/{project}/tasks", headers=headers, json={"title": ""}).status_code == 422
    assert client.post(f"/api/projects/{project}/tasks", headers=headers,
                       json={"title": "x", "status": "finished"}).status_code == 422
    assert client.post(f"/api/projects/{project}/tasks", headers=headers,
                       json={"title": "x", "estimate_hours": -2}).status_code == 422
    assert client.post(f"/api/projects/{project}/tasks", headers=headers,
                       json={"title": "x", "assignee_id": 999}).status_code == 422
    assert client.post("/api/projects/999/tasks", headers=headers, json={"title": "x"}).status_code == 404


def test_filters_and_search(client, seeded):
    _, info = seeded
    headers = token_for(client)
    project = info["projects"]["WEB"]
    all_tasks = client.get(f"/api/projects/{project}/tasks", headers=headers).json()
    done = client.get(f"/api/projects/{project}/tasks?status=done", headers=headers).json()
    assert 0 < len(done) < len(all_tasks)
    assert all(t["status"] == "done" for t in done)
    found = client.get(f"/api/projects/{project}/tasks?q=burndown", headers=headers).json()
    assert found and all("burndown" in t["title"].lower() or "burndown" in t["description"].lower() for t in found)
    urgent_first = [t["priority"] for t in all_tasks]
    assert urgent_first.index("urgent") < urgent_first.index("low"), "urgent tasks sort first"


def test_comments(client, seeded):
    _, info = seeded
    headers = token_for(client)
    task = client.get(f"/api/projects/{info['projects']['WEB']}/tasks", headers=headers).json()[0]
    created = client.post(f"/api/tasks/{task['id']}/comments", headers=headers,
                          json={"body": "Blocked on the API key."})
    assert created.status_code == 201 and created.json()["author"] == "Umer Hashmi"
    assert client.post(f"/api/tasks/{task['id']}/comments", headers=headers, json={"body": ""}).status_code == 422
    detail = client.get(f"/api/tasks/{task['id']}", headers=headers).json()
    assert any(c["body"] == "Blocked on the API key." for c in detail["comments"])


# ------------------------------------------------------------------- reports
def test_burndown_shape(seeded):
    db, info = seeded
    data = reports.burndown(db, info["sprints"]["current"])
    assert len(data["days"]) == len(data["ideal"]) == len(data["actual"]) == 14
    assert data["ideal"][0] == data["committed_hours"]
    assert data["ideal"][-1] == 0
    assert data["actual"][0] is not None
    assert data["actual"][-1] is None, "the actual line must not run into the future"
    assert data["remaining_hours"] == pytest.approx(data["committed_hours"] - data["completed_hours"])
    with pytest.raises(LookupError):
        reports.burndown(db, 9999)


def test_burndown_falls_as_tasks_are_completed(seeded):
    db, info = seeded
    sprint = info["sprints"]["current"]
    before = reports.burndown(db, sprint)
    task = db.one("SELECT id FROM tasks WHERE sprint_id = ? AND status != 'done'", (sprint,))
    db.execute("UPDATE tasks SET status = 'done', completed_at = datetime('now') WHERE id = ?", (task["id"],))
    after = reports.burndown(db, sprint)
    assert after["remaining_hours"] < before["remaining_hours"]
    assert after["tasks_done"] == before["tasks_done"] + 1


def test_workload_includes_people_with_nothing_open(seeded):
    db, info = seeded
    rows = reports.workload(db, info["projects"]["MOB"])
    names = {r["name"] for r in rows}
    assert "Sara Iqbal" in names
    assert "Client Viewer" not in names, "viewers are not assignees"
    assert all(r["open_hours"] >= 0 for r in rows)
    assert sum(r["overdue"] for r in rows) >= 1, "the demo data has an overdue mobile task"


def test_project_summary_and_completion(seeded):
    db, info = seeded
    summary = reports.project_summary(db, info["projects"]["WEB"])
    t = summary["tasks"]
    assert t["total"] == t["todo"] + t["in_progress"] + t["review"] + t["done"]
    assert summary["completion_percent"] == pytest.approx(round(100 * t["done"] / t["total"], 1))
    with pytest.raises(LookupError):
        reports.project_summary(db, 9999)


def test_throughput_counts_completed_work(seeded):
    db, info = seeded
    rows = reports.throughput(db, info["projects"]["WEB"], weeks=8)
    assert rows and sum(r["completed"] for r in rows) >= 1


def test_csv_export(client, seeded):
    _, info = seeded
    headers = token_for(client)
    response = client.get(f"/api/projects/{info['projects']['WEB']}/export.csv", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("id,title,status,priority")
    assert len(lines) == 13   # 12 demo tasks + header


def test_health_and_ui(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert "<title>Project Tracker</title>" in client.get("/").text


def test_seed_refuses_to_run_twice(db):
    seed(db)
    with pytest.raises(Exception):
        seed(db)
