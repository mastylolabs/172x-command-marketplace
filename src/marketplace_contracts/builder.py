from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import VERSION
from .io import (
    MAX_JSON_BYTES,
    canonical_json_bytes,
    fsync_directory,
    load_json,
    logical_target,
    read_bounded_file,
    resolve_reference,
    sha256_bytes,
    sha256_file,
)
from .model import ContractError, PackageRecord, ValidationIssue
from .validator import (
    CONTRACT_RELEASE_URI,
    EXPECTED_ARCHITECTURE_BINDING,
    EXPECTED_GATE_BINDING,
    EXPECTED_SOURCE_IDENTITY,
    SCHEMA_FILES,
    SOURCE_DESCRIPTOR_URI,
    validate_catalog_snapshot,
    validate_document,
    validate_package,
)

SOURCE_DESCRIPTOR = SOURCE_DESCRIPTOR_URI
CONTRACT_RELEASE = CONTRACT_RELEASE_URI
DEFAULT_OUTPUT = "registry/generated/v1"
EXPECTED_PACKAGE_IDS = {"org.catppuccin.mocha", "com.mastylolabs.clock"}


def _exact_keys(value: dict[str, Any], expected: set[str], path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        issues.append(ValidationIssue("SCHEMA_UNKNOWN_FIELD", path, "object contains an unknown field"))
    if missing:
        issues.append(ValidationIssue("SCHEMA_INVALID", path, "object is missing a required field"))
    return issues


def _validate_source_descriptor(repo_root: Path, source: Any) -> list[ValidationIssue]:
    if not isinstance(source, dict):
        return [ValidationIssue("SCHEMA_INVALID", "$", "release source must be an object")]
    issues = _exact_keys(
        source,
        {
            "architecture",
            "buildGate",
            "contractReleaseUri",
            "developerDocsUri",
            "packages",
            "releaseState",
            "revision",
            "revocations",
            "schemaVersion",
            "sourceIdentity",
        },
        "$",
    )
    if source.get("schemaVersion") != 1:
        issues.append(ValidationIssue("CONTRACT_MAJOR_UNSUPPORTED", "$.schemaVersion", "only release source v1 is supported"))
    if source.get("sourceIdentity") != EXPECTED_SOURCE_IDENTITY:
        issues.append(ValidationIssue("SOURCE_IDENTITY_MISMATCH", "$.sourceIdentity", "unexpected W1 source identity"))
    if source.get("releaseState") != "private":
        issues.append(ValidationIssue("PUBLICATION_STATE_INVALID", "$.releaseState", "W1 release must remain private"))
    architecture = source.get("architecture")
    if isinstance(architecture, dict):
        issues.extend(_exact_keys(architecture, {"artifactId", "sha256", "uri", "version"}, "$.architecture"))
        if architecture.get("artifactId") != "DA-W0-ARCH-001" or architecture.get("version") != "v0.1":
            issues.append(ValidationIssue("SOURCE_IDENTITY_MISMATCH", "$.architecture", "architecture identity differs from gate"))
        if architecture.get("sha256") != EXPECTED_ARCHITECTURE_BINDING["sha256"]:
            issues.append(ValidationIssue("DIGEST_MISMATCH", "$.architecture.sha256", "architecture hash differs from gate"))
        uri = architecture.get("uri")
        if isinstance(uri, str):
            try:
                path = resolve_reference(repo_root, uri, "$.architecture.uri")
                if not path.is_file():
                    issues.append(ValidationIssue("FILE_NOT_FOUND", "$.architecture.uri", "architecture input is missing"))
                elif sha256_file(path) != EXPECTED_ARCHITECTURE_BINDING["sha256"]:
                    issues.append(
                        ValidationIssue("DIGEST_MISMATCH", "$.architecture.sha256", "architecture input bytes changed")
                    )
            except ContractError as exc:
                issues.extend(exc.issues)
    else:
        issues.append(ValidationIssue("SCHEMA_INVALID", "$.architecture", "architecture binding is required"))
    gate = source.get("buildGate")
    if isinstance(gate, dict):
        issues.extend(_exact_keys(gate, {"artifactId", "sha256", "version"}, "$.buildGate"))
        if gate != EXPECTED_GATE_BINDING:
            issues.append(ValidationIssue("SOURCE_IDENTITY_MISMATCH", "$.buildGate", "build-gate identity differs"))
    else:
        issues.append(ValidationIssue("SCHEMA_INVALID", "$.buildGate", "build-gate binding is required"))
    packages = source.get("packages")
    if not isinstance(packages, list):
        issues.append(ValidationIssue("SCHEMA_INVALID", "$.packages", "packages must be an array"))
    elif len(packages) != 2:
        issues.append(ValidationIssue("REPRESENTATIVE_PACKAGE_COUNT", "$.packages", "W1 requires exactly two packages"))
    else:
        for index, item in enumerate(packages):
            if not isinstance(item, dict):
                issues.append(ValidationIssue("SCHEMA_INVALID", f"$.packages[{index}]", "package entry must be an object"))
                continue
            issues.extend(
                _exact_keys(
                    item,
                    {"classification", "manifestUri", "maturity", "publication"},
                    f"$.packages[{index}]",
                )
            )
            if item.get("publication") != "accepted-unpublished":
                issues.append(
                    ValidationIssue(
                        "PUBLICATION_STATE_INVALID",
                        f"$.packages[{index}].publication",
                        "private representatives must remain accepted-unpublished",
                    )
                )
            if item.get("classification") not in {"community"}:
                issues.append(
                    ValidationIssue(
                        "CLASSIFICATION_INVALID",
                        f"$.packages[{index}].classification",
                        "W1 representatives cannot claim Official or Curated status",
                    )
                )
            if item.get("maturity") != "experimental":
                issues.append(
                    ValidationIssue(
                        "MATURITY_INVALID",
                        f"$.packages[{index}].maturity",
                        "W1 representatives must remain experimental",
                    )
                )
            uri = item.get("manifestUri")
            if isinstance(uri, str):
                try:
                    resolve_reference(repo_root, uri, f"$.packages[{index}].manifestUri")
                except ContractError as exc:
                    issues.extend(exc.issues)
    if source.get("revocations") not in ([], None) and not isinstance(source.get("revocations"), list):
        issues.append(ValidationIssue("SCHEMA_INVALID", "$.revocations", "revocations must be an array"))
    for field in ("contractReleaseUri", "developerDocsUri"):
        uri = source.get(field)
        if isinstance(uri, str):
            try:
                path = resolve_reference(repo_root, uri, f"$.{field}")
                if not path.is_file():
                    issues.append(ValidationIssue("FILE_NOT_FOUND", f"$.{field}", "release input is missing"))
            except ContractError as exc:
                issues.extend(exc.issues)
    return issues


def _validate_contract_release(repo_root: Path, value: Any) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return [ValidationIssue("SCHEMA_INVALID", "$", "contract release must be an object")]
    issues = _exact_keys(
        value,
        {
            "artifactId",
            "date",
            "developerDocs",
            "reasonCodes",
            "schemas",
            "sourceIdentity",
            "state",
            "validatorVersion",
            "version",
        },
        "$",
    )
    expected_scalars = {
        "artifactId": "172X-MKT-CONTRACTS-001",
        "date": "2026-08-27",
        "sourceIdentity": EXPECTED_SOURCE_IDENTITY,
        "state": "private",
        "validatorVersion": VERSION,
        "version": "v1",
    }
    for key, expected in expected_scalars.items():
        if value.get(key) != expected:
            issues.append(ValidationIssue("SOURCE_IDENTITY_MISMATCH", f"$.{key}", f"expected {expected}"))
    schemas = value.get("schemas")
    if not isinstance(schemas, dict) or schemas != SCHEMA_FILES:
        issues.append(ValidationIssue("CONTRACT_SYNC_FAILED", "$.schemas", "schema ownership map differs from validator"))
    docs = value.get("developerDocs")
    if docs != {"contractVersion": "v1", "publicationState": "private-local-ci-only", "uri": "docs/contracts/v1/index.md"}:
        issues.append(ValidationIssue("CONTRACT_SYNC_FAILED", "$.developerDocs", "developer docs identity differs"))
    if value.get("reasonCodes") != "contracts/v1/reason-codes.json":
        issues.append(ValidationIssue("CONTRACT_SYNC_FAILED", "$.reasonCodes", "reason-code registry identity differs"))
    elif not (repo_root / value["reasonCodes"]).is_file():
        issues.append(ValidationIssue("FILE_NOT_FOUND", value["reasonCodes"], "reason-code registry is missing"))
    for uri in SCHEMA_FILES.values():
        if not (repo_root / uri).is_file():
            issues.append(ValidationIssue("FILE_NOT_FOUND", uri, "contract schema is missing"))
    return issues


def _file_binding(repo_root: Path, uri: str) -> dict[str, str]:
    path = resolve_reference(repo_root, uri, uri)
    return {"sha256": sha256_file(path), "uri": uri}


def compose_release(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any], list[PackageRecord]]:
    source_path = repo_root / SOURCE_DESCRIPTOR
    source = load_json(source_path, repo_root=repo_root)
    issues = _validate_source_descriptor(repo_root, source)
    contract_path = repo_root / CONTRACT_RELEASE
    contract = load_json(contract_path, repo_root=repo_root)
    issues.extend(_validate_contract_release(repo_root, contract))
    if not isinstance(source, dict):
        raise ContractError(issues)

    records: list[PackageRecord] = []
    seen: set[tuple[str, str]] = set()
    source_entries: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(source.get("packages", [])):
        if not isinstance(item, dict) or not isinstance(item.get("manifestUri"), str):
            continue
        try:
            manifest_path = resolve_reference(repo_root, item["manifestUri"], f"$.packages[{index}].manifestUri")
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        record, report = validate_package(repo_root, manifest_path)
        issues.extend(report.issues)
        if record is None:
            continue
        if record.identity in seen:
            issues.append(
                ValidationIssue("DUPLICATE_PACKAGE", f"$.packages[{index}]", "release package identity duplicated")
            )
        seen.add(record.identity)
        records.append(record)
        source_entries[record.identity] = item
    if {record.identity[0] for record in records} != EXPECTED_PACKAGE_IDS:
        issues.append(
            ValidationIssue(
                "REPRESENTATIVE_PACKAGE_SET",
                "$.packages",
                "W1 package set must be Catppuccin Mocha Theme and Clock Widget only",
            )
        )
    if issues:
        raise ContractError(issues)

    revision = source["revision"]
    revocations = {
        "releaseState": "private",
        "revision": revision,
        "revocations": sorted(
            source["revocations"], key=lambda item: (item["packageId"], item["packageVersion"], item["state"])
        ),
        "schemaVersion": 1,
    }
    revocations_report = validate_document(repo_root, "revocations", revocations, target="generated revocations")
    if not revocations_report.valid:
        raise ContractError(revocations_report.issues)

    entries: list[dict[str, Any]] = []
    for record in records:
        package = record.manifest["package"]
        source_entry = source_entries[record.identity]
        entries.append(
            {
                "classification": source_entry["classification"],
                "deliveryMode": package["deliveryMode"],
                "displayName": package["name"],
                "manifestSha256": record.manifest_sha256,
                "manifestUri": record.manifest_path.relative_to(repo_root).as_posix(),
                "maturity": source_entry["maturity"],
                "packageId": package["id"],
                "packageVersion": package["version"],
                "publication": source_entry["publication"],
                "revision": revision,
                "type": package["type"],
            }
        )
    entries.sort(key=lambda item: (item["displayName"].casefold(), item["packageId"], item["packageVersion"]))

    architecture = source["architecture"]
    gate = source["buildGate"]
    docs_uri = source["developerDocsUri"]
    index = {
        "catalogFormat": 1,
        "entries": entries,
        "generatedFrom": {
            "architecture": {
                "artifactId": architecture["artifactId"],
                "sha256": architecture["sha256"],
                "version": architecture["version"],
            },
            "buildGate": gate,
            "contractRelease": _file_binding(repo_root, source["contractReleaseUri"]),
            "developerDocs": {
                "contractVersion": "v1",
                "publicationState": "private-local-ci-only",
                "source": _file_binding(repo_root, docs_uri),
            },
            "schemas": {kind: _file_binding(repo_root, uri) for kind, uri in sorted(SCHEMA_FILES.items())},
            "source": _file_binding(repo_root, SOURCE_DESCRIPTOR_URI),
            "sourceIdentity": EXPECTED_SOURCE_IDENTITY,
            "validatorVersion": VERSION,
        },
        "releaseState": "private",
        "revision": revision,
        "revocationsSha256": sha256_bytes(canonical_json_bytes(revocations)),
        "revocationsUri": "catalog/v1/revocations.json",
        "schemaVersion": 1,
        "sort": "display-name-casefold,package-id,package-version",
    }
    index_report = validate_document(repo_root, "catalog", index, target="generated catalog")
    if not index_report.valid:
        raise ContractError(index_report.issues)
    return index, revocations, records


