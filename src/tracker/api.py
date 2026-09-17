"""HTTP API and the static board UI."""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, reports
from .db import Database
from .schemas import CommentIn, LoginRequest, ProjectIn, SprintIn, TaskIn, TaskPatch, UserIn

WEB = Path(__file__).resolve().parents[2] / "web"
WRITE_ROLES = {"admin", "member"}


def create_app(db: Database | None = None) -> FastAPI:
    database = db or Database(os.environ.get("TRACKER_DB", "tracker.db"))
    app = FastAPI(title="Project Tracker", version="1.0.0",
                  description="Projects, sprints, tasks, workload and burndown.")
    app.state.db = database

    # ------------------------------------------------------------ auth plumbing
    def current_user(request: Request) -> dict:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        user = auth.user_for_token(database, token) if token else None
        if not user:
            raise HTTPException(status_code=401, detail="sign in first",
                                headers={"WWW-Authenticate": "Bearer"})
        return user

    def can_write(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in WRITE_ROLES:
            raise HTTPException(status_code=403, detail="your role is read-only")
        return user

    def admin_only(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="admins only")
        return user

    # -------------------------------------------------------------------- auth
    @app.post("/api/login", tags=["auth"])
    def login(body: LoginRequest) -> dict:
        result = auth.login(database, body.email, body.password)
        if not result:
            raise HTTPException(status_code=401, detail="wrong email or password")
        user, token = result
        return {"token": token, "user": user}

    @app.post("/api/logout", tags=["auth"])
    def logout(request: Request, user: dict = Depends(current_user)) -> dict:
        auth.revoke(database, request.headers["authorization"][7:].strip())
        return {"status": "signed out"}

    @app.get("/api/me", tags=["auth"])
    def me(user: dict = Depends(current_user)) -> dict:
        return user

    @app.get("/api/users", tags=["users"])
    def list_users(user: dict = Depends(current_user)) -> list[dict]:
        return database.query("SELECT id, email, name, role FROM users ORDER BY name")

    @app.post("/api/users", status_code=201, tags=["users"])
    def add_user(body: UserIn, user: dict = Depends(admin_only)) -> dict:
        if database.one("SELECT 1 FROM users WHERE email = ?", (body.email.lower(),)):
            raise HTTPException(status_code=409, detail="that email is already registered")
        created = auth.create_user(database, body.email, body.name, body.password, body.role)
        database.log("user.created", user_id=user["id"], detail=created["email"])
        return created

    # ---------------------------------------------------------------- projects
    @app.get("/api/projects", tags=["projects"])
    def list_projects(user: dict = Depends(current_user), status: str | None = None) -> list[dict]:
        sql = """SELECT p.*, u.name AS lead_name,
                        (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id) AS task_count,
                        (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') AS done_count
                 FROM projects p LEFT JOIN users u ON u.id = p.lead_id"""
        params: tuple = ()
        if status:
            sql += " WHERE p.status = ?"
            params = (status,)
        return database.query(sql + " ORDER BY p.created_at DESC", params)

    @app.post("/api/projects", status_code=201, tags=["projects"])
    def add_project(body: ProjectIn, user: dict = Depends(can_write)) -> dict:
        if database.one("SELECT 1 FROM projects WHERE key = ?", (body.key,)):
            raise HTTPException(status_code=409, detail=f"project key {body.key} is taken")
        pid = database.execute(
            "INSERT INTO projects (key, name, description, lead_id, status) VALUES (?, ?, ?, ?, ?)",
            (body.key, body.name, body.description, body.lead_id, body.status))
        database.log("project.created", project_id=pid, user_id=user["id"], detail=body.key)
        return database.one("SELECT * FROM projects WHERE id = ?", (pid,))

    @app.get("/api/projects/{project_id}", tags=["projects"])
    def get_project(project_id: int, user: dict = Depends(current_user)) -> dict:
        project = database.one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    @app.get("/api/projects/{project_id}/summary", tags=["reports"])
    def project_summary(project_id: int, user: dict = Depends(current_user)) -> dict:
        try:
            return reports.project_summary(database, project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/throughput", tags=["reports"])
    def throughput(project_id: int, weeks: int = Query(6, ge=1, le=52),
                   user: dict = Depends(current_user)) -> list[dict]:
        return reports.throughput(database, project_id, weeks)

    @app.delete("/api/projects/{project_id}", status_code=204, tags=["projects"])
    def delete_project(project_id: int, user: dict = Depends(admin_only)) -> None:
        if not database.one("SELECT 1 FROM projects WHERE id = ?", (project_id,)):
            raise HTTPException(status_code=404, detail="project not found")
        database.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ----------------------------------------------------------------- sprints
    @app.get("/api/projects/{project_id}/sprints", tags=["sprints"])
    def list_sprints(project_id: int, user: dict = Depends(current_user)) -> list[dict]:
        return database.query(
            """SELECT s.*, (SELECT COUNT(*) FROM tasks t WHERE t.sprint_id = s.id) AS task_count
               FROM sprints s WHERE s.project_id = ? ORDER BY s.starts_on DESC""", (project_id,))

    @app.post("/api/projects/{project_id}/sprints", status_code=201, tags=["sprints"])
    def add_sprint(project_id: int, body: SprintIn, user: dict = Depends(can_write)) -> dict:
        if not database.one("SELECT 1 FROM projects WHERE id = ?", (project_id,)):
            raise HTTPException(status_code=404, detail="project not found")
        sid = database.execute(
            "INSERT INTO sprints (project_id, name, starts_on, ends_on, goal) VALUES (?, ?, ?, ?, ?)",
            (project_id, body.name, body.starts_on.isoformat(), body.ends_on.isoformat(), body.goal))
        database.log("sprint.created", project_id=project_id, user_id=user["id"], detail=body.name)
        return database.one("SELECT * FROM sprints WHERE id = ?", (sid,))

    @app.get("/api/sprints/{sprint_id}/burndown", tags=["reports"])
    def burndown(sprint_id: int, user: dict = Depends(current_user)) -> dict:
        try:
            return reports.burndown(database, sprint_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ------------------------------------------------------------------- tasks
    @app.get("/api/projects/{project_id}/tasks", tags=["tasks"])
    def list_tasks(project_id: int, user: dict = Depends(current_user),
                   status: str | None = None, assignee_id: int | None = None,
                   sprint_id: int | None = None, q: str | None = None,
                   overdue: bool = False) -> list[dict]:
        sql = ["""SELECT t.*, u.name AS assignee_name, s.name AS sprint_name,
                         (SELECT COUNT(*) FROM comments c WHERE c.task_id = t.id) AS comment_count
                  FROM tasks t
                  LEFT JOIN users u ON u.id = t.assignee_id
                  LEFT JOIN sprints s ON s.id = t.sprint_id
                  WHERE t.project_id = ?"""]
        params: list = [project_id]
        if status:
            sql.append("AND t.status = ?")
            params.append(status)
        if assignee_id:
            sql.append("AND t.assignee_id = ?")
            params.append(assignee_id)
        if sprint_id:
            sql.append("AND t.sprint_id = ?")
            params.append(sprint_id)
        if q:
            sql.append("AND (t.title LIKE ? OR t.description LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if overdue:
            sql.append("AND t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date < date('now')")
        sql.append("ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                   "WHEN 'medium' THEN 2 ELSE 3 END, t.due_date IS NULL, t.due_date, t.id")
        return database.query(" ".join(sql), tuple(params))

    @app.post("/api/projects/{project_id}/tasks", status_code=201, tags=["tasks"])
    def add_task(project_id: int, body: TaskIn, user: dict = Depends(can_write)) -> dict:
        if not database.one("SELECT 1 FROM projects WHERE id = ?", (project_id,)):
            raise HTTPException(status_code=404, detail="project not found")
        if body.assignee_id and not database.one("SELECT 1 FROM users WHERE id = ?", (body.assignee_id,)):
            raise HTTPException(status_code=422, detail="assignee does not exist")
        completed = "datetime('now')" if body.status == "done" else "NULL"
        tid = database.execute(
            f"""INSERT INTO tasks (project_id, sprint_id, title, description, status, priority,
                                   assignee_id, estimate_hours, due_date, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {completed})""",
            (project_id, body.sprint_id, body.title, body.description, body.status, body.priority,
             body.assignee_id, body.estimate_hours, body.due_date.isoformat() if body.due_date else None))
        database.log("task.created", task_id=tid, project_id=project_id, user_id=user["id"],
                     detail=body.title)
        return database.one("SELECT * FROM tasks WHERE id = ?", (tid,))

    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: int, user: dict = Depends(current_user)) -> dict:
        task = database.one(
            """SELECT t.*, u.name AS assignee_name, p.key AS project_key
               FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id
               JOIN projects p ON p.id = t.project_id WHERE t.id = ?""", (task_id,))
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        task["comments"] = database.query(
            """SELECT c.id, c.body, c.created_at, u.name AS author
               FROM comments c JOIN users u ON u.id = c.user_id
               WHERE c.task_id = ? ORDER BY c.created_at""", (task_id,))
        task["activity"] = database.query(
            """SELECT a.action, a.detail, a.created_at, u.name AS actor
               FROM activity a LEFT JOIN users u ON u.id = a.user_id
               WHERE a.task_id = ? ORDER BY a.created_at DESC LIMIT 20""", (task_id,))
        return task

    @app.patch("/api/tasks/{task_id}", tags=["tasks"])
    def update_task(task_id: int, body: TaskPatch, user: dict = Depends(can_write)) -> dict:
        task = database.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return task
        if "due_date" in fields and isinstance(fields["due_date"], date):
            fields["due_date"] = fields["due_date"].isoformat()
        sets = [f"{k} = ?" for k in fields]
        values = list(fields.values())
        sets.append("updated_at = datetime('now')")
        if "status" in fields and fields["status"] != task["status"]:
            # completed_at is what every report keys off, so it is maintained here
            sets.append("completed_at = datetime('now')" if fields["status"] == "done" else "completed_at = NULL")
            database.log("task.status", task_id=task_id, project_id=task["project_id"],
                         user_id=user["id"], detail=f"{task['status']} → {fields['status']}")
        database.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*values, task_id))
        return database.one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    @app.delete("/api/tasks/{task_id}", status_code=204, tags=["tasks"])
    def delete_task(task_id: int, user: dict = Depends(can_write)) -> None:
        if not database.one("SELECT 1 FROM tasks WHERE id = ?", (task_id,)):
            raise HTTPException(status_code=404, detail="task not found")
        database.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    @app.post("/api/tasks/{task_id}/comments", status_code=201, tags=["tasks"])
    def add_comment(task_id: int, body: CommentIn, user: dict = Depends(can_write)) -> dict:
        task = database.one("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        cid = database.execute("INSERT INTO comments (task_id, user_id, body) VALUES (?, ?, ?)",
                               (task_id, user["id"], body.body))
        database.log("task.commented", task_id=task_id, project_id=task["project_id"], user_id=user["id"])
        return database.one(
            """SELECT c.id, c.body, c.created_at, u.name AS author FROM comments c
               JOIN users u ON u.id = c.user_id WHERE c.id = ?""", (cid,))

    # ----------------------------------------------------------------- reports
    @app.get("/api/workload", tags=["reports"])
    def workload(project_id: int | None = None, user: dict = Depends(current_user)) -> list[dict]:
        return reports.workload(database, project_id)

    @app.get("/api/projects/{project_id}/export.csv", tags=["reports"])
    def export_csv(project_id: int, user: dict = Depends(current_user)) -> StreamingResponse:
        rows = database.query(
            """SELECT t.id, t.title, t.status, t.priority, u.name AS assignee, s.name AS sprint,
                      t.estimate_hours, t.spent_hours, t.due_date, t.created_at, t.completed_at
               FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id
               LEFT JOIN sprints s ON s.id = t.sprint_id
               WHERE t.project_id = ? ORDER BY t.id""", (project_id,))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="text/csv", headers={
            "content-disposition": f'attachment; filename="project-{project_id}-tasks.csv"'})

    @app.get("/api/activity", tags=["reports"])
    def activity(limit: int = Query(30, ge=1, le=200), user: dict = Depends(current_user)) -> list[dict]:
        return database.query(
            """SELECT a.action, a.detail, a.created_at, u.name AS actor, t.title AS task_title,
                      p.key AS project_key
               FROM activity a
               LEFT JOIN users u ON u.id = a.user_id
               LEFT JOIN tasks t ON t.id = a.task_id
               LEFT JOIN projects p ON p.id = a.project_id
               ORDER BY a.created_at DESC, a.id DESC LIMIT ?""", (limit,))

    @app.get("/api/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "projects": len(database.query("SELECT id FROM projects"))}

    # --------------------------------------------------------------------- web
    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB / "index.html")

    return app


app = create_app()
