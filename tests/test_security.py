from reefberry.security import LoginThrottle, hash_password, password_problem, verify_password


def test_password_round_trip():
    stored = hash_password("coral-reef-1")
    assert stored.startswith("scrypt$")
    assert verify_password("coral-reef-1", stored)
    assert not verify_password("coral-reef-2", stored)
    assert not verify_password("anything", "garbage")


def test_same_password_gets_different_hashes():
    assert hash_password("same-password") != hash_password("same-password")


def test_password_rules():
    assert password_problem("short") is not None
    assert password_problem("long-enough", "different") is not None
    assert password_problem("long-enough", "long-enough") is None


def test_throttle_blocks_after_five_failures():
    t = LoginThrottle(max_failures=5, window_s=60)
    for _ in range(4):
        t.fail("1.2.3.4")
    assert t.blocked_for("1.2.3.4") == 0
    t.fail("1.2.3.4")
    assert t.blocked_for("1.2.3.4") > 0
    assert t.blocked_for("5.6.7.8") == 0
    t.reset("1.2.3.4")
    assert t.blocked_for("1.2.3.4") == 0
