from __future__ import annotations

import copy
import json
import re
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
from marketplace_contracts.io import canonical_json_bytes
from marketplace_contracts.model import ContractError
from marketplace_contracts.validator import validate_catalog_snapshot


CATALOG_FIELD_MUTATIONS = (
    ("classification", "official"),
    ("publication", "published"),
    ("maturity", "standard"),
    ("displayName", "SEC V06 trusted system package"),
)

BALANCED_RELOCATION_PROBES = (
    ("script", "</p></div><script>SEC_V08_SCRIPT_RELOCATION()</script><div><p>"),
    ("event", "</p></div><img src=x onerror=SEC_V08_EVENT_RELOCATION()><div><p>"),
    ("processing-instruction", "</p></div><?SEC_V08_PI_RELOCATION?><div><p>"),
)

BUILD_STAMP_REJECTION_PROBES = (
    ("active-script", "--><script>SEC_V09_STAMP_SCRIPT()</script><!--"),
    ("event-handler", "--><img src=x onerror=SEC_V09_STAMP_EVENT()><!--"),
    ("processing-instruction", "--><?SEC_V09_STAMP_PI?><!--"),
    ("declaration", "--><!SEC_V09_STAMP_DECL><!--"),
    ("inert-text", "SEC_V09_STAMP_INERT"),
    ("nul", "2026-08-27 12:34:56.123456+00:00\x00"),
    ("escape", "2026-08-27 12:34:56.123456+00:00\x1b"),
    ("c1", "2026-08-27 12:34:56.123456+00:00\x85"),
    ("invalid-calendar", "2026-02-30 12:34:56.123456+00:00"),
    ("invalid-time", "2026-08-27 25:34:56.123456+00:00"),
    ("leading-space", " 2026-08-27 12:34:56.123456+00:00"),
    ("trailing-space", "2026-08-27 12:34:56.123456+00:00 "),
    ("date-time-delimiter", "2026-08-27T12:34:56.123456+00:00"),
    ("timezone-delimiter", "2026-08-27 12:34:56.123456Z"),
    ("timezone-offset-shape", "2026-08-27 12:34:56.123456+0000"),
    ("fraction-width", "2026-08-27 12:34:56.12345+00:00"),
    ("html-encoded", "--&gt;&lt;script&gt;SEC_V09_STAMP_HTML&lt;/script&gt;&lt;!--"),
    ("percent-encoded", "%2D%2D%3E%3Cscript%3ESEC_V09_STAMP_PERCENT%3C/script%3E"),
)

_BUILD_STAMP_VALUE = re.compile(r"(?m)^(Build Date UTC : )([^\r\n]+)$")


def _copy_docs_repository(repo_root: Path, destination: Path) -> Path:
    for relative in ("contracts", "schemas", "packages", "registry/source", "docs"):
        shutil.copytree(repo_root / relative, destination / relative)
    shutil.copy2(repo_root / "LICENSE", destination / "LICENSE")
    shutil.copy2(repo_root / "mkdocs.yml", destination / "mkdocs.yml")
    return destination


def _write_snapshot(
    directory: Path,
    index: dict[str, Any],
    revocations: dict[str, Any],
) -> tuple[Path, Path]:
    directory.mkdir(parents=True)
    index_path = directory / "index.json"
    revocations_path = directory / "revocations.json"
    index_path.write_bytes(canonical_json_bytes(index))
    revocations_path.write_bytes(canonical_json_bytes(revocations))
    return index_path, revocations_path


