"""File I/O that never follows symlinks.

CFKit runs as root but writes into directories owned by the tunnel's service user, so every
path there is attacker-influenced. These helpers refuse to follow a symlink at the final path
component or a symlinked parent directory.
"""
from __future__ import annotations

import os
from pathlib import Path


def read_text_nofollow(path: Path) -> str:
    """Read a regular file; raises OSError for a symlink, FileNotFoundError if missing."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        return f.read()


def write_new_file(path: Path, text: str, mode: int = 0o644, ids: tuple[int, int] | None = None) -> None:
    """Create `path` fresh (O_EXCL|O_NOFOLLOW), replacing any existing entry, including a symlink."""
    if path.parent.is_symlink():
        raise OSError(f"refusing symlinked directory: {path.parent}")
    try:
        os.unlink(path)  # removes a symlink itself; never follows it
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        if ids is not None:
            os.fchown(fd, *ids)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w") as f:
        f.write(text)
