from app.auth import LoginLimiter, hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("s3cret")
    assert h != "s3cret" and h.startswith("$argon2")
    assert verify_password(h, "s3cret") is True
    assert verify_password(h, "wrong") is False


def test_verify_with_empty_or_garbage_hash_is_false():
    assert verify_password("", "x") is False
    assert verify_password("not-a-hash", "x") is False


def test_limiter_blocks_after_max_failures_and_recovers():
    t = {"now": 0.0}
    lim = LoginLimiter(max_failures=3, window=100, clock=lambda: t["now"])
    for _ in range(3):
        assert not lim.blocked("1.1.1.1")
        lim.fail("1.1.1.1")
    assert lim.blocked("1.1.1.1") and not lim.blocked("2.2.2.2")
    t["now"] = 101
    assert not lim.blocked("1.1.1.1")


def test_limiter_reset_clears():
    lim = LoginLimiter(max_failures=1, window=100)
    lim.fail("ip")
    assert lim.blocked("ip")
    lim.reset("ip")
    assert not lim.blocked("ip")
