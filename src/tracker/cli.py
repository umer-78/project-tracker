"""tracker: set up the database, seed demo data, or run the server."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from . import auth, reports
from .db import Database
from .seed import seed as seed_demo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tracker", description=__doc__)
    ap.add_argument("--db", default=os.environ.get("TRACKER_DB", "tracker.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database")
    sub.add_parser("demo", help="fill the database with demo data")

    u = sub.add_parser("adduser", help="create a user")
    u.add_argument("email")
    u.add_argument("name")
    u.add_argument("--role", choices=["admin", "member", "viewer"], default="member")

    s = sub.add_parser("serve", help="run the API and the board UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    r = sub.add_parser("report", help="print a project summary")
    r.add_argument("project_id", type=int)

    args = ap.parse_args(argv)
    db = Database(args.db)

    if args.cmd == "init":
        print(f"database ready at {Path(args.db).resolve()}")
        return 0

    if args.cmd == "demo":
        if db.one("SELECT 1 FROM users LIMIT 1"):
            print("database already has data — delete it first or use a new --db path")
            return 1
        info = seed_demo(db)
        print(f"demo data created in {args.db}")
        print(f"sign in as {info['email']} / {info['password']}")
        return 0

    if args.cmd == "adduser":
        password = getpass.getpass("password: ")
        user = auth.create_user(db, args.email, args.name, password, args.role)
        print(f"created {user['email']} ({user['role']})")
        return 0

    if args.cmd == "report":
        try:
            summary = reports.project_summary(db, args.project_id)
        except LookupError as exc:
            print(exc)
            return 1
        p = summary["project"]
        t = summary["tasks"]
        print(f"{p['key']} — {p['name']} ({p['status']})")
        print(f"  {t['done']}/{t['total']} tasks done ({summary['completion_percent']}%)")
        print(f"  todo {t['todo']}   in progress {t['in_progress']}   review {t['review']}")
        print(f"  estimated {summary['estimated_hours']}h, spent {summary['spent_hours']}h")
        if summary["overdue"]:
            print(f"  overdue: {len(summary['overdue'])}")
            for task in summary["overdue"][:5]:
                print(f"    #{task['id']} {task['title']} (due {task['due_date']})")
        print("\n  workload:")
        for row in reports.workload(db, args.project_id):
            print(f"    {row['name']:<16} {row['open_tasks']:>2} open  {row['open_hours']:>6}h"
                  f"  overdue {row['overdue']}")
        return 0

    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(db), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
