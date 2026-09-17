"""Demo data: two projects, a finished sprint, a running one and a backlog.

Dates are relative to today, so the burndown chart and the overdue list always
have something to show.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from . import auth
from .db import Database

PEOPLE = [
    ("umer@example.com", "Umer Hashmi", "admin"),
    ("ayesha@example.com", "Ayesha Khan", "member"),
    ("bilal@example.com", "Bilal Ahmed", "member"),
    ("sara@example.com", "Sara Iqbal", "member"),
    ("client@example.com", "Client Viewer", "viewer"),
]

WEB_TASKS = [
    ("Design the onboarding flow", "Three screens: sign up, verify e-mail, first project.", 8, "high"),
    ("Build the sign-up API", "Email + password, rate limited, with tests.", 13, "high"),
    ("Password reset by e-mail", "Single-use token valid for 30 minutes.", 5, "medium"),
    ("Kanban drag and drop", "Keyboard accessible as well as mouse.", 8, "high"),
    ("Task filters", "By assignee, sprint, status and free text.", 5, "medium"),
    ("CSV export", "Everything a manager pastes into a spreadsheet.", 3, "low"),
    ("Burndown chart", "Ideal line, actual line, no forecasting into the future.", 8, "medium"),
    ("Audit log", "Who changed what, and when.", 5, "medium"),
    ("Dark mode", "Follow the system setting.", 3, "low"),
    ("Load test the API", "500 concurrent board loads.", 5, "medium"),
    ("Fix flaky sprint test", "Fails on the first of the month.", 2, "urgent"),
    ("Write the deployment runbook", "Including the rollback steps.", 3, "medium"),
]

MOBILE_TASKS = [
    ("Push notification service", "Batched, with quiet hours.", 13, "high"),
    ("Offline task cache", "Read-only when there is no signal.", 8, "medium"),
    ("Biometric sign-in", "Face ID and fingerprint.", 5, "medium"),
    ("Crash reporting", "Symbolicated stack traces.", 3, "high"),
    ("App store screenshots", "Six locales.", 2, "low"),
]


def seed(db: Database, *, password: str = "password123", seed_value: int = 5) -> dict:
    """Fill an empty database with demo data. Returns the sign-in details."""
    rng = random.Random(seed_value)
    users = [auth.create_user(db, email, name, password, role) for email, name, role in PEOPLE]
    by_name = {u["name"]: u["id"] for u in users}
    members = [by_name[n] for n in ("Umer Hashmi", "Ayesha Khan", "Bilal Ahmed", "Sara Iqbal")]

    web = db.execute(
        "INSERT INTO projects (key, name, description, lead_id, status) VALUES (?, ?, ?, ?, 'active')",
        ("WEB", "Customer web app", "The main product: projects, boards and reports.", by_name["Umer Hashmi"]))
    mobile = db.execute(
        "INSERT INTO projects (key, name, description, lead_id, status) VALUES (?, ?, ?, ?, 'active')",
        ("MOB", "Mobile companion app", "iOS and Android client for the same API.", by_name["Ayesha Khan"]))

    today = date.today()
    last_start = today - timedelta(days=21)
    sprint_past = db.execute(
        "INSERT INTO sprints (project_id, name, starts_on, ends_on, goal) VALUES (?, ?, ?, ?, ?)",
        (web, "Sprint 7", last_start.isoformat(), (last_start + timedelta(days=13)).isoformat(),
         "Sign-up and onboarding end to end"))
    current_start = today - timedelta(days=5)
    sprint_now = db.execute(
        "INSERT INTO sprints (project_id, name, starts_on, ends_on, goal) VALUES (?, ?, ?, ?, ?)",
        (web, "Sprint 8", current_start.isoformat(), (current_start + timedelta(days=13)).isoformat(),
         "Board usability and reporting"))

    def add(project: int, sprint: int | None, title: str, description: str, hours: float,
            priority: str, status: str, assignee: int | None, due: date | None,
            completed: datetime | None) -> int:
        task_id = db.execute(
            """INSERT INTO tasks (project_id, sprint_id, title, description, status, priority,
                                  assignee_id, estimate_hours, spent_hours, due_date, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, sprint, title, description, status, priority, assignee, hours,
             round(hours * rng.uniform(0.4, 1.3), 1) if status != "todo" else 0,
             due.isoformat() if due else None, completed.isoformat(sep=" ") if completed else None))
        db.log("task.created", task_id=task_id, project_id=project, user_id=assignee, detail=title)
        return task_id

    # finished sprint: everything done, spread across its two weeks
    for i, (title, description, hours, priority) in enumerate(WEB_TASKS[:5]):
        done_at = datetime.combine(last_start + timedelta(days=2 + i * 2), datetime.min.time(),
                                   tzinfo=timezone.utc).replace(tzinfo=None)
        add(web, sprint_past, title, description, hours, priority, "done", members[i % len(members)],
            last_start + timedelta(days=13), done_at)

    # current sprint: a mix, with some already completed
    statuses = ["done", "done", "in_progress", "review", "todo", "todo", "in_progress"]
    for i, (title, description, hours, priority) in enumerate(WEB_TASKS[5:]):
        status = statuses[i % len(statuses)]
        completed = (datetime.combine(current_start + timedelta(days=1 + i), datetime.min.time())
                     if status == "done" else None)
        due = today + timedelta(days=rng.choice([-2, 1, 3, 6, 9]))
        add(web, sprint_now, title, description, hours, priority, status,
            members[(i + 2) % len(members)], due, completed)

    # mobile project: backlog only, one overdue
    for i, (title, description, hours, priority) in enumerate(MOBILE_TASKS):
        add(mobile, None, title, description, hours, priority,
            "in_progress" if i == 0 else "todo", members[i % len(members)],
            today + timedelta(days=[-3, 5, 12, 20, 30][i]), None)

    first_task = db.one("SELECT id FROM tasks ORDER BY id LIMIT 1")
    db.execute("INSERT INTO comments (task_id, user_id, body) VALUES (?, ?, ?)",
               (first_task["id"], by_name["Ayesha Khan"], "Design is approved, ready to build."))
    db.execute("INSERT INTO comments (task_id, user_id, body) VALUES (?, ?, ?)",
               (first_task["id"], by_name["Bilal Ahmed"], "Shipped behind a flag."))

    return {"email": "umer@example.com", "password": password,
            "projects": {"WEB": web, "MOB": mobile},
            "sprints": {"past": sprint_past, "current": sprint_now}}
