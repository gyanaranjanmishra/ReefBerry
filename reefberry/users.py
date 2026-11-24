"""Account management shared by the web app and the command line."""

from __future__ import annotations

from .db import Database, utcnow
from .security import hash_password, password_problem, username_problem


class UserError(Exception):
    pass


def list_users(db: Database) -> list[dict]:
    return [dict(r) for r in db.query("SELECT id, username, created_at FROM users ORDER BY id")]


def add_user(db: Database, username: str, password: str) -> int:
    username = username.strip()
    if problem := username_problem(username) or password_problem(password):
        raise UserError(problem)
    if db.one("SELECT 1 FROM users WHERE username = ?", (username,)):
        raise UserError(f"An account called '{username}' already exists.")
    db.execute(
        "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)",
        (username, hash_password(password), utcnow()),
    )
    db.log_event("account_created", detail=username)
    return db.one("SELECT id FROM users WHERE username = ?", (username,))["id"]


def set_password(db: Database, username: str, password: str) -> None:
    """Change a password and sign that account out everywhere."""
    if problem := password_problem(password):
        raise UserError(problem)
    row = db.one("SELECT id FROM users WHERE username = ?", (username,))
    if not row:
        raise UserError(f"There's no account called '{username}'.")
    db.execute(
        "UPDATE users SET password_hash = ?, session_epoch = session_epoch + 1 WHERE id = ?",
        (hash_password(password), row["id"]),
    )
    db.log_event("password_reset", detail=username)


def delete_user(db: Database, username: str) -> None:
    row = db.one("SELECT id FROM users WHERE username = ?", (username,))
    if not row:
        raise UserError(f"There's no account called '{username}'.")
    db.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    db.log_event("account_deleted", detail=username)
