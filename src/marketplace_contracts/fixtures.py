from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .builder import compose_release
from .io import MAX_JSON_BYTES, canonical_json_bytes, load_json, resolve_reference
from .model import ContractError, ValidationIssue, ValidationReport
from .validator import validate_catalog_snapshot, validate_file, validate_package

FIXTURE_INDEX = "fixtures/cases.json"


def _mutate(value: Any, mutations: list[dict[str, Any]]) -> Any:
    result = copy.deepcopy(value)
    for mutation in mutations:
        pointer = mutation["pointer"]
        parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.strip("/").split("/") if part]
        parent = result
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        leaf = parts[-1]
        operation = mutation["op"]
        if operation == "set":
            if isinstance(parent, list):
                parent[int(leaf)] = mutation["value"]
            else:
                parent[leaf] = mutation["value"]
        elif operation == "delete":
            if isinstance(parent, list):
                del parent[int(leaf)]
            else:
                del parent[leaf]
        elif operation == "append":
            target = parent[int(leaf)] if isinstance(parent, list) else parent[leaf]
            target.append(copy.deepcopy(mutation["value"]))
        elif operation == "append-copy":
            target = parent[int(leaf)] if isinstance(parent, list) else parent[leaf]
            target.append(copy.deepcopy(target[int(mutation["index"])]))
        else:
            raise ValueError("unknown fixture mutation operation")
    return result


def validate_fixtures(repo_root: Path) -> ValidationReport:
    value = load_json(repo_root / FIXTURE_INDEX)
    if not isinstance(value, list):
        return ValidationReport(FIXTURE_INDEX, (ValidationIssue("FIXTURE_INDEX_INVALID", "$", "must be an array"),))
    issues: list[ValidationIssue] = []
    seen_names: set[str] = set()
    for index, case in enumerate(value):
        path = f"$[{index}]"
        if not isinstance(case, dict):
            issues.append(ValidationIssue("FIXTURE_INDEX_INVALID", path, "fixture case must be an object"))
            continue
        required = {"expect", "kind", "name"}
        if not required.issubset(case):
            issues.append(ValidationIssue("FIXTURE_INDEX_INVALID", path, "fixture requires expect/kind/name"))
            continue
        name = case["name"]
        if name in seen_names:
            issues.append(ValidationIssue("FIXTURE_INDEX_INVALID", path, "fixture name duplicated"))
        seen_names.add(name)
        expected = sorted(case["expect"])
        kind = case["kind"]
        try:
            if kind == "package":
                manifest = resolve_reference(repo_root, case["path"], f"{path}.path")
                _, report = validate_package(repo_root, manifest)
            elif kind == "mutation":
                base_path = resolve_reference(repo_root, case["base"], f"{path}.base")
                base_value = load_json(base_path)
                mutated = _mutate(base_value, case["mutations"])
                from .validator import validate_document

                report = validate_document(repo_root, case["documentKind"], mutated, target=name)
            elif kind == "package-alter-payload":
                manifest = resolve_reference(repo_root, case["path"], f"{path}.path")
                payload = resolve_reference(repo_root, case["payload"], f"{path}.payload")
                with tempfile.TemporaryDirectory(prefix="172x-package-fixture-") as temporary:
                    temporary_root = Path(temporary)
                    shutil.copytree(repo_root / "schemas", temporary_root / "schemas")
                    package_relative = manifest.parent.relative_to(repo_root)
                    shutil.copytree(manifest.parent, temporary_root / package_relative)
                    if (repo_root / "LICENSE").is_file():
                        shutil.copy2(repo_root / "LICENSE", temporary_root / "LICENSE")
                    altered = temporary_root / payload.relative_to(repo_root)
                    suffix = case.get("appendContent", "altered").encode("utf-8")
                    altered.write_bytes(altered.read_bytes() + suffix)
                    _, report = validate_package(temporary_root, temporary_root / manifest.relative_to(repo_root))
            elif kind == "snapshot-mutation":
                index_value, revocations_value, _ = compose_release(repo_root)
                index_value = _mutate(index_value, case.get("indexMutations", []))
                revocations_value = _mutate(revocations_value, case.get("revocationMutations", []))
                with tempfile.TemporaryDirectory(prefix="172x-snapshot-fixture-") as temporary:
                    temporary_root = Path(temporary)
                    index_path = temporary_root / "index.json"
                    revocations_path = temporary_root / "revocations.json"
                    index_path.write_bytes(canonical_json_bytes(index_value))
                    revocations_path.write_bytes(canonical_json_bytes(revocations_value))
                    report = validate_catalog_snapshot(repo_root, index_path, revocations_path)
            elif kind == "snapshot":
                index_path = resolve_reference(repo_root, case["path"], f"{path}.path")
                revocations_path = index_path.with_name("revocations.json")
                report = validate_catalog_snapshot(repo_root, index_path, revocations_path)
            elif kind == "oversized-json":
                with tempfile.TemporaryDirectory(prefix="172x-fixture-") as temporary:
                    oversized = Path(temporary) / "oversized.json"
                    oversized.write_bytes(b" " * (MAX_JSON_BYTES + 1))
                    try:
                        load_json(oversized)
                        report = ValidationReport(str(oversized), ())
                    except ContractError as exc:
                        report = ValidationReport(str(oversized), exc.issues)
            else:
                fixture_path = resolve_reference(repo_root, case["path"], f"{path}.path")
                report = validate_file(repo_root, kind, fixture_path)
        except ContractError as exc:
            report = ValidationReport(case.get("path", name), exc.issues)
        observed = sorted({issue.code for issue in report.issues})
        if observed != expected:
            issues.append(
                ValidationIssue(
                    "FIXTURE_EXPECTATION_FAILED",
                    path,
                    "fixture result differs from its registered reason-code expectation",
                )
            )
    return ValidationReport(FIXTURE_INDEX, tuple(sorted(issues, key=lambda item: (item.path, item.code, item.message))))
