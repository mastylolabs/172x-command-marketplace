from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from . import VERSION
from .io import (
    MAX_PACKAGE_BYTES,
    MAX_PAYLOAD_BYTES,
    load_json,
    load_json_bytes,
    load_json_with_bytes,
    logical_target,
    plain_text_issue,
    read_bounded_file,
    reference_issue,
    regular_file_size,
    resolve_reference,
    sha256_bytes,
    sha256_file,
)
from .model import ContractError, PackageRecord, ValidationIssue, ValidationReport

SCHEMA_FILES = {
    "catalog": "schemas/v1/catalog.schema.json",
    "revocations": "schemas/v1/revocations.schema.json",
    "manifest": "schemas/v1/manifest.schema.json",
    "theme": "schemas/v1/theme.schema.json",
    "widget": "schemas/v1/widget.schema.json",
    "panel": "schemas/v1/panel.schema.json",
}

TYPE_DELIVERY = {
    "theme": "declarative-data",
    "widget": "host-bundled-source",
    "panel": "declarative-data",
}

IMPLEMENTATION_CONTEXT = {
    "commandHostVersion": "0.1.0",
    "extensionApiVersion": "1.0.0",
    "platforms": {"platform-neutral", "host-build-defined"},
    "typeContractMajor": 1,
}

SOURCE_DESCRIPTOR_URI = "registry/source/v1/release.json"
CONTRACT_RELEASE_URI = "contracts/v1/release.json"
DEVELOPER_DOCS_URI = "docs/contracts/v1/index.md"
ARCHITECTURE_URI = "docs/architecture/172x-command-marketplace-and-integration-architecture-v0.1.md"
EXPECTED_SOURCE_IDENTITY = "172X-W1-PRIVATE-CONTRACTS-v0.1"
EXPECTED_ARCHITECTURE_BINDING = {
    "artifactId": "DA-W0-ARCH-001",
    "sha256": "4d0d3ab71850ce2466554403c180f9030d035261cba857eb0762cf990a6723ab",
    "version": "v0.1",
}
EXPECTED_GATE_BINDING = {
    "artifactId": "172X-GATE-CMD-W1-001",
    "sha256": "53995489c5cbc25adde4e3e32a9635de65d96f7564b355fefdb20b4f7b3bc567",
    "version": "v0.1",
}

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_RANGE = re.compile(
    r"^>=((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)) "
    r"<((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$"
)
_REFERENCE_KEYS = {"uri", "manifestUri", "revocationsUri", "sourceUri", "repositoryUri"}
_DISPLAY_TEXT_KEYS = {
    "attribution",
    "description",
    "displayName",
    "name",
    "project",
    "purpose",
    "repositoryIdentity",
    "summary",
    "topic",
}
_THEME_ACTIVE_KEYS = {"css", "script", "javascript", "font", "fonts", "url", "asset", "assets", "behavior"}
_PANEL_EXECUTABLE_KEYS = {"code", "controller", "script", "callback", "component", "render", "module"}
_UNSAFE_TEXT = re.compile(r"(?i)(javascript:|data:|file:|<script|@import|@font-face|url\s*\()")
_WIDGET_SOURCE_FORBIDDEN = re.compile(
    r"(?i)(\b(?:eval|Function|fetch|XMLHttpRequest|WebSocket|require)\s*\(|"
    r"\bimport\s*\(|__TAURI__|@tauri-apps|child_process|node:fs|\bprocess\s*\.)"
)


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _range_contains(value: str, version: str) -> bool:
    match = _RANGE.fullmatch(value)
    if not match:
        return False
    lower, upper = (_version_tuple(match.group(index)) for index in (1, 2))
    candidate = _version_tuple(version)
    return lower < upper and lower <= candidate < upper


def _range_is_nonempty(value: str) -> bool:
    match = _RANGE.fullmatch(value)
    if not match:
        return False
    lower, upper = (_version_tuple(match.group(index)) for index in (1, 2))
    return lower < upper


def _json_path(parts: Iterable[object]) -> str:
    result = "$"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


def _schema_issues(repo_root: Path, kind: str, value: dict[str, Any] | list[Any]) -> list[ValidationIssue]:
    schema_value = load_json(repo_root / SCHEMA_FILES[kind], repo_root=repo_root)
    assert isinstance(schema_value, dict)
    validator = Draft202012Validator(schema_value)
    issues: list[ValidationIssue] = []
    messages = {
        "additionalProperties": "document contains an unknown field",
        "const": "value differs from the required v1 constant",
        "enum": "value is outside the v1 allowlist",
        "maxItems": "collection exceeds the v1 item limit",
        "maxLength": "text exceeds the v1 length limit",
        "maximum": "number exceeds the v1 maximum",
        "minItems": "collection is below the v1 item minimum",
        "minLength": "text is below the v1 length minimum",
        "minimum": "number is below the v1 minimum",
        "pattern": "value does not match the required v1 format",
        "required": "required field is missing",
        "type": "value has the wrong JSON type",
        "uniqueItems": "collection items must be unique",
    }
    for error in sorted(
        validator.iter_errors(value),
        key=lambda item: (_json_path(item.absolute_path), str(item.validator)),
    ):
        if error.validator == "additionalProperties":
            code = "SCHEMA_UNKNOWN_FIELD"
        elif error.validator in {"maxItems", "maxLength", "maximum"}:
            code = "LIMIT_EXCEEDED"
        else:
            code = "SCHEMA_INVALID"
        issues.append(
            ValidationIssue(
                code,
                _json_path(error.absolute_path),
                messages.get(str(error.validator), "value does not satisfy the v1 schema"),
            )
        )
    return issues


