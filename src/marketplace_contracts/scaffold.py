from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .io import canonical_json_bytes, read_bounded_file, sha256_bytes
from .model import ContractError, ValidationIssue
from .validator import TYPE_DELIVERY, validate_package

_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+){2,}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SAFE_NAME = re.compile(r"^[^<>\x00-\x1f]{1,80}$")


def _payload(uri: str, role: str, media_type: str, content: bytes) -> dict[str, Any]:
    return {"mediaType": media_type, "role": role, "sha256": sha256_bytes(content), "size": len(content), "uri": uri}


def _theme(package_id: str, version: str, name: str) -> bytes:
    return canonical_json_bytes({
        "metadata": {"appearance": "dark", "attribution": "Original private starter palette authored for this package.", "name": name},
        "packageId": package_id, "packageVersion": version, "schemaVersion": 1,
        "tokens": {
            "accent": "#00ffff", "background": "#000000", "border": "#ffffff", "danger": "#ff4d4d",
            "focus": "#ffff00", "info": "#66ccff", "selection": "#444444", "success": "#00ff66",
            "surface": "#050505", "surfaceElevated": "#101010", "syntaxComment": "#c7c7c7",
            "syntaxKeyword": "#ff66ff", "syntaxNumber": "#ffff00", "syntaxString": "#00ff66",
            "terminalBackground": "#000000", "terminalForeground": "#ffffff", "textMuted": "#c7c7c7",
            "textPrimary": "#ffffff", "textSecondary": "#eeeeee", "warning": "#ffff00",
        },
        "type": "theme",
    })


def _widget(package_id: str, version: str, package_root: str) -> tuple[bytes, bytes]:
    source = (
        "export interface StarterWidgetProps { readonly label: string }\n\n"
        "export function StarterWidget({ label }: StarterWidgetProps) {\n"
        "  return { kind: \"host-bundled-review-source\", label } as const\n"
        "}\n"
    ).encode()
    data = canonical_json_bytes({
        "availability": "requires-compatible-command-build",
        "buildAssociation": {"inventoryIdentity": f"{package_id}@{version}", "mode": "exact-host-build-inventory", "state": "not-bundled"},
        "dataInputs": [], "deliveryMode": "host-bundled-source", "packageId": package_id, "packageVersion": version,
        "placement": {"preferredRoles": ["sidebar-primary"], "size": {"maxHeight": 4, "maxWidth": 6, "minHeight": 1, "minWidth": 2}},
        "runtimeDownload": False, "runtimeLoading": "prohibited", "schemaVersion": 1,
        "sourceAssociation": {"sourceRevision": "private-scaffold-v1", "sourceSha256": sha256_bytes(source), "sourceUri": f"{package_root}/src/Widget.ts"},
        "type": "widget",
    })
    return data, source


def _panel(package_id: str, version: str) -> bytes:
    return canonical_json_bytes({
        "automaticPlacement": False, "layoutRole": "right-sidebar", "packageId": package_id,
        "packageVersion": version, "schemaVersion": 1,
        "slots": [{
            "acceptedWidget": {"apiRange": ">=1.0.0 <2.0.0", "typeContractMajor": 1},
            "behaviorHints": ["resizable", "collapsible", "scrollable"], "conflicts": [], "id": "primary",
            "occupancy": {"maximum": 8, "minimum": 0, "overflow": "host-scroll"}, "role": "sidebar-primary",
            "size": {"maxHeight": 12, "maxWidth": 8, "minHeight": 1, "minWidth": 2},
        }],
        "type": "panel",
    })


