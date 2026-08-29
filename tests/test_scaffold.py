from __future__ import annotations

from pathlib import Path

import pytest

from marketplace_contracts.cli import main as cli_main
from marketplace_contracts.model import ContractError
from marketplace_contracts.scaffold import scaffold_package
from marketplace_contracts.validator import validate_package


@pytest.mark.parametrize("package_type", ["theme", "widget", "panel"])
def test_scaffold_is_deterministic_strict_and_package_valid(contract_sandbox: Path, tmp_path: Path, package_type: str) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for target in (first, second):
        target.mkdir()
        (target / "packages").mkdir()
        (target / "schemas").symlink_to(contract_sandbox / "schemas", target_is_directory=True)
        (target / "LICENSE").write_bytes((contract_sandbox / "LICENSE").read_bytes())
    package_id = f"com.mastylolabs.scaffold-{package_type}"
    first_result = scaffold_package(first, package_type, package_id, f"Starter {package_type.title()}")
    second_result = scaffold_package(second, package_type, package_id, f"Starter {package_type.title()}")
    first_root = first / "packages" / package_id / "1.0.0"
    second_root = second / "packages" / package_id / "1.0.0"
    assert first_result == second_result
    assert {item.relative_to(first_root).as_posix(): item.read_bytes() for item in first_root.rglob("*") if item.is_file()} == {
        item.relative_to(second_root).as_posix(): item.read_bytes() for item in second_root.rglob("*") if item.is_file()
    }
    record, report = validate_package(first, first_root / "manifest.json")
    assert report.valid
    assert record is not None


def test_scaffold_never_overwrites_existing_destination(contract_sandbox: Path) -> None:
    result = scaffold_package(contract_sandbox, "theme", "com.mastylolabs.no-overwrite", "No Overwrite")
    manifest = contract_sandbox / result["manifest"]
    before = manifest.read_bytes()
    with pytest.raises(ContractError) as failure:
        scaffold_package(contract_sandbox, "theme", "com.mastylolabs.no-overwrite", "Changed")
    assert {issue.code for issue in failure.value.issues} == {"SCAFFOLD_EXISTS"}
    assert manifest.read_bytes() == before


def test_scaffold_cli_rejects_unsafe_identity_without_writing(contract_sandbox: Path) -> None:
    assert cli_main(["--repo-root", str(contract_sandbox), "scaffold", "--type", "panel", "--id", "../escape", "--name", "Escape"]) == 1
    assert not (contract_sandbox.parent / "escape").exists()
