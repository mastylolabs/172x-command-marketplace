from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .builder import DEFAULT_OUTPUT, SOURCE_DESCRIPTOR, build_catalog, check_catalog
from .docs_check import validate_docs, validate_site_output
from .fixtures import validate_fixtures
from .io import load_json, resolve_reference
from .model import ContractError, ValidationReport
from .scaffold import scaffold_package
from .trust import TEST_TRUST_OUTPUT, build_test_trust_bundle, check_test_trust_bundle, source_tree_digest
from .validator import validate_catalog_snapshot, validate_file, validate_package, validate_schemas


def _repo_root(value: str | None) -> Path:
    return Path(value or ".").resolve()


def _input_path(repo_root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else repo_root / candidate


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _report_exit(report: ValidationReport) -> int:
    _print(report.as_dict())
    return 0 if report.valid else 1


def _verify(repo_root: Path) -> dict[str, Any]:
    reports = [validate_schemas(repo_root), validate_fixtures(repo_root), validate_docs(repo_root)]
    source = load_json(repo_root / SOURCE_DESCRIPTOR, repo_root=repo_root)
    if isinstance(source, dict):
        for package in source.get("packages", []):
            if isinstance(package, dict) and isinstance(package.get("manifestUri"), str):
                manifest = resolve_reference(repo_root, package["manifestUri"], "$.packages.manifestUri")
                _, report = validate_package(repo_root, manifest)
                reports.append(report)
    build_result = check_catalog(repo_root, repo_root / DEFAULT_OUTPUT)
    current = load_json(repo_root / DEFAULT_OUTPUT / "current.json", repo_root=repo_root)
    if not isinstance(current, dict):
        raise ContractError([])
    snapshot = repo_root / DEFAULT_OUTPUT / current["snapshotUri"].replace("/index.json", "")
    reports.append(validate_catalog_snapshot(repo_root, snapshot / "index.json", snapshot / "revocations.json"))
    invalid = [report for report in reports if not report.valid]
    if invalid:
        issues = [issue for report in invalid for issue in report.issues]
        raise ContractError(issues)
    trust_result = check_test_trust_bundle(repo_root)
    return {
        "build": build_result,
        "trust": trust_result,
        "checks": [report.target for report in reports],
        "status": "passed",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marketplacectl")
    parser.add_argument("--repo-root", help="repository root; defaults to current directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schemas")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--kind", required=True, choices=["catalog", "revocations", "manifest", "theme", "widget", "panel"])
    validate.add_argument("path")
    package = subparsers.add_parser("package")
    package.add_argument("manifest")
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("index")
    snapshot.add_argument("revocations")
    subparsers.add_parser("fixtures")
    subparsers.add_parser("docs")
    site = subparsers.add_parser("site")
    site.add_argument("path")
    build = subparsers.add_parser("build")
    build.add_argument("--output", default=DEFAULT_OUTPUT)
    build.add_argument("--check", action="store_true")
    trust = subparsers.add_parser("trust-fixture")
    trust.add_argument("--output", default=TEST_TRUST_OUTPUT)
    trust.add_argument("--check", action="store_true")
    source_digest = subparsers.add_parser("source-digest")
    source_digest.add_argument("path")
    source_digest.add_argument("--revision", default="HEAD")
    scaffold = subparsers.add_parser("scaffold")
    scaffold.add_argument("--type", required=True, choices=["theme", "widget", "panel"])
    scaffold.add_argument("--id", required=True)
    scaffold.add_argument("--name", required=True)
    scaffold.add_argument("--version", default="1.0.0")
    subparsers.add_parser("verify")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = _repo_root(args.repo_root)
    try:
        if args.command == "schemas":
            return _report_exit(validate_schemas(repo_root))
        if args.command == "validate":
            return _report_exit(validate_file(repo_root, args.kind, _input_path(repo_root, args.path)))
        if args.command == "package":
            _, report = validate_package(repo_root, _input_path(repo_root, args.manifest))
            return _report_exit(report)
        if args.command == "snapshot":
            return _report_exit(
                validate_catalog_snapshot(
                    repo_root,
                    _input_path(repo_root, args.index),
                    _input_path(repo_root, args.revocations),
                )
            )
        if args.command == "fixtures":
            return _report_exit(validate_fixtures(repo_root))
        if args.command == "docs":
            return _report_exit(validate_docs(repo_root))
        if args.command == "site":
            return _report_exit(validate_site_output(_input_path(repo_root, args.path)))
        if args.command == "build":
            output = _input_path(repo_root, args.output)
            result = check_catalog(repo_root, output) if args.check else build_catalog(repo_root, output)
            _print({"status": "passed", **result})
            return 0
        if args.command == "trust-fixture":
            output = _input_path(repo_root, args.output)
            result = check_test_trust_bundle(repo_root, output) if args.check else build_test_trust_bundle(repo_root, output)
            _print({"status": "passed", **result})
            return 0
        if args.command == "source-digest":
            commit, digest, paths = source_tree_digest(repo_root, args.revision, args.path)
            _print({"commit": commit, "path": args.path, "sha256": digest, "trackedFiles": list(paths)})
            return 0
        if args.command == "scaffold":
            _print(scaffold_package(repo_root, args.type, args.id, args.name, args.version))
            return 0
        if args.command == "verify":
            _print(_verify(repo_root))
            return 0
    except ContractError as exc:
        _print({"status": "failed", "issues": [issue.as_dict() for issue in exc.issues]})
        return 1
    except Exception:
        _print(
            {
                "status": "failed",
                "issues": [{"code": "INTERNAL_ERROR", "message": "unexpected internal failure", "path": "$"}],
            }
        )
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
