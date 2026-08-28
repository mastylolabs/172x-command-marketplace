from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from marketplace_contracts.builder import compose_release
from marketplace_contracts.docs_check import validate_docs
from marketplace_contracts.io import canonical_json_bytes
from marketplace_contracts.reason_codes import REASON_CODES


GENERATED_FILE_BINDINGS = (
    ("contractRelease",),
    ("developerDocs", "source"),
    ("schemas", "catalog"),
    ("schemas", "manifest"),
    ("schemas", "panel"),
    ("schemas", "revocations"),
    ("schemas", "theme"),
    ("schemas", "widget"),
    ("source",),
)

GENERATED_FIXED_IDENTITY_MUTATIONS = (
    (("architecture", "artifactId"), "OTHER-ARCH"),
    (("architecture", "sha256"), "0" * 64),
    (("architecture", "version"), "v9.9"),
    (("buildGate", "artifactId"), "OTHER-GATE"),
    (("buildGate", "sha256"), "0" * 64),
    (("buildGate", "version"), "v9.9"),
    (("developerDocs", "contractVersion"), "v9"),
    (("developerDocs", "publicationState"), "public"),
    (("sourceIdentity",), "OTHER-SOURCE"),
    (("validatorVersion",), "9.9.9"),
)


def test_docs_examples_links_and_release_sync(repo_root: Path) -> None:
    assert validate_docs(repo_root).valid


def test_all_emitted_and_fixture_reason_codes_are_registered(repo_root: Path) -> None:
    emitted: set[str] = set()
    pattern = re.compile(r'(?:ValidationIssue|"code"\s*:?)\(?(?:\s*)["\']([A-Z][A-Z0-9_]+)["\']')
    for source in sorted((repo_root / "src/marketplace_contracts").glob("*.py")):
        emitted.update(pattern.findall(source.read_text(encoding="utf-8")))
    fixtures = json.loads((repo_root / "fixtures/cases.json").read_text(encoding="utf-8"))
    expected = {code for case in fixtures for code in case["expect"]}
    assert emitted.issubset(REASON_CODES)
    assert expected.issubset(REASON_CODES)


def test_ci_runs_documented_gate_without_secrets(repo_root: Path) -> None:
    workflow_path = repo_root / ".github/workflows/validate.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["local-gate"]
    commands = [step.get("run") for step in job["steps"] if "run" in step]
    assert "uv sync --locked --all-groups --no-install-project" in commands
    assert "PYTHONPATH=src uv run --no-sync python scripts/gate.py" in commands
    assert "secrets" not in workflow_path.read_text(encoding="utf-8").casefold()


def test_cli_output_and_exit_codes_are_deterministic(repo_root: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "marketplace_contracts.cli",
        "validate",
        "--kind",
        "theme",
        "docs/examples/v1/theme.json",
    ]
    first = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, check=False)
    second = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, check=False)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["valid"] is True

    invalid = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketplace_contracts.cli",
            "validate",
            "--kind",
            "manifest",
            "fixtures/invalid/malformed.json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode == 1
    assert {issue["code"] for issue in json.loads(invalid.stdout)["issues"]} == {"JSON_INVALID"}


@pytest.mark.parametrize(
    ("api_range", "expected_codes"),
    [
        (">=2.0.0 <1.0.0", {"SEMVER_RANGE_INVALID"}),
        (">=1.0.0 <1.0.0", {"SEMVER_RANGE_INVALID"}),
        ("*", {"SCHEMA_INVALID", "SEMVER_RANGE_INVALID"}),
    ],
)
def test_panel_range_cli_fails_closed(
    repo_root: Path,
    tmp_path: Path,
    api_range: str,
    expected_codes: set[str],
) -> None:
    panel = json.loads((repo_root / "docs/examples/v1/panel.json").read_text(encoding="utf-8"))
    panel["slots"][0]["acceptedWidget"]["apiRange"] = api_range
    panel_path = tmp_path / "panel.json"
    panel_path.write_bytes(canonical_json_bytes(panel))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketplace_contracts.cli",
            "--repo-root",
            str(repo_root),
            "validate",
            "--kind",
            "panel",
            str(panel_path),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert {issue["code"] for issue in json.loads(result.stdout)["issues"]} == expected_codes


def test_snapshot_cli_rejects_every_generated_file_binding(repo_root: Path, tmp_path: Path) -> None:
    index, revocations, _ = compose_release(repo_root)
    revocations_path = tmp_path / "revocations.json"
    revocations_path.write_bytes(canonical_json_bytes(revocations))
    for case_index, binding_path in enumerate(GENERATED_FILE_BINDINGS):
        altered = json.loads(json.dumps(index))
        binding = altered["generatedFrom"]
        for segment in binding_path:
            binding = binding[segment]
        binding["sha256"] = "0" * 64
        index_path = tmp_path / f"index-{case_index}.json"
        index_path.write_bytes(canonical_json_bytes(altered))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "marketplace_contracts.cli",
                "--repo-root",
                str(repo_root),
                "snapshot",
                str(index_path),
                str(revocations_path),
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, binding_path
        assert "DIGEST_MISMATCH" in {issue["code"] for issue in json.loads(result.stdout)["issues"]}


def test_snapshot_cli_rejects_every_fixed_generated_identity(repo_root: Path, tmp_path: Path) -> None:
    index, revocations, _ = compose_release(repo_root)
    revocations_path = tmp_path / "fixed-revocations.json"
    revocations_path.write_bytes(canonical_json_bytes(revocations))
    for case_index, (identity_path, value) in enumerate(GENERATED_FIXED_IDENTITY_MUTATIONS):
        altered = json.loads(json.dumps(index))
        parent = altered["generatedFrom"]
        for segment in identity_path[:-1]:
            parent = parent[segment]
        parent[identity_path[-1]] = value
        index_path = tmp_path / f"fixed-index-{case_index}.json"
        index_path.write_bytes(canonical_json_bytes(altered))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "marketplace_contracts.cli",
                "--repo-root",
                str(repo_root),
                "snapshot",
                str(index_path),
                str(revocations_path),
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, identity_path
        assert "SOURCE_IDENTITY_MISMATCH" in {
            issue["code"] for issue in json.loads(result.stdout)["issues"]
        }
