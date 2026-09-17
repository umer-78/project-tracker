"""Request and response shapes."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Status = Literal["todo", "in_progress", "review", "done"]
Priority = Literal["low", "medium", "high", "urgent"]
Role = Literal["admin", "member", "viewer"]


class LoginRequest(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    role: Role = "member"


class ProjectIn(BaseModel):
    key: str = Field(min_length=2, max_length=10, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    lead_id: int | None = None
    status: Literal["active", "on_hold", "done", "archived"] = "active"

    @field_validator("key")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class SprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    starts_on: date
    ends_on: date
    goal: str = ""

    @field_validator("ends_on")
    @classmethod
    def after_start(cls, v: date, info):
        start = info.data.get("starts_on")
        if start and v < start:
            raise ValueError("ends_on must not be before starts_on")
        return v


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    status: Status = "todo"
    priority: Priority = "medium"
    assignee_id: int | None = None
    sprint_id: int | None = None
    estimate_hours: float = Field(default=0, ge=0, le=1000)
    due_date: date | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: Status | None = None
    priority: Priority | None = None
    assignee_id: int | None = None
    sprint_id: int | None = None
    estimate_hours: float | None = Field(default=None, ge=0, le=1000)
    spent_hours: float | None = Field(default=None, ge=0, le=1000)
    due_date: date | None = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