def validate_schemas(repo_root: Path) -> ValidationReport:
    issues: list[ValidationIssue] = []
    for kind, relative in sorted(SCHEMA_FILES.items()):
        path = repo_root / relative
        try:
            schema = load_json(path, repo_root=repo_root)
            if not isinstance(schema, dict):
                raise ValueError("schema must be an object")
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # jsonschema exposes several schema-specific exception classes
            if isinstance(exc, ContractError):
                issues.extend(exc.issues)
            else:
                issues.append(
                    ValidationIssue("SCHEMA_DEFINITION_INVALID", relative, "schema definition is invalid")
                )
    return ValidationReport("schemas/v1", tuple(sorted(issues, key=lambda item: (item.path, item.code, item.message))))


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str | None, Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{path}.{key}"
            yield child, key, value[key]
            yield from _walk(value[key], child)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            child = f"{path}[{index}]"
            yield child, None, child_value
            yield from _walk(child_value, child)


def _common_preflight(value: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        return issues
    if "schemaVersion" in value and value["schemaVersion"] != 1:
        issues.append(
            ValidationIssue("CONTRACT_MAJOR_UNSUPPORTED", "$.schemaVersion", "only contract major 1 is supported")
        )
    for path, key, child in _walk(value):
        if key in _REFERENCE_KEYS or (isinstance(key, str) and key.endswith("Uri")):
            issue = reference_issue(child, path)
            if issue:
                issues.append(issue)
        if key in _DISPLAY_TEXT_KEYS:
            issue = plain_text_issue(child, path)
            if issue:
                issues.append(issue)
    return issues


def _manifest_preflight(value: Any) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return []
    issues: list[ValidationIssue] = []
    package = value.get("package")
    if not isinstance(package, dict):
        return issues
    package_type = package.get("type")
    delivery = package.get("deliveryMode")
    if package_type not in TYPE_DELIVERY:
        issues.append(ValidationIssue("TYPE_UNSUPPORTED", "$.package.type", "v1 supports theme, widget, and panel only"))
    if delivery not in set(TYPE_DELIVERY.values()):
        issues.append(
            ValidationIssue(
                "DELIVERY_UNSUPPORTED",
                "$.package.deliveryMode",
                "v1 supports declarative-data and host-bundled-source only",
            )
        )
    if package_type in TYPE_DELIVERY and delivery in set(TYPE_DELIVERY.values()):
        if TYPE_DELIVERY[package_type] != delivery:
            issues.append(
                ValidationIssue(
                    "TYPE_DELIVERY_MISMATCH",
                    "$.package.deliveryMode",
                    "package type requires its fixed v1 delivery mode",
                )
            )
    version = package.get("version")
    if isinstance(version, str) and not _SEMVER.fullmatch(version):
        issues.append(ValidationIssue("SEMVER_INVALID", "$.package.version", "package version must be strict X.Y.Z"))
    compatibility = value.get("compatibility")
    if isinstance(compatibility, dict):
        for field in ("commandHostRange", "extensionApiRange"):
            candidate = compatibility.get(field)
            if isinstance(candidate, str) and not _RANGE.fullmatch(candidate):
                issues.append(
                    ValidationIssue(
                        "SEMVER_RANGE_INVALID", f"$.compatibility.{field}", "v1 ranges must use >=X.Y.Z <X.Y.Z"
                    )
                )
        host_range = compatibility.get("commandHostRange")
        if isinstance(host_range, str) and _RANGE.fullmatch(host_range):
            if not _range_contains(host_range, IMPLEMENTATION_CONTEXT["commandHostVersion"]):
                issues.append(
                    ValidationIssue(
                        "INCOMPATIBLE_HOST",
                        "$.compatibility.commandHostRange",
                        "range excludes the v1 private validation host context",
                    )
                )
        api_range = compatibility.get("extensionApiRange")
        if isinstance(api_range, str) and _RANGE.fullmatch(api_range):
            if not _range_contains(api_range, IMPLEMENTATION_CONTEXT["extensionApiVersion"]):
                issues.append(
                    ValidationIssue(
                        "INCOMPATIBLE_EXTENSION_API",
                        "$.compatibility.extensionApiRange",
                        "range excludes the v1 private validation API context",
                    )
                )
        major = compatibility.get("typeContractMajor")
        if isinstance(major, int) and major != IMPLEMENTATION_CONTEXT["typeContractMajor"]:
            issues.append(
                ValidationIssue(
                    "INCOMPATIBLE_TYPE_CONTRACT",
                    "$.compatibility.typeContractMajor",
                    "type contract major is unsupported by v1",
                )
            )
        platforms = compatibility.get("platforms")
        if isinstance(platforms, list) and not set(platforms).intersection(IMPLEMENTATION_CONTEXT["platforms"]):
            issues.append(
                ValidationIssue(
                    "INCOMPATIBLE_PLATFORM",
                    "$.compatibility.platforms",
                    "no platform marker matches the v1 private validation context",
                )
            )
    return issues


def _theme_preflight(value: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path, key, child in _walk(value):
        if isinstance(key, str) and key.lower() in _THEME_ACTIVE_KEYS:
            issues.append(ValidationIssue("THEME_ACTIVE_CONTENT", path, "active Theme content is prohibited"))
        if isinstance(child, str) and _UNSAFE_TEXT.search(child):
            issues.append(ValidationIssue("THEME_ACTIVE_CONTENT", path, "active or external Theme value is prohibited"))
    return issues


def _panel_preflight(value: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path, key, child in _walk(value):
        if isinstance(key, str) and key.lower() in _PANEL_EXECUTABLE_KEYS:
            issues.append(ValidationIssue("PANEL_EXECUTABLE_CONTENT", path, "Panel code/controller content is prohibited"))
        if key == "automaticPlacement" and child is not False:
            issues.append(
                ValidationIssue("PANEL_AUTOMATIC_PLACEMENT", path, "Panel packages cannot select or place Widgets")
            )
        if isinstance(child, str) and _UNSAFE_TEXT.search(child):
            issues.append(ValidationIssue("PANEL_EXECUTABLE_CONTENT", path, "active or external Panel value is prohibited"))
    if isinstance(value, dict) and isinstance(value.get("slots"), list):
        ids = [slot.get("id") for slot in value["slots"] if isinstance(slot, dict)]
        if len(ids) != len(set(ids)):
            issues.append(ValidationIssue("DUPLICATE_SLOT", "$.slots", "Panel slot IDs must be unique"))
        for index, slot in enumerate(value["slots"]):
            if not isinstance(slot, dict):
                continue
            accepted_widget = slot.get("acceptedWidget")
            if isinstance(accepted_widget, dict):
                api_range = accepted_widget.get("apiRange")
                if isinstance(api_range, str) and not _range_is_nonempty(api_range):
                    issues.append(
                        ValidationIssue(
                            "SEMVER_RANGE_INVALID",
                            f"$.slots[{index}].acceptedWidget.apiRange",
                            "Panel accepted Widget API range must use >=X.Y.Z <X.Y.Z with lower bound below upper bound",
                        )
                    )
            occupancy = slot.get("occupancy")
            if isinstance(occupancy, dict) and isinstance(occupancy.get("minimum"), int) and isinstance(
                occupancy.get("maximum"), int
            ):
                if occupancy["minimum"] > occupancy["maximum"]:
                    issues.append(
                        ValidationIssue(
                            "PANEL_BOUNDS_INVALID",
                            f"$.slots[{index}].occupancy",
                            "minimum occupancy cannot exceed maximum",
                        )
                    )
            size = slot.get("size")
            if isinstance(size, dict):
                for axis in ("Width", "Height"):
                    minimum = size.get(f"min{axis}")
                    maximum = size.get(f"max{axis}")
                    if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
                        issues.append(
                            ValidationIssue(
                                "PANEL_BOUNDS_INVALID",
                                f"$.slots[{index}].size",
                                f"min{axis} cannot exceed max{axis}",
                            )
                        )
    return issues


def _widget_preflight(value: Any) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return []
    issues: list[ValidationIssue] = []
    if value.get("runtimeDownload") is not False:
        issues.append(
            ValidationIssue("WIDGET_RUNTIME_DOWNLOAD", "$.runtimeDownload", "Widget runtime download must be false")
        )
    if value.get("runtimeLoading") not in {None, "prohibited"}:
        issues.append(
            ValidationIssue("WIDGET_RUNTIME_DOWNLOAD", "$.runtimeLoading", "Widget runtime loading is prohibited")
        )
    placement = value.get("placement")
    if isinstance(placement, dict) and isinstance(placement.get("size"), dict):
        size = placement["size"]
        for axis in ("Width", "Height"):
            minimum = size.get(f"min{axis}")
            maximum = size.get(f"max{axis}")
            if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
                issues.append(
                    ValidationIssue(
                        "WIDGET_BOUNDS_INVALID",
                        "$.placement.size",
                        f"min{axis} cannot exceed max{axis}",
                    )
                )
    return issues


def _catalog_preflight(value: Any) -> list[ValidationIssue]:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        return []
    issues: list[ValidationIssue] = []
    revision = value.get("revision")
    identities: set[tuple[object, object]] = set()
    for index, entry in enumerate(value["entries"]):
        if not isinstance(entry, dict):
            continue
        identity = (entry.get("packageId"), entry.get("packageVersion"))
        if identity in identities:
            issues.append(
                ValidationIssue("DUPLICATE_PACKAGE", f"$.entries[{index}]", "catalog package ID/version is duplicated")
            )
        identities.add(identity)
        if entry.get("revision") != revision:
            issues.append(
                ValidationIssue(
                    "CATALOG_MIXED_REVISION", f"$.entries[{index}].revision", "entry revision differs from catalog"
                )
            )
        package_type = entry.get("type")
        delivery = entry.get("deliveryMode")
        if package_type not in TYPE_DELIVERY:
            issues.append(ValidationIssue("TYPE_UNSUPPORTED", f"$.entries[{index}].type", "catalog type unsupported"))
        elif delivery != TYPE_DELIVERY[package_type]:
            issues.append(
                ValidationIssue(
                    "TYPE_DELIVERY_MISMATCH",
                    f"$.entries[{index}].deliveryMode",
                    "catalog type/delivery pair is unsupported",
                )
            )
    return issues


def _revocations_preflight(value: Any) -> list[ValidationIssue]:
    if not isinstance(value, dict) or not isinstance(value.get("revocations"), list):
        return []
    revision = value.get("revision")
    issues: list[ValidationIssue] = []
    identities: set[tuple[object, object]] = set()
    for index, item in enumerate(value["revocations"]):
        if not isinstance(item, dict):
            continue
        identity = (item.get("packageId"), item.get("packageVersion"))
        if identity in identities:
            issues.append(
                ValidationIssue("DUPLICATE_REVOCATION", f"$.revocations[{index}]", "revocation is duplicated")
            )
        identities.add(identity)
        if item.get("effectiveRevision") != revision:
            issues.append(
                ValidationIssue(
                    "REVOCATION_INCOHERENT",
                    f"$.revocations[{index}].effectiveRevision",
                    "revocation revision differs from set revision",
                )
            )
    return issues


def validate_document(repo_root: Path, kind: str, value: Any, *, target: str) -> ValidationReport:
    if kind not in SCHEMA_FILES:
        return ValidationReport(target, (ValidationIssue("KIND_UNSUPPORTED", "$", "document kind is unsupported"),))
    issues = _common_preflight(value)
    if kind == "manifest":
        issues.extend(_manifest_preflight(value))
    elif kind == "theme":
        issues.extend(_theme_preflight(value))
    elif kind == "panel":
        issues.extend(_panel_preflight(value))
    elif kind == "widget":
        issues.extend(_widget_preflight(value))
    elif kind == "catalog":
        issues.extend(_catalog_preflight(value))
    elif kind == "revocations":
        issues.extend(_revocations_preflight(value))
    issues.extend(_schema_issues(repo_root, kind, value))
    ordered = tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message)))
    return ValidationReport(target, ordered)