def scaffold_package(repo_root: Path, package_type: str, package_id: str, name: str, version: str = "1.0.0") -> dict[str, str]:
    if package_type not in TYPE_DELIVERY:
        raise ContractError([ValidationIssue("TYPE_UNSUPPORTED", "packageType", "scaffold type must be theme, widget, or panel")])
    if not _PACKAGE_ID.fullmatch(package_id) or len(package_id) > 96:
        raise ContractError([ValidationIssue("PACKAGE_ID_INVALID", "packageId", "package ID must be a bounded lowercase reverse-domain identity")])
    if not _SEMVER.fullmatch(version):
        raise ContractError([ValidationIssue("SEMVER_INVALID", "version", "package version must be strict X.Y.Z")])
    if not _SAFE_NAME.fullmatch(name) or not name.strip():
        raise ContractError([ValidationIssue("PACKAGE_NAME_INVALID", "name", "package name must be bounded plain text")])

    packages_root = (repo_root / "packages").resolve()
    destination = packages_root / package_id / version
    if destination.exists() or destination.is_symlink():
        raise ContractError([ValidationIssue("SCAFFOLD_EXISTS", destination.relative_to(repo_root).as_posix(), "scaffold destination already exists; no files were overwritten")])
    destination.parent.mkdir(parents=True, exist_ok=True)
    package_root = destination.relative_to(repo_root).as_posix()
    readme = (
        f"# {name}\n\nPrivate accepted-unpublished {package_type} starter for `{package_id}@{version}`.\n\n"
        "This package is not publicly installable. Widgets remain host-bundled source only; Themes and Panels remain declarative data only.\n"
    ).encode()
    license_bytes = read_bounded_file(repo_root / "LICENSE", max_bytes=64 * 1024, target="LICENSE")
    files: dict[str, bytes] = {"README.md": readme}
    data_name = f"{package_type}.json"
    if package_type == "theme":
        files[data_name] = _theme(package_id, version, name)
    elif package_type == "widget":
        files[data_name], files["src/Widget.ts"] = _widget(package_id, version, package_root)
    else:
        files[data_name] = _panel(package_id, version)

    payloads = [_payload(f"{package_root}/{data_name}", f"{package_type}-data", "application/json", files[data_name])]
    if package_type == "widget":
        payloads.append(_payload(f"{package_root}/src/Widget.ts", "widget-source", "text/typescript", files["src/Widget.ts"]))
    payloads.extend([
        _payload(f"{package_root}/README.md", "documentation", "text/markdown", readme),
        _payload("LICENSE", "license", "text/plain", license_bytes),
    ])
    description = {
        "theme": "Private declarative Theme starter with inert bounded color tokens.",
        "widget": "Private review source for a Widget that must be compiled into an exact compatible Command build.",
        "panel": "Private declarative Panel starter with explicit host-selected placement only.",
    }[package_type]
    manifest = {
        "capabilities": [],
        "compatibility": {
            "commandHostRange": ">=0.1.0 <1.0.0", "conflicts": [], "dependencies": [], "extensionApiRange": ">=1.0.0 <2.0.0",
            "platforms": ["host-build-defined" if package_type == "widget" else "platform-neutral"], "typeContractMajor": 1,
        },
        "contract": {"artifactId": "172X-MKT-MANIFEST-001", "version": "v1"},
        "developerDocs": [{"contractVersion": "v1", "sha256": sha256_bytes(readme), "topic": f"{name} private starter", "uri": f"{package_root}/README.md"}],
        "license": {"file": {"sha256": sha256_bytes(license_bytes), "uri": "LICENSE"}, "spdx": "Apache-2.0", "thirdPartyNotices": []},
        "lifecycle": {
            "dataRetention": "host-owned-preserve-until-explicit-purge" if package_type == "widget" else "not-applicable",
            "rollback": "host-build-rollback-only" if package_type == "widget" else "host-owned-last-valid", "state": "accepted-unpublished",
        },
        "package": {"deliveryMode": TYPE_DELIVERY[package_type], "description": description, "id": package_id, "name": name, "summary": f"Private {name} {package_type} starter.", "type": package_type, "version": version},
        "payloads": payloads, "schemaVersion": 1,
        "source": {"authors": [{"evidence": "declared", "name": "Mastylo Labs LLC", "role": "package-author"}], "repositoryUri": f"{package_root}/README.md", "revision": "private-scaffold-v1", "upstream": []},
        "trust": {"authorEvidence": "declared", "maintenance": "limited", "official": False, "provenance": "digest-recorded", "review": "unreviewed", "security": "not-reviewed", "sourceAvailability": "linked"},
    }
    files["manifest.json"] = canonical_json_bytes(manifest)

    staging = Path(tempfile.mkdtemp(prefix=f".{version}.scaffold-", dir=destination.parent))
    try:
        for relative, content in sorted(files.items()):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.replace(staging, destination)
        record, report = validate_package(repo_root, destination / "manifest.json")
        if record is None or not report.valid:
            shutil.rmtree(destination)
            raise ContractError(report.issues)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {"manifest": f"{package_root}/manifest.json", "package": f"{package_id}@{version}", "status": "scaffolded", "type": package_type}
