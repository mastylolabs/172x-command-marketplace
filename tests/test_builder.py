from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import marketplace_contracts.builder as builder
from marketplace_contracts.builder import build_catalog
from marketplace_contracts.cli import main as cli_main
from marketplace_contracts.io import canonical_json_bytes
from marketplace_contracts.model import ContractError
from marketplace_contracts.validator import validate_catalog_snapshot


FAILURE_BOUNDARIES = (
    "index-write",
    "revocations-write",
    "checksums-write",
    "snapshot-fsync",
    "staging-fsync",
    "snapshot-replace",
    "snapshot-directory-fsync",
    "pointer-write",
    "pointer-replace",
    "output-fsync",
)


def tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def tree_directories(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_dir()}


def staging_paths(output: Path) -> list[Path]:
    return sorted(output.parent.glob(f".{output.name}.staging-*"))


def install_failure(monkeypatch: pytest.MonkeyPatch, boundary: str, output: Path) -> None:
    original_write = builder._write_file
    original_fsync = builder.fsync_directory
    original_replace = builder.os.replace

    write_names = {
        "index-write": "index.json",
        "revocations-write": "revocations.json",
        "checksums-write": "SHA256SUMS",
    }

    def injected_write(file_path: Path, content: bytes):  # type: ignore[no-untyped-def]
        staged_name = write_names.get(boundary)
        is_staged_target = staged_name is not None and file_path.name == staged_name and file_path.parent.name == "snapshot"
        is_pointer_target = boundary == "pointer-write" and file_path.name == ".current.json.tmp"
        if is_staged_target or is_pointer_target:
            original_os_write = builder.os.write

            def partial_write(descriptor: int, value: bytes) -> int:
                original_os_write(descriptor, value[:1])
                raise OSError(f"injected {boundary} failure")

            monkeypatch.setattr(builder.os, "write", partial_write)
            try:
                return original_write(file_path, content)
            finally:
                monkeypatch.setattr(builder.os, "write", original_os_write)
        return original_write(file_path, content)

    def injected_fsync(directory: Path) -> None:
        matches = (
            (boundary == "snapshot-fsync" and directory.name == "snapshot")
            or (boundary == "staging-fsync" and directory.name.startswith(f".{output.name}.staging-"))
            or (boundary == "snapshot-directory-fsync" and directory == output / "snapshots")
            or (boundary == "output-fsync" and directory == output)
        )
        if matches:
            raise OSError(f"injected {boundary} failure")
        original_fsync(directory)

    def injected_replace(source: Path, destination: Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if boundary == "snapshot-replace" and source_path.name == "snapshot":
            original_replace(source, destination)
            raise OSError("injected snapshot-replace failure after rename")
        if (
            boundary == "pointer-replace"
            and source_path.name == ".current.json.tmp"
            and destination_path.name == "current.json"
        ):
            original_replace(source, destination)
            raise OSError("injected pointer-replace failure after rename")
        original_replace(source, destination)

    monkeypatch.setattr(builder, "_write_file", injected_write)
    monkeypatch.setattr(builder, "fsync_directory", injected_fsync)
    monkeypatch.setattr(builder.os, "replace", injected_replace)


def set_next_revision(contract_sandbox: Path) -> None:
    source_path = contract_sandbox / "registry/source/v1/release.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["revision"] = "w1-private-2026.08.27.2"
    source_path.write_bytes(canonical_json_bytes(source))


def test_builder_is_byte_deterministic(contract_sandbox: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_result = build_catalog(contract_sandbox, first)
    second_result = build_catalog(contract_sandbox, second)
    assert first_result["indexSha256"] == second_result["indexSha256"]
    assert tree_bytes(first) == tree_bytes(second)


def test_builder_failure_preserves_current_output(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    build_catalog(contract_sandbox, output)
    before = tree_bytes(output)
    theme = contract_sandbox / "packages/org.catppuccin.mocha/1.0.0/theme.json"
    theme.write_bytes(theme.read_bytes() + b"altered")
    with pytest.raises(ContractError):
        build_catalog(contract_sandbox, output)
    assert tree_bytes(output) == before


def test_first_failed_build_leaves_no_output(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    manifest = contract_sandbox / "packages/org.catppuccin.mocha/1.0.0/manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ContractError):
        build_catalog(contract_sandbox, output)
    assert not output.exists()


@pytest.mark.parametrize("boundary", FAILURE_BOUNDARIES)
def test_first_build_io_failure_cleans_all_artifacts(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    output = tmp_path / "generated"
    install_failure(monkeypatch, boundary, output)
    result = cli_main(
        ["--repo-root", str(contract_sandbox), "build", "--output", str(output)]
    )
    assert result == 1
    assert not output.exists()
    assert staging_paths(output) == []


@pytest.mark.parametrize("boundary", FAILURE_BOUNDARIES)
def test_rebuild_io_failure_restores_prior_tree_byte_for_byte(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    output = tmp_path / "generated"
    build_catalog(contract_sandbox, output)
    before_files = tree_bytes(output)
    before_directories = tree_directories(output)
    set_next_revision(contract_sandbox)
    install_failure(monkeypatch, boundary, output)
    result = cli_main(
        ["--repo-root", str(contract_sandbox), "build", "--output", str(output)]
    )
    assert result == 1
    assert tree_bytes(output) == before_files
    assert tree_directories(output) == before_directories
    assert staging_paths(output) == []


def test_immutable_revision_conflict_preserves_pointer(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    build_catalog(contract_sandbox, output)
    pointer_before = (output / "current.json").read_bytes()
    docs = contract_sandbox / "docs/contracts/v1/index.md"
    docs.write_text(docs.read_text(encoding="utf-8") + "\nnon-semantic change\n", encoding="utf-8")
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "OUTPUT_REVISION_CONFLICT" in {issue.code for issue in failure.value.issues}
    assert (output / "current.json").read_bytes() == pointer_before


def test_revocation_digest_binds_exact_file_bytes(contract_sandbox: Path, tmp_path: Path) -> None:
    output = tmp_path / "generated"
    result = build_catalog(contract_sandbox, output)
    snapshot = output / "snapshots" / result["revision"]
    revocations = snapshot / "revocations.json"
    revocations.write_bytes(revocations.read_bytes() + b" ")
    report = validate_catalog_snapshot(contract_sandbox, snapshot / "index.json", revocations)
    assert "CATALOG_REVOCATION_DIGEST_MISMATCH" in {issue.code for issue in report.issues}


def test_builder_staging_uses_complete_generated_source_validation(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "generated"
    original_compose = builder.compose_release

    def altered_compose(repo_root: Path):  # type: ignore[no-untyped-def]
        index, revocations, records = original_compose(repo_root)
        altered_index = copy.deepcopy(index)
        altered_index["generatedFrom"]["schemas"]["theme"]["sha256"] = "0" * 64
        return altered_index, revocations, records

    monkeypatch.setattr(builder, "compose_release", altered_compose)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert "DIGEST_MISMATCH" in {issue.code for issue in failure.value.issues}
    assert not output.exists()
    assert staging_paths(output) == []