_REVISION_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_SNAPSHOT_FILES = {"index.json", "revocations.json", "SHA256SUMS"}


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass
class _Journal:
    created: dict[Path, _Identity] = field(default_factory=dict)
    prior_current: bytes | None = None
    published_current: _Identity | None = None


@dataclass(frozen=True)
class _Lock:
    root: Path
    root_identity: _Identity
    owner: Path
    owner_identity: _Identity
    owner_token: bytes


def _identity(metadata: os.stat_result) -> _Identity:
    return _Identity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _lstat(path: Path, *, code: str = "OUTPUT_PATH_UNSAFE") -> tuple[os.stat_result | None, list[ValidationIssue]]:
    try:
        return path.lstat(), []
    except FileNotFoundError:
        return None, []
    except OSError:
        return None, [ValidationIssue(code, "generated-output", "output path metadata could not be read")]


def _require_identity(path: Path, expected: _Identity) -> bool:
    metadata, _ = _lstat(path)
    if metadata is None:
        return False
    observed = _identity(metadata)
    if stat.S_ISDIR(expected.mode):
        return (
            observed.device == expected.device
            and observed.inode == expected.inode
            and stat.S_IFMT(observed.mode) == stat.S_IFMT(expected.mode)
        )
    return observed == expected


def _same_object(path: Path, expected: _Identity) -> bool:
    metadata, _ = _lstat(path)
    if metadata is None:
        return False
    observed = _identity(metadata)
    return (
        observed.device == expected.device
        and observed.inode == expected.inode
        and stat.S_IFMT(observed.mode) == stat.S_IFMT(expected.mode)
    )