@pytest.fixture(scope="module")
def canonical_mkdocs_index(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> str:
    site = tmp_path_factory.mktemp("v09-canonical-site") / "site"
    build = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    return (site / "index.html").read_text(encoding="utf-8")


def _replace_build_stamp_value(document: str, value: str) -> str:
    matches = list(_BUILD_STAMP_VALUE.finditer(document))
    assert len(matches) == 1
    match = matches[0]
    return document[: match.start(2)] + value + document[match.end(2) :]


@pytest.mark.parametrize(
    "document",
    (
        '<!DOCTYPE html><html><body><div role="main"></div><![CDATA[SEC_V06_EARLY_CDATA]]></body></html>',
        '<!DOCTYPE html><html><body><div role="main"></div><!UNKNOWN SEC_V06_EARLY_DECL></body></html>',
        '<!DOCTYPE html><html><body><div role="main"></main><![CDATA[SEC_V06_MALFORMED_CDATA]]></div></body></html>',
    ),
)
def test_generated_site_boundary_escape_rejects_direct_and_cli_without_disclosure(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    document: str,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(document, encoding="utf-8")

    report = validate_site_output(site)
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    exit_code = cli.main(["--repo-root", str(repo_root), "site", str(site)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert [(issue["code"], issue["path"]) for issue in output["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]
    serialized = json.dumps(output)
    assert "SEC_V06_" not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized


def test_generated_site_preserves_template_declaration_outside_authored_content(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<!DOCTYPE html><html><body><div role="main"><p>Café — 安全.</p></div></body></html>',
        encoding="utf-8",
    )
    assert validate_site_output(site).valid


@pytest.mark.parametrize(
    "probe",
    (
        "</div><![CDATA[SEC_V06_MKDOCS_PREMATURE_CDATA]]>",
        "</main><![CDATA[SEC_V06_MKDOCS_MALFORMED_CDATA]]>",
    ),
)
def test_strict_mkdocs_boundary_escape_is_rejected_by_source_and_generated_site_boundaries(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    probe: str,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{probe}\n", encoding="utf-8")
    source_report = validate_docs(sandbox)
    assert ("DOC_ACTIVE_CONTENT", "docs/index.md") in {
        (issue.code, issue.path) for issue in source_report.issues
    }

    site = tmp_path / "site"
    build = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=sandbox,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    emitted = (site / "index.html").read_text(encoding="utf-8")
    assert probe in emitted
    report = validate_site_output(site)
    assert ("DOC_ACTIVE_CONTENT", "index.html") in {
        (issue.code, issue.path) for issue in report.issues
    }

    exit_code = cli.main(["--repo-root", str(repo_root), "site", str(site)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert ("DOC_ACTIVE_CONTENT", "index.html") in {
        (issue["code"], issue["path"]) for issue in output["issues"]
    }
    serialized = json.dumps(output)
    assert "SEC_V06_" not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize(("probe_name", "probe"), BALANCED_RELOCATION_PROBES)
def test_strict_mkdocs_balanced_authored_relocation_is_rejected_twice_at_every_boundary(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    probe_name: str,
    probe: str,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{probe}\n", encoding="utf-8")
    source_report = validate_docs(sandbox)
    assert ("DOC_ACTIVE_CONTENT", "docs/index.md") in {
        (issue.code, issue.path) for issue in source_report.issues
    }

    site = tmp_path / "site"
    build = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=sandbox,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    emitted = (site / "index.html").read_text(encoding="utf-8")
    assert probe in emitted

    direct = [validate_site_output(site).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(["--repo-root", str(repo_root), "site", str(site)]))
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    assert [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    serialized = json.dumps({"direct": direct, "cli": outputs}, ensure_ascii=False)
    for forbidden in (
        "SEC_V08_",
        str(repo_root),
        str(tmp_path),
        "\x00",
        "\x1b",
        "\x85",
        "Traceback",
        "Exception",
    ):
        assert forbidden not in serialized, probe_name


@pytest.mark.parametrize(
    "active_output",
    (
        "<script>SEC_V08_DIRECT_SCRIPT()</script>",
        "<img src=x onerror=SEC_V08_DIRECT_EVENT()>",
        "<?SEC_V08_DIRECT_PI?>",
        '<script src="js/base.js"></script>',
    ),
)
def test_generated_site_template_suffix_binding_rejects_balanced_direct_relocation(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    active_output: str,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<!DOCTYPE html><html><body><div role="main"><p></p></div>'
        f"{active_output}<div><p></p></div></body></html>",
        encoding="utf-8",
    )
    direct = [validate_site_output(site).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(["--repo-root", str(repo_root), "site", str(site)]))
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    serialized = json.dumps({"direct": direct, "cli": outputs}, ensure_ascii=False)
    assert "SEC_V08_" not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized


def test_generated_site_template_suffix_binding_rejects_inert_relocated_bytes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<!DOCTYPE html><html><body><div role="main"><p>inside</p></div>'
        "<p>Café — relocated.</p><div><p></p></div></body></html>",
        encoding="utf-8",
    )
    report = validate_site_output(site)
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]


@pytest.mark.parametrize(("probe_name", "stamp_value"), BUILD_STAMP_REJECTION_PROBES)
def test_generated_site_build_stamp_rejects_noncanonical_values_twice_at_direct_and_cli(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    canonical_mkdocs_index: str,
    probe_name: str,
    stamp_value: str,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    mutated = _replace_build_stamp_value(canonical_mkdocs_index, stamp_value)
    (site / "index.html").write_text(mutated, encoding="utf-8")

    direct = [validate_site_output(site).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert [(issue["code"], issue["path"]) for issue in direct[0]["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(["--repo-root", str(repo_root), "site", str(site)]))
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [1, 1]
    assert outputs[0] == outputs[1]
    assert [(issue["code"], issue["path"]) for issue in outputs[0]["issues"]] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]

    serialized = json.dumps({"direct": direct, "cli": outputs}, ensure_ascii=False)
    for forbidden in (
        "SEC_V09_",
        str(repo_root),
        str(tmp_path),
        "\x00",
        "\x1b",
        "\x85",
        "Traceback",
        "Exception",
    ):
        assert forbidden not in serialized, probe_name


@pytest.mark.parametrize(
    "stamp_value",
    (
        None,
        "2024-02-29 23:59:59.000001+00:00",
        "2024-02-29 23:59:59+00:00",
    ),
    ids=("canonical", "safe-alternate-microseconds", "safe-alternate-source-date-epoch"),
)
def test_generated_site_build_stamp_accepts_only_supported_mkdocs_utc_values_without_mutation(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    canonical_mkdocs_index: str,
    stamp_value: str | None,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    document = (
        canonical_mkdocs_index
        if stamp_value is None
        else _replace_build_stamp_value(canonical_mkdocs_index, stamp_value)
    )
    index = site / "index.html"
    index.write_text(document, encoding="utf-8")

    direct = [validate_site_output(site).as_dict() for _ in range(2)]
    assert direct[0] == direct[1]
    assert direct[0]["valid"] is True
    assert direct[0]["issues"] == []

    exits: list[int] = []
    outputs: list[dict[str, Any]] = []
    for _ in range(2):
        exits.append(cli.main(["--repo-root", str(repo_root), "site", str(site)]))
        outputs.append(json.loads(capsys.readouterr().out))
    assert exits == [0, 0]
    assert outputs[0] == outputs[1]
    assert outputs[0]["valid"] is True
    assert outputs[0]["issues"] == []
    assert index.read_text(encoding="utf-8") == document


@pytest.mark.parametrize(("field", "value"), CATALOG_FIELD_MUTATIONS)
def test_snapshot_and_builder_bind_source_and_manifest_owned_catalog_fields(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: str,
) -> None:
    index, revocations, _ = compose_release(repo_root)
    altered = copy.deepcopy(index)
    altered["entries"][0][field] = value
    index_path, revocations_path = _write_snapshot(tmp_path / "snapshot", altered, revocations)
    expected = ("CATALOG_ENTRY_INCOHERENT", f"$.entries[0].{field}")

    report = validate_catalog_snapshot(repo_root, index_path, revocations_path)
    assert expected in {(issue.code, issue.path) for issue in report.issues}

    exit_code = cli.main(
        ["--repo-root", str(repo_root), "snapshot", str(index_path), str(revocations_path)]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert expected in {(issue["code"], issue["path"]) for issue in output["issues"]}
    serialized = json.dumps(output)
    assert "SEC V06 trusted system package" not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized

    original_compose = builder.compose_release

    def altered_compose(root: Path):  # type: ignore[no-untyped-def]
        staged_index, staged_revocations, records = original_compose(root)
        staged_index = copy.deepcopy(staged_index)
        staged_index["entries"][0][field] = value
        return staged_index, staged_revocations, records

    monkeypatch.setattr(builder, "compose_release", altered_compose)
    generated = tmp_path / "generated"
    with pytest.raises(ContractError) as failure:
        build_catalog(repo_root, generated)
    assert expected in {(issue.code, issue.path) for issue in failure.value.issues}
    assert not generated.exists()
    assert not list(tmp_path.glob(".generated.staging-*"))
    assert not (tmp_path / ".generated.lock").exists()


@pytest.mark.parametrize("case", ("add", "omit", "remap", "cross-wire", "reorder"))
def test_snapshot_source_mapping_cardinality_and_order_fail_closed(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    index, revocations, _ = compose_release(repo_root)
    source_digest = index["generatedFrom"]["source"]["sha256"]
    if case == "add":
        added = copy.deepcopy(index["entries"][0])
        added["displayName"] = "Added representative"
        added["packageId"] = "org.example.added"
        index["entries"].append(added)
    elif case == "omit":
        index["entries"].pop()
    elif case == "remap":
        index["entries"][0]["manifestUri"] = "packages/org.example.unknown/1.0.0/manifest.json"
    elif case == "cross-wire":
        first_uri = index["entries"][0]["manifestUri"]
        index["entries"][0]["manifestUri"] = index["entries"][1]["manifestUri"]
        index["entries"][1]["manifestUri"] = first_uri
    else:
        index["entries"].reverse()
    assert index["generatedFrom"]["source"]["sha256"] == source_digest

    index_path, revocations_path = _write_snapshot(tmp_path / "snapshot", index, revocations)
    report = validate_catalog_snapshot(repo_root, index_path, revocations_path)
    assert "CATALOG_ENTRY_INCOHERENT" in {issue.code for issue in report.issues}

    exit_code = cli.main(
        ["--repo-root", str(repo_root), "snapshot", str(index_path), str(revocations_path)]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert "CATALOG_ENTRY_INCOHERENT" in {issue["code"] for issue in output["issues"]}
    serialized = json.dumps(output)
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized
