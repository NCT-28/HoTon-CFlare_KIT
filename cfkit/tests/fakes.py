from __future__ import annotations

from app.runner import Result


class FakeRunner:
    """Records every command; later `when_has` rules win over earlier ones."""

    def __init__(self):
        self.calls: list[tuple[list[str], str | None]] = []
        self._rules: list[tuple[tuple[str, ...], object]] = []

    def when_has(self, *tokens: str, result):
        self._rules.append((tokens, result))

    def run(self, args, *, user=None, timeout=60):
        self.calls.append((list(args), user))
        for tokens, res in reversed(self._rules):
            if all(t in args for t in tokens):
                return res(args) if callable(res) else res
        return Result(0, "", "")

    def argv(self) -> list[list[str]]:
        return [c[0] for c in self.calls]

    def has_call(self, *tokens: str) -> bool:
        return any(all(t in a for t in tokens) for a in self.argv())