def _validate_output_parent(output_root: Path) -> None:
    if not output_root.is_absolute() or output_root == Path(output_root.anchor):
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "output must be a bounded absolute child path")]
        )
    parent = output_root.parent
    metadata, metadata_issues = _lstat(parent)
    if metadata_issues:
        raise ContractError(metadata_issues)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "output parent must be an existing directory")]
        )
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "output parent must be operator-owned")]
        )
    if metadata.st_mode & 0o022:
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "output parent must not be group/world writable")]
        )
    current = Path(output_root.anchor)
    for part in output_root.parts[1:-1]:
        current /= part
        component, component_issues = _lstat(current)
        if component_issues or component is None or not stat.S_ISDIR(component.st_mode) or stat.S_ISLNK(component.st_mode):
            raise ContractError(
                [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "output ancestors must be real directories")]
            )


def _write_file(path: Path, content: bytes) -> _Identity:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    created_identity: _Identity | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        created_identity = _identity(os.fstat(descriptor))
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fsync(descriptor)
        final_identity = _identity(os.fstat(descriptor))
        if final_identity.inode != created_identity.inode or final_identity.device != created_identity.device:
            raise OSError("output identity changed")
        return final_identity
    except Exception:
        if created_identity is not None and _same_object(path, created_identity):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _mkdir_owned(path: Path, journal: _Journal) -> None:
    os.mkdir(path, 0o700)
    journal.created[path] = _identity(path.lstat())