def validate_file(repo_root: Path, kind: str, path: Path) -> ValidationReport:
    target = logical_target(repo_root, path)
    try:
        value = load_json(path, repo_root=repo_root, target=target)
    except ContractError as exc:
        return ValidationReport(target, exc.issues)
    return validate_document(repo_root, kind, value, target=target)


def _identity_issues(manifest: dict[str, Any], payload: dict[str, Any], role: str) -> list[ValidationIssue]:
    package = manifest["package"]
    issues: list[ValidationIssue] = []
    for key, manifest_key in (("packageId", "id"), ("packageVersion", "version"), ("type", "type")):
        if payload.get(key) != package[manifest_key]:
            issues.append(
                ValidationIssue(
                    "IDENTITY_MISMATCH", f"$.payloads[{role}].{key}", "payload identity differs from manifest"
                )
            )
    return issues


def validate_package(repo_root: Path, manifest_path: Path) -> tuple[PackageRecord | None, ValidationReport]:
    relative_target = logical_target(repo_root, manifest_path)
    try:
        manifest_value, manifest_bytes = load_json_with_bytes(
            manifest_path,
            repo_root=repo_root,
            target=relative_target,
        )
    except ContractError as exc:
        return None, ValidationReport(relative_target, exc.issues)
    report = validate_document(repo_root, "manifest", manifest_value, target=relative_target)
    if not report.valid or not isinstance(manifest_value, dict):
        return None, report

    issues: list[ValidationIssue] = []
    payload_entries = manifest_value["payloads"]
    total_size = 0
    cumulative_limit_reached = False
    roles: dict[str, dict[str, Any]] = {}
    payload_bytes: dict[str, bytes] = {}
    for index, payload_entry in enumerate(payload_entries):
        role = payload_entry["role"]
        entry_path = f"$.payloads[{index}]"
        if role in roles:
            issues.append(ValidationIssue("DUPLICATE_PAYLOAD_ROLE", f"$.payloads[{index}].role", "payload role duplicated"))
        roles[role] = payload_entry
        uri = payload_entry["uri"]
        try:
            payload_path = resolve_reference(repo_root, uri, f"$.payloads[{index}].uri")
            size = regular_file_size(payload_path, target=f"{entry_path}.uri")
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        if size > MAX_PAYLOAD_BYTES:
            issues.append(
                ValidationIssue(
                    "LIMIT_EXCEEDED", f"{entry_path}.uri", "payload exceeds the v1 per-file byte limit"
                )
            )
            continue
        if cumulative_limit_reached or total_size + size > MAX_PACKAGE_BYTES:
            if not cumulative_limit_reached:
                issues.append(
                    ValidationIssue(
                        "LIMIT_EXCEEDED",
                        "$.payloads",
                        "package payloads exceed the v1 cumulative byte limit",
                    )
                )
            cumulative_limit_reached = True
            continue
        total_size += size
        if size != payload_entry["size"]:
            issues.append(ValidationIssue("SIZE_MISMATCH", entry_path, "payload size differs from manifest"))
        try:
            content = read_bounded_file(payload_path, max_bytes=MAX_PAYLOAD_BYTES, target=f"{entry_path}.uri")
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        payload_bytes[role] = content
        if sha256_bytes(content) != payload_entry["sha256"]:
            issues.append(ValidationIssue("DIGEST_MISMATCH", entry_path, "payload SHA-256 differs from manifest"))

    package_type = manifest_value["package"]["type"]
    expected_data_role = {"theme": "theme-data", "widget": "widget-data", "panel": "panel-data"}[package_type]
    if expected_data_role not in roles:
        issues.append(
                ValidationIssue("MANIFEST_PAYLOAD_MISSING", "$.payloads", "package type requires its v1 data payload")
        )
    if package_type == "widget" and "widget-source" not in roles:
        issues.append(
            ValidationIssue("MANIFEST_PAYLOAD_MISSING", "$.payloads", "widget requires review-only widget-source")
        )
    if package_type != "widget" and "widget-source" in roles:
        issues.append(
            ValidationIssue("TYPE_DELIVERY_MISMATCH", "$.payloads", "only Widget may carry review-only source")
        )

    data_entry = roles.get(expected_data_role)
    if data_entry and expected_data_role in payload_bytes:
        try:
            data_value = load_json_bytes(payload_bytes[expected_data_role], target=f"$.payloads.{expected_data_role}")
            data_report = validate_document(repo_root, package_type, data_value, target=data_entry["uri"])
            issues.extend(data_report.issues)
            if isinstance(data_value, dict):
                issues.extend(_identity_issues(manifest_value, data_value, expected_data_role))
                if package_type == "widget":
                    source_entry = roles.get("widget-source")
                    source_association = data_value.get("sourceAssociation", {})
                    if source_entry and (
                        source_association.get("sourceUri") != source_entry["uri"]
                        or source_association.get("sourceSha256") != source_entry["sha256"]
                    ):
                        issues.append(
                            ValidationIssue(
                                "IDENTITY_MISMATCH",
                                "$.sourceAssociation",
                                "Widget source association differs from manifest source payload",
                            )
                        )
                    if source_association.get("sourceRevision") != manifest_value["source"]["revision"]:
                        issues.append(
                            ValidationIssue(
                                "IDENTITY_MISMATCH",
                                "$.sourceAssociation.sourceRevision",
                                "Widget source revision differs from manifest source revision",
                            )
                        )
        except ContractError as exc:
            issues.extend(exc.issues)

    source_entry = roles.get("widget-source")
    if source_entry and "widget-source" in payload_bytes:
        try:
            source_text = payload_bytes["widget-source"].decode("utf-8")
            if _WIDGET_SOURCE_FORBIDDEN.search(source_text):
                issues.append(
                    ValidationIssue(
                        "WIDGET_SOURCE_PROHIBITED",
                        "$.payloads.widget-source",
                        "Widget review source contains dynamic loading or prohibited authority",
                    )
                )
        except (ContractError, UnicodeDecodeError) as exc:
            if isinstance(exc, ContractError):
                issues.extend(exc.issues)
            else:
                issues.append(
                    ValidationIssue(
                        "WIDGET_SOURCE_PROHIBITED",
                        "$.payloads.widget-source",
                        "Widget review source must be valid UTF-8 text",
                    )
                )

    payload_by_uri = {entry["uri"]: entry for entry in payload_entries}
    if len(payload_by_uri) != len(payload_entries):
        issues.append(ValidationIssue("DUPLICATE_PAYLOAD_URI", "$.payloads", "payload URI duplicated"))
    license_binding = manifest_value["license"]["file"]
    license_uri = license_binding["uri"]
    docs_uris = {entry["uri"] for entry in manifest_value["developerDocs"]}
    payload_uris = {entry["uri"] for entry in payload_entries}
    if license_uri not in payload_uris:
        issues.append(ValidationIssue("MANIFEST_PAYLOAD_MISSING", "$.license.file.uri", "license file is not digest-bound"))
    elif payload_by_uri[license_uri]["sha256"] != license_binding["sha256"]:
        issues.append(ValidationIssue("DIGEST_MISMATCH", "$.license.file.sha256", "license digest binding differs"))
    for index, notice in enumerate(manifest_value["license"]["thirdPartyNotices"]):
        notice_uri = notice["uri"]
        if notice_uri not in payload_uris:
            issues.append(
                ValidationIssue(
                    "MANIFEST_PAYLOAD_MISSING",
                    f"$.license.thirdPartyNotices[{index}].uri",
                    "third-party notice is not digest-bound",
                )
            )
        elif payload_by_uri[notice_uri]["sha256"] != notice["sha256"]:
            issues.append(
                ValidationIssue(
                    "DIGEST_MISMATCH",
                    f"$.license.thirdPartyNotices[{index}].sha256",
                    "notice digest binding differs",
                )
            )
    for index, docs_entry in enumerate(manifest_value["developerDocs"]):
        docs_uri = docs_entry["uri"]
        if docs_uri not in payload_uris:
            issues.append(
                ValidationIssue(
                    "DOC_REFERENCE_INVALID", f"$.developerDocs[{index}].uri", "developer documentation is not digest-bound"
                )
            )
        elif payload_by_uri[docs_uri]["sha256"] != docs_entry["sha256"]:
            issues.append(
                ValidationIssue(
                    "DIGEST_MISMATCH",
                    f"$.developerDocs[{index}].sha256",
                    "developer documentation digest binding differs",
                )
            )

    ordered = tuple(sorted(set(report.issues + tuple(issues)), key=lambda item: (item.path, item.code, item.message)))
    final_report = ValidationReport(relative_target, ordered)
    if not final_report.valid:
        return None, final_report
    record = PackageRecord(manifest_path, manifest_value, sha256_bytes(manifest_bytes))
    return record, final_report


