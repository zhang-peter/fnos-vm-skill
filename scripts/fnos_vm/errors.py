"""Error types and exit codes shared by the CLI and API client."""

from __future__ import annotations

from typing import Any


EXIT_OK = 0
EXIT_BUSINESS = 1
EXIT_USAGE = 2
EXIT_AUTH = 3


class FnosVmError(Exception):
    """Base class for errors that the CLI knows how to render."""

    kind = "error"
    exit_code = EXIT_BUSINESS

    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"kind": self.kind, "msg": self.message}
        if self.code is not None:
            error["code"] = self.code
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}


class UsageError(FnosVmError):
    kind = "argument"
    exit_code = EXIT_USAGE


class AuthError(FnosVmError):
    kind = "auth"
    exit_code = EXIT_AUTH


class BusinessError(FnosVmError):
    kind = "business"
    exit_code = EXIT_BUSINESS


class NetworkError(FnosVmError):
    kind = "network"
    exit_code = EXIT_BUSINESS


class SafetyError(FnosVmError):
    kind = "safety"
    exit_code = EXIT_BUSINESS


class PreflightError(FnosVmError):
    kind = "preflight"
    exit_code = EXIT_BUSINESS

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("preflight failed: " + "; ".join(problems), details=problems)