def _scan_tree(path: Path, *, target: str) -> tuple[dict[str, bytes], dict[Path, _Identity]]:
    root_metadata, root_issues = _lstat(path)
    if root_issues:
        raise ContractError(root_issues)
    if root_metadata is None:
        return {}, {}
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", target, "output root must be a non-symlink directory")]
        )
    files: dict[str, bytes] = {}
    identities: dict[Path, _Identity] = {path: _identity(root_metadata)}
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise ContractError(
                [ValidationIssue("OUTPUT_PATH_UNSAFE", target, "output directory could not be read safely")]
            ) from exc
        for child in children:
            child_path = Path(child.path)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ContractError(
                    [ValidationIssue("OUTPUT_PATH_UNSAFE", target, "output entry metadata could not be read safely")]
                ) from exc
            identities[child_path] = _identity(metadata)
            relative = child_path.relative_to(path).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                raise ContractError(
                    [ValidationIssue("OUTPUT_PATH_UNSAFE", relative, "symbolic links are prohibited in generated output")]
                )
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child_path)
            elif stat.S_ISREG(metadata.st_mode):
                files[relative] = read_bounded_file(
                    child_path,
                    max_bytes=MAX_JSON_BYTES,
                    target=relative,
                )
            else:
                raise ContractError(
                    [ValidationIssue("OUTPUT_PATH_UNSAFE", relative, "non-regular generated output is prohibited")]
                )
    return files, identities


