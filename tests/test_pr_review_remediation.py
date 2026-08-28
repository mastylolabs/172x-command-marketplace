from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import marketplace_contracts.builder as builder
import marketplace_contracts.cli as cli
from marketplace_contracts.builder import build_catalog, compose_release
from marketplace_contracts.docs_check import validate_docs, validate_site_output
from marketplace_contracts.io import MAX_PACKAGE_BYTES, canonical_json_bytes, sha256_bytes
from marketplace_contracts.model import ContractError
from marketplace_contracts.validator import validate_catalog_snapshot, validate_package


AUTHORIZED_REVOCATIONS = (
    {
        "effectiveRevision": "w1-private-2026.08.27.1",
        "guidanceUri": "docs/contracts/v1/lifecycle-and-trust.md",
        "packageId": "com.mastylolabs.clock",
        "packageVersion": "1.0.0",
        "reasonCode": "SECURITY",
        "state": "suspended",
    },
    {
        "effectiveRevision": "w1-private-2026.08.27.1",
        "packageId": "org.catppuccin.mocha",
        "packageVersion": "1.0.0",
        "reasonCode": "POLICY",
        "state": "delisted",
    },
)

REVOCATION_MUTATIONS = (
    ("added", "REVOCATION_INCOHERENT", "$.revocations"),
    ("omitted", "REVOCATION_INCOHERENT", "$.revocations"),
    ("remapped-package", "REVOCATION_INCOHERENT", "$.revocations"),
    ("remapped-version", "REVOCATION_INCOHERENT", "$.revocations"),
    ("cross-wired", "REVOCATION_INCOHERENT", "$.revocations[0].state"),
    ("duplicated", "DUPLICATE_REVOCATION", "$.revocations[2]"),
    ("reordered", "REVOCATION_INCOHERENT", "$.revocations"),
    ("state", "REVOCATION_INCOHERENT", "$.revocations[0].state"),
    ("reason", "REVOCATION_INCOHERENT", "$.revocations[0].reasonCode"),
    ("effective-revision", "REVOCATION_INCOHERENT", "$.revocations[0].effectiveRevision"),
    ("guidance-altered", "REVOCATION_INCOHERENT", "$.revocations[0].guidanceUri"),
    ("guidance-omitted", "REVOCATION_INCOHERENT", "$.revocations[0].guidanceUri"),
)

DOC_SOURCE_CASES = (
    ("required-outside", "FILE_TYPE_UNSAFE", "docs/index.md"),
    ("required-inside", "FILE_TYPE_UNSAFE", "docs/index.md"),
    ("discovered-outside", "FILE_TYPE_UNSAFE", "docs/discovered.md"),
    ("discovered-inside", "FILE_TYPE_UNSAFE", "docs/discovered.md"),
    ("config-outside", "FILE_TYPE_UNSAFE", "mkdocs.yml"),
    ("config-inside", "FILE_TYPE_UNSAFE", "mkdocs.yml"),
    ("config-content", "DOC_SYNC_FAILED", "mkdocs.yml"),
)

REQUIRED_DOC_EXAMPLES = (
    "docs/examples/v1/theme.json",
    "docs/examples/v1/widget.json",
    "docs/examples/v1/panel.json",
)

REQUIRED_DOC_EXAMPLE_SUBSTITUTIONS = (
    "directory",
    "fifo",
    "inside-symlink",
    "outside-symlink",
)


def _copy_repository(repo_root: Path, destination: Path) -> Path:
    return Path(
        shutil.copytree(
            repo_root,
            destination,
            ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__"),
        )
    )


