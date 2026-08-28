from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

import marketplace_contracts.builder as builder
import marketplace_contracts.cli as cli
import marketplace_contracts.validator as validator
from marketplace_contracts.builder import build_catalog, check_catalog
from marketplace_contracts.docs_check import validate_docs, validate_site_output
from marketplace_contracts.io import (
    MAX_PACKAGE_BYTES,
    MAX_PAYLOAD_BYTES,
    canonical_json_bytes,
    reference_issue,
    sha256_bytes,
)
from marketplace_contracts.model import ContractError
from marketplace_contracts.validator import validate_document, validate_package


def _copy_docs_repository(repo_root: Path, destination: Path) -> Path:
    for relative in ("contracts", "schemas", "packages", "registry/source", "docs"):
        shutil.copytree(repo_root / relative, destination / relative)
    shutil.copy2(repo_root / "LICENSE", destination / "LICENSE")
    shutil.copy2(repo_root / "mkdocs.yml", destination / "mkdocs.yml")
    return destination


def _codes(failure: ContractError) -> set[str]:
    return {issue.code for issue in failure.issues}


def test_pointer_temp_symlink_cannot_mutate_outside_sentinel(
    contract_sandbox: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    sentinel = tmp_path / "outside-sentinel"
    sentinel.write_bytes(b"outside-byte-exact")
    (output / ".current.json.tmp").symlink_to(sentinel)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "OUTPUT_PATH_UNSAFE" in _codes(failure.value)
    assert sentinel.read_bytes() == b"outside-byte-exact"
    assert (output / ".current.json.tmp").is_symlink()


def test_check_rejects_symlink_backed_expected_index(
    contract_sandbox: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "generated"
    result = build_catalog(contract_sandbox, output)
    index = output / "snapshots" / result["revision"] / "index.json"
    sentinel = tmp_path / "outside-index"
    sentinel.write_bytes(index.read_bytes())
    index.unlink()
    index.symlink_to(sentinel)
    with pytest.raises(ContractError) as failure:
        check_catalog(contract_sandbox, output)
    assert "OUTPUT_PATH_UNSAFE" in _codes(failure.value)
    assert sentinel.read_bytes().startswith(b"{")


@pytest.mark.parametrize("case", ["output-root", "ancestor", "current-leaf"])
def test_output_symlinked_components_and_leaves_fail_closed(
    contract_sandbox: Path,
    tmp_path: Path,
    case: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"unchanged")
    if case == "output-root":
        output = tmp_path / "generated"
        output.symlink_to(outside, target_is_directory=True)
    elif case == "ancestor":
        ancestor = tmp_path / "linked-parent"
        ancestor.symlink_to(outside, target_is_directory=True)
        output = ancestor / "generated"
    else:
        output = tmp_path / "generated"
        build_catalog(contract_sandbox, output)
        current = output / "current.json"
        current.unlink()
        current.symlink_to(sentinel)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "OUTPUT_PATH_UNSAFE" in _codes(failure.value)
    assert sentinel.read_bytes() == b"unchanged"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_nonregular_output_leaf_fails_before_read(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    os.mkfifo(output / "current.json")
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "OUTPUT_PATH_UNSAFE" in _codes(failure.value)


def test_lock_contention_is_stable_and_non_destructive(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    lock = tmp_path / ".generated.lock"
    lock.mkdir()
    foreign = lock / "foreign"
    foreign.write_bytes(b"preserve-lock-owner")
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert _codes(failure.value) == {"OUTPUT_LOCKED"}
    assert foreign.read_bytes() == b"preserve-lock-owner"
    assert not output.exists()


def test_check_obeys_same_single_writer_lock(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    build_catalog(contract_sandbox, output)
    before = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    lock = tmp_path / ".generated.lock"
    lock.mkdir()
    (lock / "foreign").write_bytes(b"held")
    with pytest.raises(ContractError) as failure:
        check_catalog(contract_sandbox, output)
    assert _codes(failure.value) == {"OUTPUT_LOCKED"}
    after = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert after == before
    assert (lock / "foreign").read_bytes() == b"held"


def test_concurrent_foreign_file_is_preserved_and_conflict_is_reported(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "generated"
    original_write = builder._write_file

    def fail_pointer(path: Path, content: bytes):  # type: ignore[no-untyped-def]
        if path.name == ".current.json.tmp":
            foreign = output / "foreign-writer.txt"
            foreign.write_bytes(b"foreign-byte-exact")
            raise OSError("injected pointer failure")
        return original_write(path, content)

    monkeypatch.setattr(builder, "_write_file", fail_pointer)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert {"OUTPUT_IO_FAILED", "OUTPUT_CONCURRENT_MODIFICATION"}.issubset(_codes(failure.value))
    assert (output / "foreign-writer.txt").read_bytes() == b"foreign-byte-exact"
    assert sorted(path.name for path in output.iterdir()) == ["foreign-writer.txt"]


def test_cleanup_failure_is_not_hidden(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "generated"
    original_write = builder._write_file
    original_unlink = Path.unlink

    def fail_pointer(path: Path, content: bytes):  # type: ignore[no-untyped-def]
        if path.name == ".current.json.tmp":
            raise OSError("injected pointer failure")
        return original_write(path, content)

    def fail_index_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "index.json" and output in path.parents:
            raise OSError("injected cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(builder, "_write_file", fail_pointer)
    monkeypatch.setattr(Path, "unlink", fail_index_cleanup)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "OUTPUT_CLEANUP_FAILED" in _codes(failure.value)
    assert "OUTPUT_IO_FAILED" in _codes(failure.value)


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("top-level", b'{"schemaVersion":1,"schemaVersion":1}'),
        ("type", b'{"package":{"type":"widget","type":"theme"}}'),
        ("delivery", b'{"package":{"deliveryMode":"downloaded-executable","deliveryMode":"host-bundled-source"}}'),
        ("digest", b'{"payloads":[{"sha256":"a","sha256":"b"}]}'),
        ("trust", b'{"trust":{"review":"independently-reviewed","review":"unreviewed"}}'),
        ("revision", b'{"revision":"first","revision":"second"}'),
        ("revocation", b'{"revocations":[{"state":"revoked","state":"delisted"}]}'),
    ],
)
def test_duplicate_json_members_fail_cli_with_sanitized_code(
    repo_root: Path,
    tmp_path: Path,
    label: str,
    content: bytes,
) -> None:
    path = tmp_path / f"{label}.json"
    path.write_bytes(content)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketplace_contracts.cli",
            "--repo-root",
            str(repo_root),
            "validate",
            "--kind",
            "manifest",
            str(path),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert {issue["code"] for issue in payload["issues"]} == {"JSON_DUPLICATE_KEY"}
    assert label not in payload["issues"][0]["message"]


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        ("authorEvidence", "mastylo-maintained"),
        ("maintenance", "active"),
        ("provenance", "build-correlated"),
        ("review", "automation-validated"),
        ("review", "independently-reviewed"),
        ("security", "scoped-review-complete"),
    ],
)
def test_package_cannot_self_attest_elevated_trust(
    repo_root: Path,
    pointer: str,
    value: str,
) -> None:
    manifest = json.loads((repo_root / "packages/com.mastylolabs.clock/1.0.0/manifest.json").read_text())
    manifest["trust"][pointer] = value
    report = validate_document(repo_root, "manifest", manifest, target="manifest")
    assert "SCHEMA_INVALID" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    "value",
    [
        "<script>run()</script>",
        "<img src=x onerror=run()>",
        "javascript:run()",
        "&#x6a;avascript:run()",
        "%3Cscript%3Erun()%3C/script%3E",
        "safe\u0000control",
    ],
)
def test_manifest_display_text_rejects_active_and_encoded_forms(repo_root: Path, value: str) -> None:
    manifest = json.loads((repo_root / "packages/com.mastylolabs.clock/1.0.0/manifest.json").read_text())
    manifest["package"]["name"] = value
    report = validate_document(repo_root, "manifest", manifest, target="manifest")
    assert "PLAIN_TEXT_UNSAFE" in {issue.code for issue in report.issues}


def test_manifest_display_text_allows_benign_unicode(repo_root: Path) -> None:
    manifest = json.loads((repo_root / "packages/com.mastylolabs.clock/1.0.0/manifest.json").read_text())
    manifest["package"]["name"] = "Clock — Café (安全)!"
    assert validate_document(repo_root, "manifest", manifest, target="manifest").valid


@pytest.mark.parametrize(
    "probe",
    [
        "<script>globalThis.__W1_ACTIVE_DOC_PROBE__=1</script>",
        "<img src=x onerror=run()>",
        "[active](javascript:run())",
        "&lt;svg onload=run()&gt;",
    ],
)
def test_docs_reject_active_constructs_before_build(repo_root: Path, tmp_path: Path, probe: str) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + "\n" + probe + "\n", encoding="utf-8")
    report = validate_docs(sandbox)
    assert "DOC_ACTIVE_CONTENT" in {issue.code for issue in report.issues}


def test_mkdocs_excludes_review_evidence_and_private_markers(repo_root: Path, tmp_path: Path) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    new_report = sandbox / "docs/validation/new-review-report.md"
    new_report.parent.mkdir(parents=True, exist_ok=True)
    marker = "NEW_PRIVATE_REVIEW_MARKER"
    new_report.write_text(f"# Review only\n\n{marker}\n", encoding="utf-8")
    site = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=sandbox,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert validate_site_output(site).valid
    emitted = b"\n".join(path.read_bytes() for path in site.rglob("*") if path.is_file())
    for private in (
        marker.encode(),
        b"/Users/zbigniew/dev/code/172x-command",
        b"src-tauri/src/lib.rs",
        b"feat/intelligence-plugins-platform-support",
        b"35e7bba0b9f48fc0130d22c3b211a3698203b288",
        b"__W1_ACTIVE_DOC_PROBE__",
    ):
        assert private not in emitted
    assert not (site / "architecture").exists()
    assert not (site / "validation").exists()


def test_diagnostics_do_not_echo_payload_markers_or_absolute_roots(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    marker = "PRIVATE_PAYLOAD_MARKER"
    manifest = json.loads((repo_root / "packages/com.mastylolabs.clock/1.0.0/manifest.json").read_text())
    manifest["package"]["name"] = marker * 20
    path = tmp_path / f"{marker}.json"
    path.write_bytes(canonical_json_bytes(manifest))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketplace_contracts.cli",
            "--repo-root",
            str(repo_root),
            "validate",
            "--kind",
            "manifest",
            str(path),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert marker not in result.stdout + result.stderr
    assert str(repo_root) not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["target"] == "external-input"
    assert payload["issues"][0]["path"].startswith("$")


def test_unexpected_cli_failure_is_sanitized(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "PRIVATE_UNEXPECTED_MARKER"

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{marker}:{repo_root}")

    monkeypatch.setattr(cli, "validate_file", explode)
    assert cli.main(["--repo-root", str(repo_root), "validate", "--kind", "theme", "x.json"]) == 2
    output = capsys.readouterr().out
    assert marker not in output
    assert str(repo_root) not in output
    assert "INTERNAL_ERROR" in output


@pytest.mark.parametrize("size", [MAX_PAYLOAD_BYTES + 1, MAX_PAYLOAD_BYTES * 64])
def test_oversized_regular_and_sparse_payloads_are_not_read_or_hashed(
    contract_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    source = contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/src/ClockWidget.tsx"
    with source.open("wb") as stream:
        stream.truncate(size)
    observed: list[Path] = []
    original_read = validator.read_bounded_file

    def instrument(path: Path, *, max_bytes: int, target: str = "$") -> bytes:
        observed.append(path)
        return original_read(path, max_bytes=max_bytes, target=target)

    monkeypatch.setattr(validator, "read_bounded_file", instrument)
    _, report = validate_package(
        contract_sandbox,
        contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/manifest.json",
    )
    assert "LIMIT_EXCEEDED" in {issue.code for issue in report.issues}
    assert source not in observed


def test_cumulative_package_limit_short_circuits_overflow_payloads(
    contract_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    package_dir = manifest_path.parent
    zero_digest = hashlib.sha256(b"\0" * MAX_PAYLOAD_BYTES).hexdigest()
    overflow_paths: list[Path] = []
    for index in range(5):
        payload = package_dir / f"bounded-{index}.txt"
        with payload.open("wb") as stream:
            stream.truncate(MAX_PAYLOAD_BYTES)
        if index >= 3:
            overflow_paths.append(payload)
        manifest["payloads"].append(
            {
                "mediaType": "text/plain",
                "role": "notice",
                "sha256": zero_digest,
                "size": MAX_PAYLOAD_BYTES,
                "uri": payload.relative_to(contract_sandbox).as_posix(),
            }
        )
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    observed: list[Path] = []
    original_read = validator.read_bounded_file

    def instrument(path: Path, *, max_bytes: int, target: str = "$") -> bytes:
        observed.append(path)
        return original_read(path, max_bytes=max_bytes, target=target)

    monkeypatch.setattr(validator, "read_bounded_file", instrument)
    _, report = validate_package(contract_sandbox, manifest_path)
    assert "LIMIT_EXCEEDED" in {issue.code for issue in report.issues}
    assert set(overflow_paths).isdisjoint(observed)
    assert sum(path.stat().st_size for path in observed if path.name.startswith("bounded-")) <= MAX_PACKAGE_BYTES


def test_exact_payload_byte_boundary_remains_valid(contract_sandbox: Path) -> None:
    manifest_path = contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    source = contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/src/ClockWidget.tsx"
    prefix = b"export const boundary = '"
    suffix = b"';\n"
    content = prefix + b"a" * (MAX_PAYLOAD_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(content) == MAX_PAYLOAD_BYTES
    source.write_bytes(content)
    digest = sha256_bytes(content)
    source_entry = next(entry for entry in manifest["payloads"] if entry["role"] == "widget-source")
    source_entry["size"] = len(content)
    source_entry["sha256"] = digest
    widget = contract_sandbox / "packages/com.mastylolabs.clock/1.0.0/widget.json"
    widget_value = json.loads(widget.read_text())
    widget_value["sourceAssociation"]["sourceSha256"] = digest
    widget.write_bytes(canonical_json_bytes(widget_value))
    widget_entry = next(entry for entry in manifest["payloads"] if entry["role"] == "widget-data")
    widget_entry["size"] = widget.stat().st_size
    widget_entry["sha256"] = sha256_bytes(widget.read_bytes())
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    record, report = validate_package(contract_sandbox, manifest_path)
    assert report.valid
    assert record is not None


@pytest.mark.parametrize("value", ["safe%2fname", "safe%2Fname", "%252e%252e/file", "bad%zz"])
def test_reference_helper_rejects_every_percent_encoded_form(value: str) -> None:
    issue = reference_issue(value, "$")
    assert issue is not None
    assert issue.code == "URI_UNSAFE"


def test_ci_and_no_build_chain_are_immutable_and_least_privilege(repo_root: Path) -> None:
    workflow_text = (repo_root / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    uses = [step["uses"] for step in workflow["jobs"]["local-gate"]["steps"] if "uses" in step]
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    assert "pull_request_target" not in workflow_text
    assert "secrets" not in workflow_text.casefold()
    assert workflow["permissions"] == {"contents": "read"}
    assert "enable-cache: false" in workflow_text

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "build-system" not in pyproject
    assert pyproject["tool"]["uv"]["package"] is False
    lock = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    names = {package["name"] for package in lock["package"]}
    assert "hatchling" not in names
    assert all(
        artifact.get("hash", "").startswith("sha256:")
        for package in lock["package"]
        for artifact in ([package["sdist"]] if "sdist" in package else []) + package.get("wheels", [])
    )


def test_resource_constants_define_finite_v1_envelope() -> None:
    assert MAX_PAYLOAD_BYTES == 1_048_576
    assert MAX_PACKAGE_BYTES == 4_194_304
