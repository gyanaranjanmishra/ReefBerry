"""Command line for ReefBerry.

    reefberry                          start the dashboard (what the service runs)
    reefberry user add                 create an account
    reefberry user reset-password      set a new password (signs that account out everywhere)
    reefberry user list                show accounts
    reefberry user delete NAME         remove an account

On a Pi installed with install.sh, run these with sudo, e.g. `sudo reefberry user reset-password`.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from .config import ensure_data_dir, load_config
from .db import Database
from .users import UserError, add_user, delete_user, list_users, set_password


def serve() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    uvicorn.run(
        "reefberry.app:create_app", factory=True, host=cfg.host, port=cfg.port,
        proxy_headers=False, log_level="info",
    )


def _open_db() -> Database:
    cfg = load_config()
    ensure_data_dir(cfg)
    return Database(cfg.db_path)


def _ask_password(prompt: str = "Password") -> str:
    if not sys.stdin.isatty():
        raise UserError("Run this in a terminal so the password can be typed in privately.")
    for _ in range(3):
        first = getpass.getpass(f"{prompt} (at least 8 characters): ")
        second = getpass.getpass("Repeat it: ")
        if first != second:
            print("The two passwords don't match. Try again.\n")
            continue
        if len(first) < 8:
            print("That's too short. Use at least 8 characters.\n")
            continue
        return first
    raise UserError("No password set.")


def _cmd_add(args) -> int:
    db = _open_db()
    username = args.username or input("Username: ").strip()
    password = _ask_password()
    add_user(db, username, password)
    print(f"Account '{username}' created. Sign in on the dashboard with it.")
    return 0


def _cmd_reset(args) -> int:
    db = _open_db()
    users = [u["username"] for u in list_users(db)]
    if not users:
        print("There are no accounts yet. Create one with:  reefberry user add")
        return 1
    username = args.username
    if not username:
        if len(users) == 1:
            username = users[0]
        else:
            print("Accounts: " + ", ".join(users))
            username = input("Which account? ").strip()
    print(f"Setting a new password for '{username}'.")
    set_password(db, username, _ask_password("New password"))
    print("Password changed. Anyone signed in to that account has been signed out.")
    return 0


def _cmd_list(args) -> int:
    users = list_users(_open_db())
    if not users:
        print("No accounts yet. Create one with:  reefberry user add")
    for u in users:
        print(f"{u['username']}  (created {u['created_at'][:10]})")
    return 0


def _cmd_delete(args) -> int:
    db = _open_db()
    users = list_users(db)
    if len(users) == 1 and users[0]["username"].lower() == args.username.lower():
        print("Warning: this is the only account. Nobody will be able to sign in until you add a new one.")
    if input(f"Delete account '{args.username}'? Type yes to confirm: ").strip().lower() != "yes":
        print("Nothing deleted.")
        return 1
    delete_user(db, args.username)
    print(f"Account '{args.username}' deleted.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reefberry", description="ReefBerry reef dashboard")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="start the dashboard (default)")
    user = sub.add_parser("user", help="manage accounts")
    user_sub = user.add_subparsers(dest="action", required=True)
    p = user_sub.add_parser("add", help="create an account")
    p.add_argument("username", nargs="?")
    p.set_defaults(func=_cmd_add)
    p = user_sub.add_parser("reset-password", help="set a new password")
    p.add_argument("username", nargs="?")
    p.set_defaults(func=_cmd_reset)
    p = user_sub.add_parser("list", help="show accounts")
    p.set_defaults(func=_cmd_list)
    p = user_sub.add_parser("delete", help="remove an account")
    p.add_argument("username")
    p.set_defaults(func=_cmd_delete)

    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        serve()
        return 0
    try:
        return args.func(args)
    except UserError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