def _tree_bytes(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _assert_no_builder_residue(output: Path) -> None:
    assert not list(output.parent.glob(f".{output.name}.staging-*"))
    assert not (output.parent / f".{output.name}.lock").exists()


def _authorize_revocations(repo_root: Path) -> None:
    source_path = repo_root / "registry/source/v1/release.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["revocations"] = copy.deepcopy(AUTHORIZED_REVOCATIONS)
    source_path.write_bytes(canonical_json_bytes(source))


def _mutate_revocations(value: dict[str, Any], case: str) -> dict[str, Any]:
    altered = copy.deepcopy(value)
    items = altered["revocations"]
    if case == "added":
        items.append(
            {
                "effectiveRevision": altered["revision"],
                "packageId": "zzz.example.added",
                "packageVersion": "1.0.0",
                "reasonCode": "INTEGRITY",
                "state": "revoked",
            }
        )
    elif case == "omitted":
        items.pop()
    elif case == "remapped-package":
        items[0]["packageId"] = "aaa.example.clock"
    elif case == "remapped-version":
        items[0]["packageVersion"] = "2.0.0"
    elif case == "cross-wired":
        first = copy.deepcopy(items[0])
        second = copy.deepcopy(items[1])
        items[0]["state"] = second["state"]
        items[0]["reasonCode"] = second["reasonCode"]
        items[0].pop("guidanceUri")
        items[1]["state"] = first["state"]
        items[1]["reasonCode"] = first["reasonCode"]
        items[1]["guidanceUri"] = first["guidanceUri"]
    elif case == "duplicated":
        items.append(copy.deepcopy(items[1]))
    elif case == "reordered":
        items.reverse()
    elif case == "state":
        items[0]["state"] = "revoked"
    elif case == "reason":
        items[0]["reasonCode"] = "INTEGRITY"
    elif case == "effective-revision":
        items[0]["effectiveRevision"] = "w1-private-2026.08.27.2"
    elif case == "guidance-altered":
        items[0]["guidanceUri"] = "docs/SEC_PR_001_GUIDANCE.md"
    elif case == "guidance-omitted":
        items[0].pop("guidanceUri")
    else:  # pragma: no cover - the parameter table is closed above.
        raise AssertionError(case)
    return altered


def _snapshot_paths(
    directory: Path,
    index: dict[str, Any],
    revocations: dict[str, Any],
) -> tuple[Path, Path]:
    directory.mkdir()
    index_path = directory / "index.json"
    revocations_path = directory / "revocations.json"
    index_path.write_bytes(canonical_json_bytes(index))
    revocations_path.write_bytes(canonical_json_bytes(revocations))
    return index_path, revocations_path


def _install_docs_source_case(sandbox: Path, external_root: Path, case: str) -> str:
    marker = f"SEC_PR_002_{case.upper().replace('-', '_')}"
    if case.startswith("required-"):
        target = sandbox / "docs/index.md"
        inside = sandbox / "docs/contracts/v1/index.md"
        outside = external_root / "outside-index.md"
        outside.write_text(target.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8")
        target.unlink()
        target.symlink_to(outside if case.endswith("outside") else inside)
    elif case.startswith("discovered-"):
        target = sandbox / "docs/discovered.md"
        inside = sandbox / "docs/index.md"
        outside = external_root / "outside-discovered.md"
        outside.write_text(f"# Outside\n\n{marker}\n", encoding="utf-8")
        target.symlink_to(outside if case.endswith("outside") else inside)
    elif case.startswith("config-") and case != "config-content":
        target = sandbox / "mkdocs.yml"
        inside = sandbox / "inside-mkdocs.yml"
        outside = external_root / "outside-mkdocs.yml"
        content = target.read_text(encoding="utf-8") + f"\n# {marker}\n"
        inside.write_text(content, encoding="utf-8")
        outside.write_text(content, encoding="utf-8")
        target.unlink()
        target.symlink_to(outside if case.endswith("outside") else inside)
    elif case == "config-content":
        config = sandbox / "mkdocs.yml"
        config.write_text(
            f"docs_dir: ../outside-docs\n{config.read_text(encoding='utf-8')}\n# {marker}\n",
            encoding="utf-8",
        )
    else:  # pragma: no cover - the parameter table is closed above.
        raise AssertionError(case)
    return marker


def _run_cli_twice(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> tuple[list[int], list[dict[str, Any]]]:
    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(arguments))
        outputs.append(json.loads(capsys.readouterr().out))
    return exits, outputs


def _run_gate_before_mkdocs(sandbox: Path, sentinel: Path) -> subprocess.CompletedProcess[str]:
    (sandbox / "mkdocs.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['MF_PR_004_SENTINEL']).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["MF_PR_004_SENTINEL"] = str(sentinel)
    return subprocess.run(
        [sys.executable, str(sandbox / "scripts/gate.py")],
        cwd=sandbox,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _substitute_required_example(
    sandbox: Path, external_root: Path, relative: str, substitution: str
) -> str:
    target = sandbox / relative
    original = target.read_bytes()
    marker = f"MF_PR_004_{target.stem.upper()}_{substitution.upper().replace('-', '_')}"
    target.unlink()
    if substitution == "directory":
        target.mkdir()
        (target / "probe.txt").write_text(marker, encoding="utf-8")
    elif substitution == "fifo":
        os.mkfifo(target)
    elif substitution == "inside-symlink":
        inside = sandbox / f".{target.stem}-example.json"
        inside.write_bytes(original + marker.encode("utf-8"))
        target.symlink_to(inside)
    elif substitution == "outside-symlink":
        outside = external_root / f"outside-{target.stem}-example.json"
        outside.write_bytes(original + marker.encode("utf-8"))
        target.symlink_to(outside)
    else:  # pragma: no cover - the parameter table is closed above.
        raise AssertionError(substitution)
    return marker


@pytest.mark.parametrize(("case", "expected_code", "expected_path"), REVOCATION_MUTATIONS)
def test_source_revocation_coherence_rejects_direct_cli_and_builder_staging(
    contract_sandbox: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected_code: str,
    expected_path: str,
) -> None:
    if case != "added":
        _authorize_revocations(contract_sandbox)
    index, revocations, records = compose_release(contract_sandbox)
    altered_revocations = _mutate_revocations(revocations, case)
    altered_index = copy.deepcopy(index)
    altered_index["revocationsSha256"] = sha256_bytes(canonical_json_bytes(altered_revocations))
    index_path, revocations_path = _snapshot_paths(
        tmp_path / "snapshot", altered_index, altered_revocations
    )

    direct = [
        validate_catalog_snapshot(contract_sandbox, index_path, revocations_path).as_dict()
        for _ in range(2)
    ]
    assert direct[0] == direct[1]
    assert (expected_code, expected_path) in {
        (issue["code"], issue["path"]) for issue in direct[0]["issues"]
    }

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(
            cli.main(
                [
                    "--repo-root",
                    str(contract_sandbox),
                    "snapshot",
                    str(index_path),
                    str(revocations_path),
                ]
            )
        )
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    assert (expected_code, expected_path) in {
        (issue["code"], issue["path"]) for issue in outputs[0]["issues"]
    }

    output = tmp_path / "generated"
    build_catalog(contract_sandbox, output)
    before = _tree_bytes(output)

    def altered_compose(_: Path):  # type: ignore[no-untyped-def]
        return copy.deepcopy(altered_index), copy.deepcopy(altered_revocations), records

    monkeypatch.setattr(builder, "compose_release", altered_compose)
    with pytest.raises(ContractError) as failure:
        build_catalog(contract_sandbox, output)
    assert (expected_code, expected_path) in {
        (issue.code, issue.path) for issue in failure.value.issues
    }
    assert _tree_bytes(output) == before
    _assert_no_builder_residue(output)

    serialized = json.dumps({"direct": direct, "cli": outputs}, ensure_ascii=False)
    assert "SEC_PR_001" not in serialized
    assert str(contract_sandbox) not in serialized
    assert str(tmp_path) not in serialized
    assert "Traceback" not in serialized
    assert "Exception" not in serialized


def test_source_authorized_revocations_pass_and_build_deterministically(
    contract_sandbox: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _authorize_revocations(contract_sandbox)
    index, revocations, _ = compose_release(contract_sandbox)
    assert revocations["revocations"] == list(AUTHORIZED_REVOCATIONS)
    index_path, revocations_path = _snapshot_paths(tmp_path / "snapshot", index, revocations)
    assert validate_catalog_snapshot(contract_sandbox, index_path, revocations_path).valid
    assert cli.main(
        [
            "--repo-root",
            str(contract_sandbox),
            "snapshot",
            str(index_path),
            str(revocations_path),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert build_catalog(contract_sandbox, first)["indexSha256"] == build_catalog(
        contract_sandbox, second
    )["indexSha256"]
    assert _tree_bytes(first) == _tree_bytes(second)


@pytest.mark.parametrize(("case", "expected_code", "expected_path"), DOC_SOURCE_CASES)
def test_docs_source_preflight_rejects_symlink_and_untrusted_configuration_before_read(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected_code: str,
    expected_path: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    marker = _install_docs_source_case(sandbox, tmp_path, case)

    direct = [validate_docs(sandbox).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == [
        (expected_code, expected_path)
    ]

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(["--repo-root", str(sandbox), "docs"]))
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    assert [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]] == [
        (expected_code, expected_path)
    ]

    serialized = json.dumps({"direct": direct, "cli": outputs}, ensure_ascii=False)
    for forbidden in (marker, str(repo_root), str(tmp_path), "\x00", "\x1b", "\x85", "Traceback", "Exception"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "relative",
    ("docs/index.md", "docs/discovered.md", "mkdocs.yml"),
    ids=("required", "discovered", "configuration"),
)
def test_docs_source_preflight_rejects_nonregular_input_without_read(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    fifo = sandbox / relative
    if fifo.exists():
        fifo.unlink()
    os.mkfifo(fifo)
    report = validate_docs(sandbox)
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("FILE_TYPE_UNSAFE", relative)
    ]
    assert cli.main(["--repo-root", str(sandbox), "docs"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert [(issue["code"], issue["path"]) for issue in output["issues"]] == [
        ("FILE_TYPE_UNSAFE", relative)
    ]


def test_docs_source_bounded_read_accepts_exact_limit_and_rejects_one_over(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    boundary = sandbox / "docs/zz-boundary.bin"
    boundary.write_bytes(b"x" * MAX_PACKAGE_BYTES)
    assert validate_docs(sandbox).valid
    boundary.write_bytes(b"x" * (MAX_PACKAGE_BYTES + 1))
    report = validate_docs(sandbox)
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("LIMIT_EXCEEDED", "docs/zz-boundary.bin")
    ]
    assert cli.main(["--repo-root", str(sandbox), "docs"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert [(issue["code"], issue["path"]) for issue in output["issues"]] == [
        ("LIMIT_EXCEEDED", "docs/zz-boundary.bin")
    ]


@pytest.mark.parametrize("relative", REQUIRED_DOC_EXAMPLES)
def test_required_docs_example_absence_is_a_stable_contract_failure_before_mkdocs(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    (sandbox / relative).unlink()
    expected = [("FILE_NOT_FOUND", relative)]

    direct = [validate_docs(sandbox).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == expected

    command_outputs: dict[str, list[dict[str, Any]]] = {}
    for command in ("docs", "verify"):
        exits, outputs = _run_cli_twice(
            capsys, ["--repo-root", str(sandbox), command]
        )
        assert exits == [1, 1]
        assert outputs[0] == outputs[1]
        issue_pairs = [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]]
        if command == "docs":
            assert issue_pairs == expected
        else:
            assert expected[0] in issue_pairs
        command_outputs[command] = outputs

    before_generated = _tree_bytes(sandbox / "registry/generated")
    sentinel = tmp_path / "mkdocs-executed"
    gate = _run_gate_before_mkdocs(sandbox, sentinel)
    gate_output = gate.stdout + gate.stderr
    assert gate.returncode == 1
    assert "FILE_NOT_FOUND" in gate.stdout
    assert relative in gate.stdout
    assert "INTERNAL_ERROR" not in gate_output
    assert not sentinel.exists()
    assert not (sandbox / "site").exists()
    assert _tree_bytes(sandbox / "registry/generated") == before_generated

    serialized = json.dumps(
        {"direct": direct, "commands": command_outputs}, ensure_ascii=False
    )
    for forbidden in (
        str(repo_root),
        str(tmp_path),
        "\x00",
        "\x1b",
        "\x85",
        "Traceback",
        "Exception",
        "INTERNAL_ERROR",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("substitution", REQUIRED_DOC_EXAMPLE_SUBSTITUTIONS)
@pytest.mark.parametrize("relative", REQUIRED_DOC_EXAMPLES)
def test_required_docs_example_unsafe_substitution_fails_before_read_and_mkdocs(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
    substitution: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    marker = _substitute_required_example(sandbox, tmp_path, relative, substitution)
    expected = [("FILE_TYPE_UNSAFE", relative)]

    direct = [validate_docs(sandbox).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == expected

    command_outputs: dict[str, list[dict[str, Any]]] = {}
    for command in ("docs", "verify"):
        exits, outputs = _run_cli_twice(
            capsys, ["--repo-root", str(sandbox), command]
        )
        assert exits == [1, 1]
        assert outputs[0] == outputs[1]
        issue_pairs = [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]]
        if command == "docs":
            assert issue_pairs == expected
        else:
            assert expected[0] in issue_pairs
        command_outputs[command] = outputs

    before_generated = _tree_bytes(sandbox / "registry/generated")
    sentinel = tmp_path / "mkdocs-executed"
    gate = _run_gate_before_mkdocs(sandbox, sentinel)
    gate_output = gate.stdout + gate.stderr
    assert gate.returncode == 1
    assert "FILE_TYPE_UNSAFE" in gate.stdout
    assert relative in gate.stdout
    assert marker not in gate_output
    assert "INTERNAL_ERROR" not in gate_output
    assert not sentinel.exists()
    assert not (sandbox / "site").exists()
    assert _tree_bytes(sandbox / "registry/generated") == before_generated

    serialized = json.dumps(
        {"direct": direct, "commands": command_outputs}, ensure_ascii=False
    )
    for forbidden in (
        marker,
        str(repo_root),
        str(tmp_path),
        "\x00",
        "\x1b",
        "\x85",
        "Traceback",
        "Exception",
        "INTERNAL_ERROR",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("relative", REQUIRED_DOC_EXAMPLES)
def test_required_docs_example_malformed_json_retains_specific_contract_failure(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    (sandbox / relative).write_bytes(b'{"broken":')
    expected = [("JSON_INVALID", relative)]

    direct = [validate_docs(sandbox).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == expected
    for command in ("docs", "verify"):
        exits, outputs = _run_cli_twice(
            capsys, ["--repo-root", str(sandbox), command]
        )
        assert exits == [1, 1]
        assert outputs[0] == outputs[1]
        issue_pairs = [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]]
        if command == "docs":
            assert issue_pairs == expected
        else:
            assert expected[0] in issue_pairs


def test_required_docs_examples_canonical_paths_pass_all_docs_boundaries(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    direct = [validate_docs(repo_root).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert direct[0]["valid"] is True
    for command in ("docs", "verify"):
        exits, outputs = _run_cli_twice(
            capsys, ["--repo-root", str(repo_root), command]
        )
        assert exits == [0, 0]
        assert outputs[0] == outputs[1]

    site = tmp_path / "site"
    build = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    site_direct = [validate_site_output(site).as_dict() for _ in range(2)]
    assert site_direct[0] == site_direct[1]
    assert site_direct[0]["valid"] is True
    site_exits, site_outputs = _run_cli_twice(
        capsys, ["--repo-root", str(repo_root), "site", str(site)]
    )
    assert site_exits == [0, 0]
    assert site_outputs[0] == site_outputs[1]


@pytest.mark.parametrize("case", ("required-outside", "config-outside"))
def test_supported_gate_stops_before_mkdocs_for_unsafe_docs_inputs(
    repo_root: Path,
    tmp_path: Path,
    case: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    marker = _install_docs_source_case(sandbox, tmp_path, case)
    sentinel = tmp_path / "mkdocs-executed"
    (sandbox / "mkdocs.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['SEC_PR_002_SENTINEL']).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["SEC_PR_002_SENTINEL"] = str(sentinel)
    result = subprocess.run(
        [sys.executable, str(sandbox / "scripts/gate.py")],
        cwd=sandbox,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "FILE_TYPE_UNSAFE" in result.stdout
    assert marker not in result.stdout + result.stderr
    assert str(sandbox) not in result.stdout + result.stderr
    assert not sentinel.exists()
    assert not (sandbox / "site").exists()


@pytest.mark.parametrize("case", ("descriptor-revision", "manifest-revision"))
def test_widget_source_revision_cross_binding_rejects_direct_cli_verify_and_builder(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    sandbox = _copy_repository(repo_root, tmp_path / "repo")
    output = tmp_path / "generated"
    build_catalog(sandbox, output)
    before = _tree_bytes(output)
    manifest_path = sandbox / "packages/com.mastylolabs.clock/1.0.0/manifest.json"
    widget_path = sandbox / "packages/com.mastylolabs.clock/1.0.0/widget.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    widget = json.loads(widget_path.read_text(encoding="utf-8"))
    marker = f"SEC_PR_003_{case.upper().replace('-', '_')}"
    if case == "descriptor-revision":
        widget["sourceAssociation"]["sourceRevision"] = marker
        widget_bytes = canonical_json_bytes(widget)
        widget_path.write_bytes(widget_bytes)
        data_entry = next(item for item in manifest["payloads"] if item["role"] == "widget-data")
        data_entry["sha256"] = sha256_bytes(widget_bytes)
        data_entry["size"] = len(widget_bytes)
    else:
        manifest["source"]["revision"] = marker
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    expected = ("IDENTITY_MISMATCH", "$.sourceAssociation.sourceRevision")
    direct = [validate_package(sandbox, manifest_path)[1].as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert expected in {(issue["code"], issue["path"]) for issue in direct[0]["issues"]}

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(
            cli.main(
                [
                    "--repo-root",
                    str(sandbox),
                    "package",
                    "packages/com.mastylolabs.clock/1.0.0/manifest.json",
                ]
            )
        )
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    assert expected in {(issue["code"], issue["path"]) for issue in outputs[0]["issues"]}

    assert cli.main(["--repo-root", str(sandbox), "verify"]) == 1
    verify_output = json.loads(capsys.readouterr().out)
    assert expected in {
        (issue["code"], issue["path"]) for issue in verify_output["issues"]
    }
    with pytest.raises(ContractError) as failure:
        build_catalog(sandbox, output)
    assert expected in {(issue.code, issue.path) for issue in failure.value.issues}
    assert _tree_bytes(output) == before
    _assert_no_builder_residue(output)

    serialized = json.dumps({"direct": direct, "cli": outputs, "verify": verify_output})
    assert marker not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized
    assert "Traceback" not in serialized
    assert "Exception" not in serialized


def test_canonical_widget_source_revision_binding_remains_valid(repo_root: Path) -> None:
    manifest_path = repo_root / "packages/com.mastylolabs.clock/1.0.0/manifest.json"
    record, report = validate_package(repo_root, manifest_path)
    assert report.valid
    assert record is not None
    widget = json.loads((repo_root / "packages/com.mastylolabs.clock/1.0.0/widget.json").read_text())
    assert widget["sourceAssociation"]["sourceRevision"] == record.manifest["source"]["revision"]