def _validate_output_shape(output_root: Path, files: dict[str, bytes], identities: dict[Path, _Identity]) -> None:
    if not identities:
        return
    relatives = {path.relative_to(output_root).as_posix() for path in identities if path != output_root}
    if not relatives:
        return
    if "current.json" not in files or output_root / "snapshots" not in identities:
        raise ContractError(
            [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "existing output tree is incomplete")]
        )
    for relative in sorted(relatives):
        parts = Path(relative).parts
        allowed = False
        if parts == ("current.json",):
            allowed = True
        elif parts == ("snapshots",):
            allowed = stat.S_ISDIR(identities[output_root / relative].mode)
        elif len(parts) == 2 and parts[0] == "snapshots" and _REVISION_NAME.fullmatch(parts[1]):
            allowed = stat.S_ISDIR(identities[output_root / relative].mode)
        elif (
            len(parts) == 3
            and parts[0] == "snapshots"
            and _REVISION_NAME.fullmatch(parts[1])
            and parts[2] in _SNAPSHOT_FILES
        ):
            allowed = relative in files
        if not allowed:
            raise ContractError(
                [ValidationIssue("OUTPUT_PATH_UNSAFE", relative, "unexpected generated-output entry is prohibited")]
            )
    revisions = {
        parts[1]
        for relative in relatives
        if len(parts := Path(relative).parts) >= 2 and parts[0] == "snapshots"
    }
    for revision in revisions:
        present = {
            Path(relative).name
            for relative in files
            if Path(relative).parts[:2] == ("snapshots", revision)
        }
        if present != _SNAPSHOT_FILES:
            raise ContractError(
                [ValidationIssue("OUTPUT_PATH_UNSAFE", "generated-output", "snapshot output set is incomplete")]
            )


def _tree_bytes(path: Path) -> dict[str, bytes]:
    files, identities = _scan_tree(path, target="generated-output")
    _validate_output_shape(path, files, identities)
    return files


def _tree_directories(path: Path) -> set[str]:
    _, identities = _scan_tree(path, target="generated-output")
    return {
        item.relative_to(path).as_posix()
        for item, identity in identities.items()
        if item != path and stat.S_ISDIR(identity.mode)
    }


def _acquire_lock(output_root: Path) -> _Lock:
    lock_root = output_root.parent / f".{output_root.name}.lock"
    try:
        os.mkdir(lock_root, 0o700)
    except FileExistsError as exc:
        raise ContractError(
            [ValidationIssue("OUTPUT_LOCKED", "generated-output", "another catalog writer owns the output lock")]
        ) from exc
    except OSError as exc:
        raise ContractError(
            [ValidationIssue("OUTPUT_IO_FAILED", "generated-output", "output lock could not be created")]
        ) from exc
    root_identity = _identity(lock_root.lstat())
    owner = lock_root / "owner"
    token = os.urandom(32)
    try:
        owner_identity = _write_file(owner, token)
    except Exception as exc:
        if _require_identity(lock_root, root_identity):
            try:
                lock_root.rmdir()
            except OSError:
                pass
        raise ContractError(
            [ValidationIssue("OUTPUT_IO_FAILED", "generated-output", "output lock ownership could not be recorded")]
        ) from exc
    return _Lock(lock_root, root_identity, owner, owner_identity, token)


