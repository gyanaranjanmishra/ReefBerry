import io

import pytest

from reefberry import __main__ as cli
from reefberry.config import load_config
from reefberry.db import Database
from reefberry.security import verify_password


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REEFBERRY_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def typed(monkeypatch):
    """Pretend to be a terminal and feed answers to input() and getpass()."""
    answers: list[str] = []
    monkeypatch.setattr(cli.sys, "stdin", type("T", (io.StringIO,), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))
    return answers


def _hash(username):
    db = Database(load_config().db_path)
    row = db.one("SELECT password_hash FROM users WHERE username = ?", (username,))
    db.close()
    return row["password_hash"] if row else None


def test_add_then_reset_password(data_dir, typed, capsys):
    typed += ["reefer", "first-pass-1", "first-pass-1"]
    assert cli.main(["user", "add"]) == 0
    assert verify_password("first-pass-1", _hash("reefer"))

    # Single account: reset doesn't need a username. First attempt mismatches, second works.
    typed += ["new-pass-22", "typo-pass-22", "new-pass-22", "new-pass-22"]
    assert cli.main(["user", "reset-password"]) == 0
    assert verify_password("new-pass-22", _hash("reefer"))
    assert "don't match" in capsys.readouterr().out


def test_reset_unknown_user_fails(data_dir, typed, capsys):
    typed += ["reefer", "first-pass-1", "first-pass-1"]
    cli.main(["user", "add"])
    typed += ["new-pass-22", "new-pass-22"]
    assert cli.main(["user", "reset-password", "nobody"]) == 1
    assert "no account called" in capsys.readouterr().err


def test_list_with_no_accounts(data_dir, capsys):
    assert cli.main(["user", "list"]) == 0
    assert "No accounts yet" in capsys.readouterr().out