def _file_binding_issues(
    repo_root: Path,
    binding: Any,
    *,
    expected_uri: str,
    path: str,
) -> list[ValidationIssue]:
    if not isinstance(binding, dict):
        return []
    issues: list[ValidationIssue] = []
    if binding.get("uri") != expected_uri:
        issues.append(
            ValidationIssue(
                "SOURCE_IDENTITY_MISMATCH",
                f"{path}.uri",
                "generated source binding has the wrong authoritative URI",
            )
        )
        return issues
    source_path = repo_root / expected_uri
    try:
        observed_digest = sha256_file(source_path, target=expected_uri)
        if binding.get("sha256") != observed_digest:
            issues.append(
                ValidationIssue(
                    "DIGEST_MISMATCH",
                    f"{path}.sha256",
                    "generated source digest differs from current authoritative bytes",
                )
            )
    except ContractError as exc:
        issues.extend(exc.issues)
    return issues


def _generated_from_issues(repo_root: Path, generated_from: Any) -> list[ValidationIssue]:
    if not isinstance(generated_from, dict):
        return []
    issues: list[ValidationIssue] = []
    fixed_bindings = (
        ("architecture", EXPECTED_ARCHITECTURE_BINDING),
        ("buildGate", EXPECTED_GATE_BINDING),
    )
    for name, expected in fixed_bindings:
        if generated_from.get(name) != expected:
            issues.append(
                ValidationIssue(
                    "SOURCE_IDENTITY_MISMATCH",
                    f"$.generatedFrom.{name}",
                    f"generated {name} binding differs from the fixed Wave 1 identity",
                )
            )
    architecture_path = repo_root / ARCHITECTURE_URI
    try:
        if sha256_file(architecture_path, target=ARCHITECTURE_URI) != EXPECTED_ARCHITECTURE_BINDING["sha256"]:
            issues.append(
                ValidationIssue(
                    "DIGEST_MISMATCH",
                    ARCHITECTURE_URI,
                    "fixed architecture input bytes differ from the generated identity",
                )
            )
    except ContractError as exc:
        issues.extend(exc.issues)

    issues.extend(
        _file_binding_issues(
            repo_root,
            generated_from.get("contractRelease"),
            expected_uri=CONTRACT_RELEASE_URI,
            path="$.generatedFrom.contractRelease",
        )
    )
    developer_docs = generated_from.get("developerDocs")
    if isinstance(developer_docs, dict):
        if developer_docs.get("contractVersion") != "v1":
            issues.append(
                ValidationIssue(
                    "SOURCE_IDENTITY_MISMATCH",
                    "$.generatedFrom.developerDocs.contractVersion",
                    "generated developer documentation contract version differs",
                )
            )
        if developer_docs.get("publicationState") != "private-local-ci-only":
            issues.append(
                ValidationIssue(
                    "SOURCE_IDENTITY_MISMATCH",
                    "$.generatedFrom.developerDocs.publicationState",
                    "generated developer documentation publication state differs",
                )
            )
        issues.extend(
            _file_binding_issues(
                repo_root,
                developer_docs.get("source"),
                expected_uri=DEVELOPER_DOCS_URI,
                path="$.generatedFrom.developerDocs.source",
            )
        )

    schemas = generated_from.get("schemas")
    if isinstance(schemas, dict):
        for kind, uri in sorted(SCHEMA_FILES.items()):
            issues.extend(
                _file_binding_issues(
                    repo_root,
                    schemas.get(kind),
                    expected_uri=uri,
                    path=f"$.generatedFrom.schemas.{kind}",
                )
            )
    issues.extend(
        _file_binding_issues(
            repo_root,
            generated_from.get("source"),
            expected_uri=SOURCE_DESCRIPTOR_URI,
            path="$.generatedFrom.source",
        )
    )
    if generated_from.get("sourceIdentity") != EXPECTED_SOURCE_IDENTITY:
        issues.append(
            ValidationIssue(
                "SOURCE_IDENTITY_MISMATCH",
                "$.generatedFrom.sourceIdentity",
                "generated source identity differs from the fixed Wave 1 identity",
            )
        )
    if generated_from.get("validatorVersion") != VERSION:
        issues.append(
            ValidationIssue(
                "SOURCE_IDENTITY_MISMATCH",
                "$.generatedFrom.validatorVersion",
                "generated validator version differs from the current validator",
            )
        )
    return issues