def _release_lock(lock: _Lock) -> None:
    if not _require_identity(lock.root, lock.root_identity) or not _require_identity(lock.owner, lock.owner_identity):
        raise ContractError(
            [ValidationIssue("OUTPUT_CONCURRENT_MODIFICATION", "generated-output", "output lock ownership changed")]
        )
    if read_bounded_file(lock.owner, max_bytes=64, target="output-lock") != lock.owner_token:
        raise ContractError(
            [ValidationIssue("OUTPUT_CONCURRENT_MODIFICATION", "generated-output", "output lock token changed")]
        )
    lock.owner.unlink()
    lock.root.rmdir()
    fsync_directory(lock.root.parent)


def _journal_moved_tree(source_identities: dict[Path, _Identity], source: Path, destination: Path, journal: _Journal) -> None:
    for source_path, identity in source_identities.items():
        relative = source_path.relative_to(source)
        destination_path = destination if relative == Path(".") else destination / relative
        metadata, _ = _lstat(destination_path)
        if metadata is None:
            continue
        observed = _identity(metadata)
        if (
            observed.device == identity.device
            and observed.inode == identity.inode
            and stat.S_IFMT(observed.mode) == stat.S_IFMT(identity.mode)
        ):
            journal.created[destination_path] = observed


def _remove_journaled(journal: _Journal, *, beneath: Path | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    selected = {
        path: identity
        for path, identity in journal.created.items()
        if beneath is None or path == beneath or path.is_relative_to(beneath)
    }
    files = [path for path, identity in selected.items() if not stat.S_ISDIR(identity.mode)]
    directories = [path for path, identity in selected.items() if stat.S_ISDIR(identity.mode)]
    for path in sorted(files, key=lambda value: (len(value.parts), value.as_posix()), reverse=True):
        if not _require_identity(path, selected[path]):
            if path.exists() or path.is_symlink():
                issues.append(
                    ValidationIssue(
                        "OUTPUT_CONCURRENT_MODIFICATION",
                        "generated-output",
                        "an invocation-owned output file was replaced concurrently",
                    )
                )
            continue
        try:
            path.unlink()
        except OSError:
            issues.append(
                ValidationIssue("OUTPUT_CLEANUP_FAILED", "generated-output", "invocation-owned file cleanup failed")
            )
    for path in sorted(directories, key=lambda value: (len(value.parts), value.as_posix()), reverse=True):
        if not _require_identity(path, selected[path]):
            if path.exists() or path.is_symlink():
                issues.append(
                    ValidationIssue(
                        "OUTPUT_CONCURRENT_MODIFICATION",
                        "generated-output",
                        "an invocation-owned output directory was replaced concurrently",
                    )
                )
            continue
        try:
            path.rmdir()
        except OSError:
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "foreign entries prevent invocation-owned directory cleanup",
                )
            )
    return issues


def _rollback_output(
    output_root: Path,
    journal: _Journal,
    prior_files: dict[str, bytes],
    prior_paths: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    current = output_root / "current.json"
    if journal.published_current is not None:
        if not _require_identity(current, journal.published_current):
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "published pointer was replaced concurrently",
                )
            )
        elif journal.prior_current is None:
            try:
                current.unlink()
            except OSError:
                issues.append(
                    ValidationIssue("OUTPUT_CLEANUP_FAILED", "generated-output", "published pointer cleanup failed")
                )
        else:
            recovery = output_root / ".current.json.rollback"
            try:
                recovery_identity = _write_file(recovery, journal.prior_current)
                journal.created[recovery] = recovery_identity
                os.replace(recovery, current)
                journal.created.pop(recovery, None)
                fsync_directory(output_root)
            except Exception:
                issues.append(
                    ValidationIssue("OUTPUT_CLEANUP_FAILED", "generated-output", "prior pointer restoration failed")
                )
    journal.created.pop(current, None)
    issues.extend(_remove_journaled(journal))
    for relative, prior in prior_files.items():
        path = output_root / relative
        try:
            observed = read_bounded_file(path, max_bytes=MAX_JSON_BYTES, target=relative)
        except ContractError:
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "a prior output entry changed during the failed build",
                )
            )
            continue
        if observed != prior:
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "a prior output entry changed during the failed build",
                )
            )
    try:
        observed_files, observed_identities = _scan_tree(output_root, target="generated-output")
        observed_paths = {
            path.relative_to(output_root).as_posix()
            for path in observed_identities
            if path != output_root
        }
        if set(observed_files) != set(prior_files) or observed_paths != prior_paths:
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "foreign entries remain after invocation-owned rollback",
                )
            )
    except ContractError:
        if output_root.exists() or output_root.is_symlink():
            issues.append(
                ValidationIssue(
                    "OUTPUT_CONCURRENT_MODIFICATION",
                    "generated-output",
                    "foreign or unsafe entries remain after invocation-owned rollback",
                )
            )
    return issues


