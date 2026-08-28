from __future__ import annotations

import copy
from pathlib import Path

import pytest

from marketplace_contracts.builder import compose_release
from marketplace_contracts.fixtures import validate_fixtures
from marketplace_contracts.io import MAX_JSON_BYTES, ContractError, load_json, reference_issue
from marketplace_contracts.validator import validate_document, validate_package, validate_schemas


def test_schemas_and_fixture_matrix_pass(repo_root: Path) -> None:
    assert validate_schemas(repo_root).valid
    assert validate_fixtures(repo_root).valid


@pytest.mark.parametrize(
    "manifest",
    [
        "packages/org.catppuccin.mocha/1.0.0/manifest.json",
        "packages/com.mastylolabs.clock/1.0.0/manifest.json",
    ],
)
def test_representative_packages_pass_same_validator(repo_root: Path, manifest: str) -> None:
    record, report = validate_package(repo_root, repo_root / manifest)
    assert report.valid
    assert record is not None


def test_release_has_exact_two_authorized_representatives(repo_root: Path) -> None:
    _, _, records = compose_release(repo_root)
    assert {record.identity[0] for record in records} == {
        "org.catppuccin.mocha",
        "com.mastylolabs.clock",
    }


def test_json_file_size_boundary(tmp_path: Path) -> None:
    exact = tmp_path / "exact.json"
    exact.write_bytes(b'{"x":"' + b"a" * (MAX_JSON_BYTES - 8) + b'"}')
    assert isinstance(load_json(exact), dict)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(exact.read_bytes() + b" ")
    with pytest.raises(ContractError) as failure:
        load_json(oversized)
    assert {issue.code for issue in failure.value.issues} == {"LIMIT_EXCEEDED"}


def test_reference_length_and_path_boundaries() -> None:
    assert reference_issue("a" * 240, "$") is None
    assert reference_issue("a" * 241, "$").code == "LIMIT_EXCEEDED"  # type: ignore[union-attr]
    assert reference_issue("../outside", "$").code == "PATH_TRAVERSAL"  # type: ignore[union-attr]
    assert reference_issue("/absolute", "$").code == "PATH_ABSOLUTE"  # type: ignore[union-attr]
    assert reference_issue("javascript:run", "$").code == "URI_UNSAFE"  # type: ignore[union-attr]


def test_catalog_entry_count_boundary(repo_root: Path) -> None:
    catalog, _, _ = compose_release(repo_root)
    template = copy.deepcopy(catalog["entries"][0])
    entries = []
    for index in range(512):
        entry = copy.deepcopy(template)
        entry["displayName"] = f"Package {index:03d}"
        entry["packageId"] = f"org.example.p{index:03d}"
        entry["manifestUri"] = f"packages/org.example.p{index:03d}/1.0.0/manifest.json"
        entries.append(entry)
    catalog["entries"] = entries
    assert validate_document(repo_root, "catalog", catalog, target="catalog-512").valid
    catalog["entries"].append(copy.deepcopy(entries[-1]))
    report = validate_document(repo_root, "catalog", catalog, target="catalog-513")
    assert "LIMIT_EXCEEDED" in {issue.code for issue in report.issues}


def test_panel_slot_and_occupancy_boundaries(repo_root: Path) -> None:
    panel = load_json(repo_root / "docs/examples/v1/panel.json")
    assert isinstance(panel, dict)
    template = panel["slots"][0]
    panel["slots"] = []
    for index in range(16):
        slot = copy.deepcopy(template)
        slot["id"] = f"slot-{index}"
        panel["slots"].append(slot)
    panel["slots"][0]["occupancy"]["maximum"] = 8
    assert validate_document(repo_root, "panel", panel, target="panel-16").valid
    panel["slots"].append(copy.deepcopy(panel["slots"][-1]))
    panel["slots"][-1]["id"] = "slot-16"
    report = validate_document(repo_root, "panel", panel, target="panel-17")
    assert "LIMIT_EXCEEDED" in {issue.code for issue in report.issues}
    panel["slots"] = panel["slots"][:16]
    panel["slots"][0]["occupancy"]["maximum"] = 9
    report = validate_document(repo_root, "panel", panel, target="occupancy-9")
    assert "LIMIT_EXCEEDED" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("api_range", [">=2.0.0 <1.0.0", ">=1.0.0 <1.0.0", "*"])
def test_panel_widget_api_range_must_be_a_nonempty_interval(repo_root: Path, api_range: str) -> None:
    panel = load_json(repo_root / "docs/examples/v1/panel.json")
    assert isinstance(panel, dict)
    panel["slots"][0]["acceptedWidget"]["apiRange"] = api_range
    report = validate_document(repo_root, "panel", panel, target="panel-api-range")
    assert "SEMVER_RANGE_INVALID" in {issue.code for issue in report.issues}


def test_validator_report_is_deterministic(repo_root: Path) -> None:
    theme = load_json(repo_root / "docs/examples/v1/theme.json")
    assert isinstance(theme, dict)
    theme["css"] = "@import url(https://example.invalid)"
    first = validate_document(repo_root, "theme", theme, target="determinism").as_dict()
    second = validate_document(repo_root, "theme", theme, target="determinism").as_dict()
    assert first == second