def _catalog_source_authority(
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], str | None, list[ValidationIssue]]:
    try:
        source = load_json(repo_root / SOURCE_DESCRIPTOR_URI, repo_root=repo_root, target=SOURCE_DESCRIPTOR_URI)
    except ContractError as exc:
        return {}, [], None, list(exc.issues)
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("packages"), list)
        or not isinstance(source.get("revocations"), list)
    ):
        return {}, [], None, [
            ValidationIssue(
                "CATALOG_ENTRY_INCOHERENT",
                "$.generatedFrom.source",
                "catalog snapshot does not match the bound source descriptor",
            )
        ]
    revision = source.get("revision")
    if not isinstance(revision, str):
        return {}, [], None, [
            ValidationIssue(
                "CATALOG_ENTRY_INCOHERENT",
                "$.generatedFrom.source",
                "catalog snapshot does not match the bound source descriptor",
            )
        ]
    entries: dict[str, dict[str, Any]] = {}
    for item in source["packages"]:
        if not isinstance(item, dict) or not isinstance(item.get("manifestUri"), str):
            return {}, [], None, [
                ValidationIssue(
                    "CATALOG_ENTRY_INCOHERENT",
                    "$.entries",
                    "catalog entries do not match the bound source descriptor",
                )
            ]
        uri = item["manifestUri"]
        if uri in entries:
            return {}, [], None, [
                ValidationIssue(
                    "CATALOG_ENTRY_INCOHERENT",
                    "$.entries",
                    "catalog entries do not match the bound source descriptor",
                )
            ]
        entries[uri] = item
    required_revocation_fields = {
        "effectiveRevision",
        "packageId",
        "packageVersion",
        "reasonCode",
        "state",
    }
    allowed_revocation_fields = required_revocation_fields | {"guidanceUri"}
    revocations: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for item in source["revocations"]:
        if (
            not isinstance(item, dict)
            or not required_revocation_fields.issubset(item)
            or not set(item).issubset(allowed_revocation_fields)
            or any(not isinstance(item.get(field), str) for field in required_revocation_fields)
            or ("guidanceUri" in item and not isinstance(item["guidanceUri"], str))
            or item.get("effectiveRevision") != revision
        ):
            return {}, [], None, [
                ValidationIssue(
                    "REVOCATION_INCOHERENT",
                    "$.revocations",
                    "source-authorized revocations are incoherent",
                )
            ]
        identity = (item["packageId"], item["packageVersion"])
        if identity in identities:
            return {}, [], None, [
                ValidationIssue(
                    "REVOCATION_INCOHERENT",
                    "$.revocations",
                    "source-authorized revocations are incoherent",
                )
            ]
        identities.add(identity)
        revocations.append(item)
    return entries, revocations, revision, []