def _failure_issues(failure: Exception) -> list[ValidationIssue]:
    if isinstance(failure, ContractError):
        return list(failure.issues)
    return [ValidationIssue("OUTPUT_IO_FAILED", "generated-output", "catalog output operation failed")]


def build_catalog(repo_root: Path, output_root: Path) -> dict[str, Any]:
    output_root = Path(os.path.abspath(output_root))
    _validate_output_parent(output_root)
    lock = _acquire_lock(output_root)
    journal = _Journal()
    staging_root: Path | None = None
    prior_files: dict[str, bytes] = {}
    prior_paths: set[str] = set()
    try:
        prior_files, prior_identities = _scan_tree(output_root, target="generated-output")
        _validate_output_shape(output_root, prior_files, prior_identities)
        prior_paths = {
            path.relative_to(output_root).as_posix()
            for path in prior_identities
            if path != output_root
        }
        journal.prior_current = prior_files.get("current.json")
        index, revocations, _ = compose_release(repo_root)
        revision = index["revision"]
        staging_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.staging-", dir=output_root.parent))
        journal.created[staging_root] = _identity(staging_root.lstat())
        snapshot = staging_root / "snapshot"
        _mkdir_owned(snapshot, journal)
        index_bytes = canonical_json_bytes(index)
        revocations_bytes = canonical_json_bytes(revocations)
        checksums = (
            f"{sha256_bytes(index_bytes)}  index.json\n"
            f"{sha256_bytes(revocations_bytes)}  revocations.json\n"
        ).encode("utf-8")
        for name, content in (
            ("index.json", index_bytes),
            ("revocations.json", revocations_bytes),
            ("SHA256SUMS", checksums),
        ):
            destination = snapshot / name
            journal.created[destination] = _write_file(destination, content)
        snapshot_report = validate_catalog_snapshot(repo_root, snapshot / "index.json", snapshot / "revocations.json")
        if not snapshot_report.valid:
            raise ContractError(snapshot_report.issues)
        pointer = {
            "catalogFormat": 1,
            "indexSha256": sha256_bytes(index_bytes),
            "releaseState": "private",
            "revision": revision,
            "schemaVersion": 1,
            "snapshotUri": f"snapshots/{revision}/index.json",
        }
        pointer_bytes = canonical_json_bytes(pointer)
        fsync_directory(snapshot)
        fsync_directory(staging_root)

        if not prior_identities:
            _mkdir_owned(output_root, journal)
        snapshots_root = output_root / "snapshots"
        snapshots_metadata, snapshots_issues = _lstat(snapshots_root)
        if snapshots_issues:
            raise ContractError(snapshots_issues)
        if snapshots_metadata is None:
            _mkdir_owned(snapshots_root, journal)
        elif not stat.S_ISDIR(snapshots_metadata.st_mode) or stat.S_ISLNK(snapshots_metadata.st_mode):
            raise ContractError(
                [ValidationIssue("OUTPUT_PATH_UNSAFE", "snapshots", "snapshot parent must be a real directory")]
            )

        target_snapshot = snapshots_root / revision
        target_metadata, target_issues = _lstat(target_snapshot)
        if target_issues:
            raise ContractError(target_issues)
        staged_files, staged_identities = _scan_tree(snapshot, target="staged-snapshot")
        if target_metadata is not None:
            if not stat.S_ISDIR(target_metadata.st_mode) or stat.S_ISLNK(target_metadata.st_mode):
                raise ContractError(
                    [ValidationIssue("OUTPUT_PATH_UNSAFE", "snapshot", "snapshot revision must be a real directory")]
                )
            target_files, _ = _scan_tree(target_snapshot, target="snapshot")
            if target_files != staged_files:
                raise ContractError(
                    [
                        ValidationIssue(
                            "OUTPUT_REVISION_CONFLICT",
                            "snapshot",
                            "immutable generated revision already exists with different bytes",
                        )
                    ]
                )
            for path in sorted(staged_identities, key=lambda value: len(value.parts), reverse=True):
                if stat.S_ISDIR(staged_identities[path].mode):
                    path.rmdir()
                else:
                    path.unlink()
                journal.created.pop(path, None)
        else:
            try:
                os.replace(snapshot, target_snapshot)
            except Exception:
                _journal_moved_tree(staged_identities, snapshot, target_snapshot, journal)
                for path in list(journal.created):
                    if path == snapshot or path.is_relative_to(snapshot):
                        journal.created.pop(path)
                raise
            _journal_moved_tree(staged_identities, snapshot, target_snapshot, journal)
            for path in list(journal.created):
                if path == snapshot or path.is_relative_to(snapshot):
                    journal.created.pop(path)
            fsync_directory(snapshots_root)
        staging_identity = journal.created.pop(staging_root)
        if not _require_identity(staging_root, staging_identity):
            raise ContractError(
                [ValidationIssue("OUTPUT_CONCURRENT_MODIFICATION", "generated-output", "staging ownership changed")]
            )
        staging_root.rmdir()

        pointer_temp = output_root / ".current.json.tmp"
        pointer_temp_identity = _write_file(pointer_temp, pointer_bytes)
        journal.created[pointer_temp] = pointer_temp_identity
        try:
            os.replace(pointer_temp, output_root / "current.json")
        except Exception:
            current_metadata, _ = _lstat(output_root / "current.json")
            if current_metadata is not None and (
                current_metadata.st_dev == pointer_temp_identity.device
                and current_metadata.st_ino == pointer_temp_identity.inode
            ):
                journal.published_current = _identity(current_metadata)
                journal.created.pop(pointer_temp, None)
            raise
        journal.created.pop(pointer_temp, None)
        journal.published_current = _identity((output_root / "current.json").lstat())
        fsync_directory(output_root)
        _release_lock(lock)
        lock = None  # type: ignore[assignment]
        return {
            "indexSha256": pointer["indexSha256"],
            "output": logical_target(repo_root, output_root),
            "packages": len(index["entries"]),
            "revision": revision,
        }
    except Exception as failure:
        issues = _failure_issues(failure)
        issues.extend(_rollback_output(output_root, journal, prior_files, prior_paths))
        if lock is not None:
            try:
                _release_lock(lock)
            except ContractError as cleanup_failure:
                issues.extend(cleanup_failure.issues)
            except Exception:
                issues.append(
                    ValidationIssue("OUTPUT_CLEANUP_FAILED", "generated-output", "output lock cleanup failed")
                )
        raise ContractError(issues) from failure


def check_catalog(repo_root: Path, expected_output: Path) -> dict[str, Any]:
    expected_output = Path(os.path.abspath(expected_output))
    _validate_output_parent(expected_output)
    lock = _acquire_lock(expected_output)
    try:
        expected = _tree_bytes(expected_output)
        with tempfile.TemporaryDirectory(prefix="172x-marketplace-check-") as temporary:
            candidate = Path(temporary).resolve() / "generated"
            result = build_catalog(repo_root, candidate)
            observed = _tree_bytes(candidate)
            if expected != observed:
                raise ContractError(
                    [
                        ValidationIssue(
                            "GENERATED_OUTPUT_STALE",
                            "generated-output",
                            "generated output differs from the checked-in coherent snapshot",
                        )
                    ]
                )
            result["output"] = logical_target(repo_root, expected_output)
            result["status"] = "synchronized"
        _release_lock(lock)
        return result
    except Exception as failure:
        issues = _failure_issues(failure)
        try:
            _release_lock(lock)
        except ContractError as cleanup_failure:
            issues.extend(cleanup_failure.issues)
        except Exception:
            issues.append(ValidationIssue("OUTPUT_CLEANUP_FAILED", "generated-output", "output lock cleanup failed"))
        raise ContractError(issues) from failure
