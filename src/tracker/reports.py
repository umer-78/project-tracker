"""The numbers a team actually asks for: burndown, workload, overdue, throughput."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .db import Database


def _dates(start: str, end: str) -> list[date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def burndown(db: Database, sprint_id: int) -> dict:
    """Remaining estimated hours at the end of each day of the sprint.

    The ideal line is a straight run from the committed total to zero. The actual
    line only covers days up to today, because drawing it into the future is how
    a burndown chart starts lying.
    """
    sprint = db.one("SELECT * FROM sprints WHERE id = ?", (sprint_id,))
    if not sprint:
        raise LookupError(f"sprint {sprint_id} not found")
    tasks = db.query(
        "SELECT estimate_hours, status, completed_at FROM tasks WHERE sprint_id = ?", (sprint_id,)
    )
    committed = sum(t["estimate_hours"] for t in tasks)
    days = _dates(sprint["starts_on"], sprint["ends_on"])
    today = date.today()

    ideal, actual = [], []
    step = committed / (len(days) - 1) if len(days) > 1 else committed
    for i, day in enumerate(days):
        ideal.append(round(max(0.0, committed - step * i), 2))
        if day <= today:
            done = sum(
                t["estimate_hours"] for t in tasks
                if t["completed_at"] and datetime.fromisoformat(t["completed_at"]).date() <= day
            )
            actual.append(round(committed - done, 2))
        else:
            actual.append(None)

    completed_hours = sum(t["estimate_hours"] for t in tasks if t["status"] == "done")
    return {
        "sprint": {k: sprint[k] for k in ("id", "name", "starts_on", "ends_on", "goal")},
        "days": [d.isoformat() for d in days],
        "committed_hours": round(committed, 2),
        "completed_hours": round(completed_hours, 2),
        "remaining_hours": round(committed - completed_hours, 2),
        "ideal": ideal,
        "actual": actual,
        "tasks": len(tasks),
        "tasks_done": sum(1 for t in tasks if t["status"] == "done"),
    }


def workload(db: Database, project_id: int | None = None) -> list[dict]:
    """Open hours per person, so an overloaded assignee is visible before the standup."""
    # The project filter belongs in the JOIN, not the WHERE: with it in the WHERE
    # clause a person with no tasks in this project disappears from the report
    # instead of showing up with zero.
    join_filter = "AND t.project_id = ?" if project_id else ""
    rows = db.query(
        f"""SELECT u.id, u.name,
                   COUNT(t.id) AS open_tasks,
                   COALESCE(SUM(t.estimate_hours), 0) AS open_hours,
                   COALESCE(SUM(CASE WHEN t.due_date IS NOT NULL AND t.due_date < date('now')
                                     THEN 1 ELSE 0 END), 0) AS overdue,
                   COALESCE(SUM(CASE WHEN t.priority IN ('high', 'urgent') THEN 1 ELSE 0 END), 0) AS high_priority
            FROM users u
            LEFT JOIN tasks t ON t.assignee_id = u.id AND t.status != 'done' {join_filter}
            WHERE u.role != 'viewer'
            GROUP BY u.id, u.name
            ORDER BY open_hours DESC, u.name""",
        (project_id,) if project_id else (),
    )
    return [{**r, "open_hours": round(r["open_hours"] or 0, 2)} for r in rows]


def project_summary(db: Database, project_id: int) -> dict:
    project = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise LookupError(f"project {project_id} not found")
    counts = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM tasks WHERE project_id = ? GROUP BY status", (project_id,))}
    total = sum(counts.values())
    overdue = db.query(
        """SELECT id, title, due_date, assignee_id FROM tasks
           WHERE project_id = ? AND status != 'done' AND due_date IS NOT NULL AND due_date < date('now')
           ORDER BY due_date""", (project_id,))
    hours = db.one(
        """SELECT COALESCE(SUM(estimate_hours), 0) AS estimated,
                  COALESCE(SUM(spent_hours), 0) AS spent FROM tasks WHERE project_id = ?""",
        (project_id,))
    return {
        "project": {k: project[k] for k in ("id", "key", "name", "status")},
        "tasks": {"total": total, **{s: counts.get(s, 0) for s in ("todo", "in_progress", "review", "done")}},
        "completion_percent": round(100 * counts.get("done", 0) / total, 1) if total else 0.0,
        "estimated_hours": round(hours["estimated"], 2),
        "spent_hours": round(hours["spent"], 2),
        "overdue": overdue,
    }


def throughput(db: Database, project_id: int, weeks: int = 6) -> list[dict]:
    """Tasks completed per week: the honest way to forecast, instead of guessing."""
    rows = db.query(
        """SELECT strftime('%Y-%W', completed_at) AS week, COUNT(*) AS completed,
                  COALESCE(SUM(estimate_hours), 0) AS hours
           FROM tasks
           WHERE project_id = ? AND completed_at IS NOT NULL
             AND completed_at >= datetime('now', ?)
           GROUP BY week ORDER BY week""",
        (project_id, f"-{weeks * 7} days"),
    )
    return [{"week": r["week"], "completed": r["completed"], "hours": round(r["hours"], 2)} for r in rows]