def _source_revocation_issues(
    revocations: dict[str, Any],
    source_revocations: list[dict[str, Any]],
    source_revision: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if revocations["revision"] != source_revision:
        issues.append(
            ValidationIssue(
                "REVOCATION_INCOHERENT",
                "$.revision",
                "revocation set differs from the bound authoritative source",
            )
        )
    observed = revocations["revocations"]
    expected = sorted(
        source_revocations,
        key=lambda item: (item["packageId"], item["packageVersion"], item["state"]),
    )
    observed_order = sorted(
        observed,
        key=lambda item: (item["packageId"], item["packageVersion"], item["state"]),
    )
    if observed != observed_order:
        issues.append(
            ValidationIssue(
                "REVOCATION_INCOHERENT",
                "$.revocations",
                "revocations are not in the required deterministic order",
            )
        )
    expected_by_identity = {
        (item["packageId"], item["packageVersion"]): item for item in expected
    }
    observed_identities = [
        (item["packageId"], item["packageVersion"]) for item in observed
    ]
    if (
        len(observed) != len(expected)
        or len(set(observed_identities)) != len(observed_identities)
        or set(observed_identities) != set(expected_by_identity)
    ):
        issues.append(
            ValidationIssue(
                "REVOCATION_INCOHERENT",
                "$.revocations",
                "revocations do not match the bound authoritative source",
            )
        )
    fields = (
        "packageId",
        "packageVersion",
        "state",
        "reasonCode",
        "effectiveRevision",
        "guidanceUri",
    )
    for index, item in enumerate(observed):
        source_item = expected_by_identity.get((item["packageId"], item["packageVersion"]))
        if source_item is None:
            continue
        for field in fields:
            if (field in item) != (field in source_item) or item.get(field) != source_item.get(field):
                issues.append(
                    ValidationIssue(
                        "REVOCATION_INCOHERENT",
                        f"$.revocations[{index}].{field}",
                        "revocation differs from the bound authoritative source",
                    )
                )
    return issues


def validate_catalog_snapshot(repo_root: Path, index_path: Path, revocations_path: Path) -> ValidationReport:
    target = logical_target(repo_root, index_path)
    issues: list[ValidationIssue] = []
    try:
        index, _ = load_json_with_bytes(index_path, repo_root=repo_root, target=target)
    except ContractError as exc:
        index = None
        issues.extend(exc.issues)
    try:
        revocations, revocations_bytes = load_json_with_bytes(
            revocations_path,
            repo_root=repo_root,
            target=logical_target(repo_root, revocations_path),
        )
    except ContractError as exc:
        revocations = None
        revocations_bytes = b""
        issues.extend(exc.issues)
    if index is not None:
        issues.extend(validate_document(repo_root, "catalog", index, target=target).issues)
    if revocations is not None:
        issues.extend(
            validate_document(
                repo_root,
                "revocations",
                revocations,
                target=logical_target(repo_root, revocations_path),
            ).issues
        )
    if isinstance(index, dict):
        issues.extend(_generated_from_issues(repo_root, index.get("generatedFrom")))
    if issues or not isinstance(index, dict) or not isinstance(revocations, dict):
        return ValidationReport(target, tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))))
    source_entries, source_revocations, source_revision, source_issues = _catalog_source_authority(repo_root)
    issues.extend(source_issues)
    if source_issues or source_revision is None:
        return ValidationReport(target, tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))))
    if index["revision"] != revocations["revision"]:
        issues.append(
            ValidationIssue("CATALOG_MIXED_REVISION", "$.revision", "catalog and revocations revisions differ")
        )
    if index["revocationsSha256"] != sha256_bytes(revocations_bytes):
        issues.append(
            ValidationIssue(
                "CATALOG_REVOCATION_DIGEST_MISMATCH",
                "$.revocationsSha256",
                "revocations digest differs from catalog binding",
            )
        )
    issues.extend(_source_revocation_issues(revocations, source_revocations, source_revision))
    entries = index["entries"]
    observed_manifest_uris = [entry["manifestUri"] for entry in entries]
    if (
        len(entries) != len(source_entries)
        or len(set(observed_manifest_uris)) != len(observed_manifest_uris)
        or set(observed_manifest_uris) != set(source_entries)
    ):
        issues.append(
            ValidationIssue(
                "CATALOG_ENTRY_INCOHERENT",
                "$.entries",
                "catalog entries do not match the bound source descriptor",
            )
        )
    expected_order = sorted(
        entries,
        key=lambda item: (item["displayName"].casefold(), item["packageId"], item["packageVersion"]),
    )
    if entries != expected_order:
        issues.append(
            ValidationIssue(
                "CATALOG_ENTRY_INCOHERENT",
                "$.entries",
                "catalog entries are not in the required deterministic order",
            )
        )
    for entry_index, entry in enumerate(index["entries"]):
        source_entry = source_entries.get(entry["manifestUri"])
        if source_entry is None:
            issues.append(
                ValidationIssue(
                    "CATALOG_ENTRY_INCOHERENT",
                    f"$.entries[{entry_index}].manifestUri",
                    "catalog entry is not present in the bound source descriptor",
                )
            )
            continue
        try:
            manifest_path = resolve_reference(repo_root, entry["manifestUri"], f"$.entries[{entry_index}].manifestUri")
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        record, package_report = validate_package(repo_root, manifest_path)
        issues.extend(package_report.issues)
        if record is None:
            continue
        package = record.manifest["package"]
        if entry["manifestSha256"] != record.manifest_sha256:
            issues.append(
                ValidationIssue(
                    "CATALOG_MANIFEST_DIGEST_MISMATCH",
                    f"$.entries[{entry_index}].manifestSha256",
                    "manifest digest differs from catalog binding",
                )
            )
        expected_fields = {
            "classification": source_entry.get("classification"),
            "deliveryMode": package["deliveryMode"],
            "displayName": package["name"],
            "maturity": source_entry.get("maturity"),
            "packageId": package["id"],
            "packageVersion": package["version"],
            "publication": source_entry.get("publication"),
            "revision": source_revision,
            "type": package["type"],
        }
        for field, expected in expected_fields.items():
            if entry[field] != expected:
                issues.append(
                    ValidationIssue(
                        "CATALOG_ENTRY_INCOHERENT",
                        f"$.entries[{entry_index}].{field}",
                        "catalog entry differs from its bound authoritative source",
                    )
                )
    return ValidationReport(
        target, tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message)))
    )
