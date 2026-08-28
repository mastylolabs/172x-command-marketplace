from __future__ import annotations

import copy
import json
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
from marketplace_contracts.validator import validate_catalog_snapshot, validate_document


DISPLAY_FIELDS = (
    "package.name",
    "capabilities[0].purpose",
    "developerDocs[0].topic",
    "source.authors[0].name",
    "source.upstream[0].attribution",
    "source.upstream[0].project",
    "source.upstream[0].repositoryIdentity",
    "entries[0].displayName",
)

DISPLAY_PROBES = (
    ("raw-html", '<b id="QA_V03_RAW_HTML_PROBE">qa</b>'),
    ("script", "<script>QA_V03_SCRIPT_PROBE()</script>"),
    ("event-handler", "<img src=x onerror=QA_V03_EVENT_PROBE()>"),
    ("javascript-uri", "javascript:QA_V03_URI_PROBE()"),
    ("data-uri", "data:text/html,QA_V03_URI_PROBE"),
    ("vbscript-uri", "vbscript:QA_V03_URI_PROBE()"),
    ("file-uri", "file:///QA_V03_URI_PROBE"),
    ("entity-encoded", "&lt;script&gt;QA_V03_ENTITY_PROBE()&lt;/script&gt;"),
    ("percent-encoded", "%3Cscript%3EQA_V03_PERCENT_PROBE()%3C/script%3E"),
    ("control", "QA_V03_CONTROL_PROBE\x00"),
)


def _copy_docs_repository(repo_root: Path, destination: Path) -> Path:
    for relative in ("contracts", "schemas", "packages", "registry/source", "docs"):
        shutil.copytree(repo_root / relative, destination / relative)
    shutil.copy2(repo_root / "LICENSE", destination / "LICENSE")
    shutil.copy2(repo_root / "mkdocs.yml", destination / "mkdocs.yml")
    return destination


def _base_manifest(repo_root: Path, package_id: str) -> dict[str, Any]:
    return json.loads((repo_root / f"packages/{package_id}/1.0.0/manifest.json").read_text(encoding="utf-8"))


def _document_with_display_value(repo_root: Path, field: str, value: str) -> tuple[str, dict[str, Any], str]:
    if field == "entries[0].displayName":
        document, _, _ = compose_release(repo_root)
        document["entries"][0]["displayName"] = value
        return "catalog", document, "$.entries[0].displayName"

    package_id = "org.catppuccin.mocha" if field.startswith("source.upstream") else "com.mastylolabs.clock"
    document = _base_manifest(repo_root, package_id)
    if field == "package.name":
        document["package"]["name"] = value
        path = "$.package.name"
    elif field == "capabilities[0].purpose":
        document["capabilities"] = [
            {
                "dataCategory": "none",
                "id": "host.clock.display",
                "purpose": value,
                "required": False,
                "scope": "host-provided",
            }
        ]
        path = "$.capabilities[0].purpose"
    elif field == "developerDocs[0].topic":
        document["developerDocs"][0]["topic"] = value
        path = "$.developerDocs[0].topic"
    elif field == "source.authors[0].name":
        document["source"]["authors"][0]["name"] = value
        path = "$.source.authors[0].name"
    else:
        leaf = field.rsplit(".", 1)[1]
        document["source"]["upstream"][0][leaf] = value
        path = f"$.source.upstream[0].{leaf}"
    return "manifest", document, path


