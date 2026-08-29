from __future__ import annotations

from pathlib import Path

import pytest

from marketplace_contracts.io import load_json
from marketplace_contracts.validator import validate_package


PROPOSALS = (
    "packages/org.catppuccin.latte/1.0.0/manifest.json",
    "packages/org.catppuccin.frappe/1.0.0/manifest.json",
    "packages/org.catppuccin.macchiato/1.0.0/manifest.json",
    "packages/com.mastylolabs.high-contrast/1.0.0/manifest.json",
    "packages/com.mastylolabs.world-orbit/1.0.0/manifest.json",
    "packages/com.mastylolabs.orbit-agenda/1.0.0/manifest.json",
    "packages/com.mastylolabs.system/1.0.0/manifest.json",
    "packages/com.mastylolabs.environment/1.0.0/manifest.json",
    "packages/com.mastylolabs.intelligence-panel/1.0.0/manifest.json",
)


@pytest.mark.parametrize("manifest_uri", PROPOSALS)
def test_private_package_proposal_is_strictly_valid_and_unpublished(repo_root: Path, manifest_uri: str) -> None:
    record, report = validate_package(repo_root, repo_root / manifest_uri)
    assert report.valid
    assert record is not None
    assert record.manifest["lifecycle"]["state"] == "accepted-unpublished"


def test_proposals_are_not_silently_promoted_into_signed_release(repo_root: Path) -> None:
    source = load_json(repo_root / "registry/source/v1/release.json", repo_root=repo_root)
    assert isinstance(source, dict)
    release_uris = {item["manifestUri"] for item in source["packages"]}
    assert release_uris.isdisjoint(PROPOSALS)


@pytest.mark.parametrize("flavor", ["latte", "frappe", "macchiato"])
def test_catppuccin_proposals_pin_upstream_license_and_revision(repo_root: Path, flavor: str) -> None:
    manifest = load_json(repo_root / f"packages/org.catppuccin.{flavor}/1.0.0/manifest.json", repo_root=repo_root)
    assert isinstance(manifest, dict)
    assert manifest["license"]["spdx"] == "MIT"
    assert manifest["source"]["upstream"] == [{
        "attribution": manifest["source"]["upstream"][0]["attribution"],
        "licenseSpdx": "MIT",
        "project": "Catppuccin Palette",
        "repositoryIdentity": "github:catppuccin/palette",
        "revision": "07d02aa110ef9eb7e7427afca5c73ba9cf7f8ebd",
    }]


@pytest.mark.parametrize(("flavor", "appearance"), [
    ("latte", "light"),
    ("frappe", "dark"),
    ("macchiato", "dark"),
])
def test_catppuccin_flavor_appearance_is_semantically_pinned(repo_root: Path, flavor: str, appearance: str) -> None:
    theme = load_json(repo_root / f"packages/org.catppuccin.{flavor}/1.0.0/theme.json", repo_root=repo_root)
    assert isinstance(theme, dict)
    assert theme["metadata"]["appearance"] == appearance
