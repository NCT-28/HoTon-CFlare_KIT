import pytest

from app.safeio import read_text_nofollow, write_new_file


def test_write_creates_file_with_mode(tmp_path):
    p = tmp_path / "a.new"
    write_new_file(p, "hi", 0o640, None)
    assert p.read_text() == "hi" and (p.stat().st_mode & 0o777) == 0o640


def test_write_replaces_preexisting_symlink_without_touching_target(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret")
    link = tmp_path / "x.new"
    link.symlink_to(victim)
    write_new_file(link, "new", 0o644, None)
    assert victim.read_text() == "secret"
    assert not link.is_symlink() and link.read_text() == "new"


def test_write_refuses_symlinked_parent_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    d = tmp_path / "d"
    d.symlink_to(real)
    with pytest.raises(OSError):
        write_new_file(d / "f", "x", 0o644, None)
    assert not (real / "f").exists()


def test_read_refuses_symlink(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret")
    link = tmp_path / "l"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        read_text_nofollow(link)


def test_read_regular_and_missing(tmp_path):
    p = tmp_path / "f"
    p.write_text("data")
    assert read_text_nofollow(p) == "data"
    with pytest.raises(FileNotFoundError):
        read_text_nofollow(tmp_path / "nope")