@pytest.mark.parametrize("field", DISPLAY_FIELDS)
@pytest.mark.parametrize(("probe_name", "probe"), DISPLAY_PROBES, ids=[item[0] for item in DISPLAY_PROBES])
def test_all_declared_display_fields_reject_qa_probe_matrix_at_semantic_and_cli_boundaries(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    probe_name: str,
    probe: str,
) -> None:
    kind, document, expected_path = _document_with_display_value(repo_root, field, probe)
    report = validate_document(repo_root, kind, document, target="external-input")
    matching = [issue for issue in report.issues if issue.code == "PLAIN_TEXT_UNSAFE"]
    assert [(issue.code, issue.path) for issue in matching] == [("PLAIN_TEXT_UNSAFE", expected_path)]

    path = tmp_path / "display-probe.json"
    path.write_bytes(canonical_json_bytes(document))
    exit_code = cli.main(
        ["--repo-root", str(repo_root), "validate", "--kind", kind, str(path)]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1, probe_name
    assert ("PLAIN_TEXT_UNSAFE", expected_path) in {
        (issue["code"], issue["path"]) for issue in output["issues"]
    }
    assert str(repo_root) not in json.dumps(output)
    assert str(tmp_path) not in json.dumps(output)


@pytest.mark.parametrize("field", DISPLAY_FIELDS)
def test_all_declared_display_fields_allow_benign_unicode_and_punctuation(repo_root: Path, field: str) -> None:
    kind, document, _ = _document_with_display_value(repo_root, field, "Café — ‘Clock’ (安全)! № 1")
    assert validate_document(repo_root, kind, document, target="external-input").valid


def test_altered_generated_display_name_fails_direct_snapshot_and_cli(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    index, revocations, _ = compose_release(repo_root)
    index["entries"][0]["displayName"] = "<script>QA_V03_CATALOG_PROBE()</script>"
    index_path = tmp_path / "index.json"
    revocations_path = tmp_path / "revocations.json"
    index_path.write_bytes(canonical_json_bytes(index))
    revocations_path.write_bytes(canonical_json_bytes(revocations))

    report = validate_catalog_snapshot(repo_root, index_path, revocations_path)
    assert ("PLAIN_TEXT_UNSAFE", "$.entries[0].displayName") in {
        (issue.code, issue.path) for issue in report.issues
    }

    exit_code = cli.main(
        ["--repo-root", str(repo_root), "snapshot", str(index_path), str(revocations_path)]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert ("PLAIN_TEXT_UNSAFE", "$.entries[0].displayName") in {
        (issue["code"], issue["path"]) for issue in output["issues"]
    }


def test_builder_staging_uses_complete_display_validation(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compose = builder.compose_release

    def compose_with_active_display(root: Path):  # type: ignore[no-untyped-def]
        index, revocations, records = original_compose(root)
        altered_index = copy.deepcopy(index)
        altered_index["entries"][0]["displayName"] = "<script>QA_V03_STAGING_PROBE()</script>"
        return altered_index, revocations, records

    monkeypatch.setattr(builder, "compose_release", compose_with_active_display)
    output = tmp_path / "generated"
    with pytest.raises(ContractError) as failure:
        build_catalog(repo_root, output)
    assert ("PLAIN_TEXT_UNSAFE", "$.entries[0].displayName") in {
        (issue.code, issue.path) for issue in failure.value.issues
    }
    assert not output.exists()
    assert not list(tmp_path.glob(".generated.staging-*"))
    assert not (tmp_path / ".generated.lock").exists()


@pytest.mark.parametrize(
    ("probe_name", "probe"),
    (
        ("raw-tag", '<b id="QA_V03_RAW_HTML_PROBE">qa</b>'),
        ("raw-comment", "<!-- QA_V03_RAW_HTML_PROBE -->"),
        ("raw-declaration", "<!DOCTYPE html>"),
        ("raw-cdata", "<![CDATA[QA_V03_RAW_HTML_PROBE]]>"),
        ("raw-processing-instruction", "<?QA_V03_RAW_HTML_PROBE?>"),
        ("script", "<script>QA_V03_SCRIPT_PROBE()</script>"),
        ("event-handler", "<img src=x onerror=QA_V03_EVENT_PROBE()>"),
        ("scriptable-link", "[qa](javascript:QA_V03_URI_PROBE())"),
        ("encoded-entity", "&lt;b&gt;QA_V03_ENTITY_PROBE&lt;/b&gt;"),
        ("encoded-percent", "%3Cb%3EQA_V03_PERCENT_PROBE%3C/b%3E"),
        ("nul", "QA_V03_CONTROL\x00PROBE"),
        ("esc", "QA_V03_CONTROL\x1bPROBE"),
        ("c1", "QA_V03_CONTROL\x85PROBE"),
    ),
)
def test_docs_preflight_rejects_raw_active_encoded_and_control_content(
    repo_root: Path,
    tmp_path: Path,
    probe_name: str,
    probe: str,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{probe}\n", encoding="utf-8")
    report = validate_docs(sandbox)
    assert ("DOC_ACTIVE_CONTENT", "docs/index.md") in {
        (issue.code, issue.path) for issue in report.issues
    }, probe_name
    serialized = json.dumps(report.as_dict(), ensure_ascii=False)
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized


def test_docs_preflight_allows_benign_unicode_and_documented_punctuation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    page = sandbox / "docs/index.md"
    page.write_text(
        page.read_text(encoding="utf-8") + "\nCafé — ‘Clock’ (安全)! № 1; Theme/Panel=declarative-data.\n",
        encoding="utf-8",
    )
    assert validate_docs(sandbox).valid


@pytest.mark.parametrize(
    "probe",
    (
        '<b id="QA_V03_RAW_HTML_PROBE">qa</b>',
        "<script>QA_V03_SITE_SCRIPT_PROBE()</script>",
        "<p onclick=QA_V03_SITE_EVENT_PROBE()>qa</p>",
        '<a href="javascript:QA_V03_SITE_URI_PROBE()">qa</a>',
        "QA_V03_SITE_CONTROL\x00PROBE",
    ),
)
def test_generated_site_direct_bypass_rejects_raw_active_and_control_content(
    tmp_path: Path,
    probe: str,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        f'<html><body><div role="main">{probe}</div></body></html>',
        encoding="utf-8",
    )
    report = validate_site_output(site)
    assert ("DOC_ACTIVE_CONTENT", "index.html") in {
        (issue.code, issue.path) for issue in report.issues
    }


@pytest.mark.parametrize(
    "probe",
    (
        "<![CDATA[QA_V04_DIRECT_CDATA]]>",
        "<!UNKNOWN QA_V05_UNKNOWN_DECLARATION>",
    ),
)
def test_generated_site_rejects_cdata_and_unknown_declarations_at_direct_and_cli_boundaries(
    repo_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    probe: str,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        f'<html><body><div role="main">{probe}</div></body></html>',
        encoding="utf-8",
    )
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
    assert "QA_V04_DIRECT_CDATA" not in serialized
    assert "QA_V05_UNKNOWN_DECLARATION" not in serialized
    assert str(repo_root) not in serialized
    assert str(tmp_path) not in serialized


def test_strict_mkdocs_emitted_raw_marker_fails_independent_site_validation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    marker = '<b id="QA_V03_RAW_HTML_PROBE">qa</b>'
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8")
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
    assert marker in emitted
    report = validate_site_output(site)
    assert ("DOC_ACTIVE_CONTENT", "index.html") in {
        (issue.code, issue.path) for issue in report.issues
    }


def test_strict_mkdocs_emitted_cdata_fails_independent_site_validation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    sandbox = _copy_docs_repository(repo_root, tmp_path / "repo")
    marker = "<![CDATA[QA_V04_CDATA]]>"
    page = sandbox / "docs/index.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8")
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
    assert marker in emitted
    report = validate_site_output(site)
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("DOC_ACTIVE_CONTENT", "index.html")
    ]
