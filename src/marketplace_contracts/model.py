from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sanitize_diagnostic(value: str, *, maximum: int) -> str:
    cleaned = "".join(character if character.isprintable() else "?" for character in value)
    return cleaned[:maximum]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _sanitize_diagnostic(self.code, maximum=64))
        object.__setattr__(self, "path", _sanitize_diagnostic(self.path, maximum=240))
        object.__setattr__(self, "message", _sanitize_diagnostic(self.message, maximum=240))

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    target: str
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "valid": self.valid,
            "issues": [issue.as_dict() for issue in self.issues],
        }


class ContractError(Exception):
    def __init__(self, issues: list[ValidationIssue] | tuple[ValidationIssue, ...]):
        ordered = tuple(sorted(issues, key=lambda item: (item.path, item.code, item.message)))
        super().__init__(ordered[0].message if ordered else "contract validation failed")
        self.issues = ordered


@dataclass(frozen=True)
class PackageRecord:
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str

    @property
    def identity(self) -> tuple[str, str]:
        package = self.manifest["package"]
        return package["id"], package["version"]
