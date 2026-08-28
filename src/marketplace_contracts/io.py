from __future__ import annotations

import hashlib
import html
import json
import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .model import ContractError, ValidationIssue

MAX_JSON_BYTES = 262_144
MAX_PAYLOAD_BYTES = 1_048_576
MAX_PACKAGE_BYTES = 4_194_304
MAX_REFERENCE_LENGTH = 240

_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_EVENT_HANDLER = re.compile(r"(?i)\bon[a-z][a-z0-9_-]*\s*=")
_SCRIPTABLE_URI = re.compile(r"(?i)\b(?:javascript|data|vbscript|file)\s*:")


class _DuplicateJsonKey(ValueError):
    """Internal sentinel; duplicate names and values never enter diagnostics."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def logical_target(repo_root: Path | None, path: Path) -> str:
    """Return a non-disclosing logical target for reports."""
    if repo_root is None:
        return "external-input"
    root = Path(os.path.abspath(repo_root))
    candidate = Path(os.path.abspath(path))
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return "external-input"


def _regular_file_stat(path: Path, *, target: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError([ValidationIssue("FILE_NOT_FOUND", target, "required file is missing")]) from exc
    except OSError as exc:
        raise ContractError([ValidationIssue("FILE_READ_FAILED", target, "file metadata could not be read")]) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractError(
            [ValidationIssue("FILE_TYPE_UNSAFE", target, "file must be a non-symlink regular file")]
        )
    return metadata


def regular_file_size(path: Path, *, target: str = "$") -> int:
    return _regular_file_stat(path, target=target).st_size


def read_bounded_file(path: Path, *, max_bytes: int, target: str = "$") -> bytes:
    """Read one stable regular file without following the final path component."""
    before = _regular_file_stat(path, target=target)
    if before.st_size > max_bytes:
        raise ContractError([ValidationIssue("LIMIT_EXCEEDED", target, "file exceeds the v1 byte limit")])
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError([ValidationIssue("FILE_READ_FAILED", target, "file could not be opened safely")]) from exc
    try:
        opened = os.fstat(descriptor)

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(before):
            raise ContractError([ValidationIssue("FILE_CHANGED", target, "file identity changed before bounded read")])
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(content) > max_bytes:
            raise ContractError([ValidationIssue("LIMIT_EXCEEDED", target, "file exceeds the v1 byte limit")])
        if identity(after) != identity(opened) or len(content) != opened.st_size:
            raise ContractError([ValidationIssue("FILE_CHANGED", target, "file identity changed during bounded read")])
        return content
    except OSError as exc:
        raise ContractError([ValidationIssue("FILE_READ_FAILED", target, "bounded file read failed")]) from exc
    finally:
        os.close(descriptor)


def sha256_file(path: Path, *, max_bytes: int = MAX_PACKAGE_BYTES, target: str = "$") -> str:
    return sha256_bytes(read_bounded_file(path, max_bytes=max_bytes, target=target))


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def load_json_bytes(content: bytes, *, target: str = "$") -> dict[str, Any] | list[Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError([ValidationIssue("JSON_INVALID", target, "JSON must be valid UTF-8")]) from exc
    try:
        value = json.loads(text, object_pairs_hook=_object_without_duplicates)
    except _DuplicateJsonKey as exc:
        raise ContractError(
            [ValidationIssue("JSON_DUPLICATE_KEY", target, "JSON object contains a duplicate member name")]
        ) from exc
    except json.JSONDecodeError as exc:
        raise ContractError([ValidationIssue("JSON_INVALID", target, "JSON syntax is invalid")]) from exc
    if not isinstance(value, (dict, list)):
        raise ContractError(
            [ValidationIssue("SCHEMA_INVALID", target, "top-level JSON value must be an object or array")]
        )
    return value


def load_json_with_bytes(
    path: Path,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    repo_root: Path | None = None,
    target: str | None = None,
) -> tuple[dict[str, Any] | list[Any], bytes]:
    logical = target or logical_target(repo_root, path)
    content = read_bounded_file(path, max_bytes=max_bytes, target=logical)
    return load_json_bytes(content, target=logical), content


def load_json(
    path: Path,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    repo_root: Path | None = None,
    target: str | None = None,
) -> dict[str, Any] | list[Any]:
    return load_json_with_bytes(path, max_bytes=max_bytes, repo_root=repo_root, target=target)[0]


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def plain_text_issue(value: object, path: str) -> ValidationIssue | None:
    if not isinstance(value, str):
        return None
    if any(unicodedata.category(character).startswith("C") for character in value):
        return ValidationIssue("PLAIN_TEXT_UNSAFE", path, "display text contains a prohibited control character")
    decoded = value
    for _ in range(3):
        expanded = unquote(html.unescape(decoded))
        if expanded == decoded:
            break
        decoded = expanded
    if _HTML_TAG.search(decoded) or _EVENT_HANDLER.search(decoded) or _SCRIPTABLE_URI.search(decoded):
        return ValidationIssue("PLAIN_TEXT_UNSAFE", path, "display text contains active or scriptable content")
    return None


def reference_issue(value: object, path: str) -> ValidationIssue | None:
    if not isinstance(value, str) or not value:
        return ValidationIssue("URI_UNSAFE", path, "reference must be a non-empty string")
    if len(value) > MAX_REFERENCE_LENGTH:
        return ValidationIssue(
            "LIMIT_EXCEEDED", path, f"reference exceeds v1 maximum of {MAX_REFERENCE_LENGTH} characters"
        )
    if "%" in value:
        return ValidationIssue("URI_UNSAFE", path, "percent-encoded references are not canonical v1 input")
    decoded = unquote(value)
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        return ValidationIssue("PATH_ABSOLUTE", path, "absolute references are prohibited")
    if "://" in decoded or decoded.lower().startswith(("javascript:", "data:", "file:", "vbscript:")):
        return ValidationIssue("URI_UNSAFE", path, "absolute or scriptable URI is prohibited")
    if "\\" in decoded or "?" in decoded or "#" in decoded:
        return ValidationIssue("URI_UNSAFE", path, "backslashes, query strings, and fragments are prohibited")
    parts = PurePosixPath(decoded).parts
    if any(part in {"", ".", ".."} for part in parts) or ".." in parts:
        return ValidationIssue("PATH_TRAVERSAL", path, "traversal and ambiguous path segments are prohibited")
    if PurePosixPath(decoded).as_posix() != decoded:
        return ValidationIssue("PATH_TRAVERSAL", path, "non-canonical path form is prohibited")
    if not _SAFE_REFERENCE.fullmatch(decoded):
        return ValidationIssue("URI_UNSAFE", path, "reference contains characters outside the v1 allowlist")
    return None


def resolve_reference(repo_root: Path, value: str, path: str) -> Path:
    issue = reference_issue(value, path)
    if issue:
        raise ContractError([issue])
    root = Path(os.path.abspath(repo_root))
    parts = PurePosixPath(value).parts
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise ContractError([ValidationIssue("PATH_TRAVERSAL", path, "reference resolves outside repository root")])
    current = root
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ContractError(
                [ValidationIssue("FILE_TYPE_UNSAFE", path, "reference components must not be symbolic links")]
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ContractError([ValidationIssue("FILE_TYPE_UNSAFE", path, "reference parent must be a directory")])
    return candidate


def fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
