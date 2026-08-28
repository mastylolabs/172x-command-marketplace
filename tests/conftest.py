from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def contract_sandbox(tmp_path: Path, repo_root: Path) -> Path:
    for relative in (
        "contracts",
        "schemas",
        "packages",
        "registry/source",
        "docs/architecture",
        "docs/contracts/v1",
    ):
        shutil.copytree(repo_root / relative, tmp_path / relative)
    shutil.copy2(repo_root / "LICENSE", tmp_path / "LICENSE")
    return tmp_path
