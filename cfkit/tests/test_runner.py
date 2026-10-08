from app.runner import Result, SubprocessRunner, build_command, tail
from tests.fakes import FakeRunner


def test_build_command_no_sudo_for_same_or_no_user():
    assert build_command(["echo", "x"], None, "root") == ["echo", "x"]
    assert build_command(["echo", "x"], "root", "root") == ["echo", "x"]


def test_build_command_wraps_other_user():
    assert build_command(["cloudflared", "tunnel", "list"], "toannc", "root") == [
        "sudo", "-n", "-H", "-u", "toannc", "--", "cloudflared", "tunnel", "list"
    ]


def test_subprocess_runner_captures_output():
    r = SubprocessRunner().run(["echo", "hi"])
    assert (r.code, r.out) == (0, "hi\n") and r.ok


def test_subprocess_runner_missing_binary_is_127():
    r = SubprocessRunner().run(["definitely-not-a-binary-xyz"])
    assert r.code == 127 and "not found" in r.err


def test_subprocess_runner_timeout_is_124():
    r = SubprocessRunner().run(["sleep", "5"], timeout=1)
    assert r.code == 124


def test_tail_prefers_stderr_and_truncates():
    assert tail(Result(1, "out", "boom")) == "boom"
    assert tail(Result(1, "only-out", "")) == "only-out"
    assert tail(Result(7, "", "")) == "exit code 7"
    assert len(tail(Result(1, "", "x" * 1000))) == 400


def test_fake_runner_last_rule_wins_and_records():
    f = FakeRunner()
    f.when_has("a", result=Result(1, "", "first"))
    f.when_has("a", "b", result=Result(2, "", "second"))
    assert f.run(["a", "b"]).code == 2
    assert f.run(["a"]).code == 1
    assert f.run(["zzz"]).code == 0
    assert f.has_call("a", "b") and not f.has_call("nope")
