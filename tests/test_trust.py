from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from marketplace_contracts.cli import main as cli_main
from marketplace_contracts.io import load_json_bytes
from marketplace_contracts.model import ContractError
from marketplace_contracts.trust import (
    TEST_KEY_FIXTURE,
    TEST_KEY_RING,
    TEST_TRUST_OUTPUT,
    b64url_decode,
    canonical_json,
    check_test_trust_bundle,
    latest_path_commit,
    private_test_key,
    sign_envelope,
    source_tree_digest,
    verify_envelope,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def read_bundle(repo_root: Path) -> tuple[bytes, bytes, bytes]:
    trust = repo_root / TEST_TRUST_OUTPUT
    return (
        (trust / "catalog-envelope-v1.json").read_bytes(),
        (trust / "catalog-envelope-v1.signatures.json").read_bytes(),
        (repo_root / TEST_KEY_RING).read_bytes(),
    )


def test_checked_in_bundle_is_canonical_signed_and_schema_valid(repo_root: Path) -> None:
    result = check_test_trust_bundle(repo_root)
    assert result["status"] == "verified"
    envelope_bytes, signatures_bytes, key_ring_bytes = read_bundle(repo_root)
    assert not envelope_bytes.endswith(b"\n")
    assert canonical_json(load_json_bytes(envelope_bytes)) == envelope_bytes
    envelope = verify_envelope(envelope_bytes, signatures_bytes, key_ring_bytes, now=NOW)
    schema = json.loads((repo_root / "schemas/v1/catalog-envelope.schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(envelope)


def test_one_byte_envelope_mutation_fails_closed(repo_root: Path) -> None:
    envelope, signatures, key_ring = read_bundle(repo_root)
    mutated = bytearray(envelope)
    mutated[-2] ^= 1
    with pytest.raises(ContractError) as failure:
        verify_envelope(bytes(mutated), signatures, key_ring, now=NOW)
    assert {issue.code for issue in failure.value.issues} & {
        "CATALOG_SIGNATURE_UNTRUSTED",
        "CATALOG_ENVELOPE_INVALID",
        "JSON_INVALID",
    }


def test_active_and_next_authorize_rotation_but_retired_and_revoked_do_not(repo_root: Path) -> None:
    envelope_bytes, _, key_ring_bytes = read_bundle(repo_root)
    envelope = load_json_bytes(envelope_bytes)
    fixtures = json.loads((repo_root / TEST_KEY_FIXTURE).read_text())
    for item in fixtures["keys"]:
        _, signatures = sign_envelope(
            envelope,
            item["keyId"],
            b64url_decode(item["privateSeed"], expected_bytes=32, path="seed"),
        )
        assert verify_envelope(envelope_bytes, signatures, key_ring_bytes, now=NOW)["catalogRevision"]

    for key_id, status, label in (
        ("mkt-test-ed25519-2025-01", "retired", b"172X Marketplace PRIVATE TEST retired 2025-01"),
        ("mkt-test-ed25519-2025-02", "revoked", b"172X Marketplace PRIVATE TEST revoked 2025-02"),
    ):
        seed, public = private_test_key(hashlib.sha256(label).digest())
        ring = canonical_json({
            "schemaVersion": 1,
            "keys": [{
                "algorithm": "Ed25519",
                "keyId": key_id,
                "notAfter": None,
                "notBefore": "2025-01-01T00:00:00Z",
                "publicKey": base64.urlsafe_b64encode(public).decode().rstrip("="),
                "status": status,
            }],
        })
        _, signatures = sign_envelope(envelope, key_id, seed)
        with pytest.raises(ContractError) as failure:
            verify_envelope(envelope_bytes, signatures, ring, now=NOW)
        assert {issue.code for issue in failure.value.issues} == {"CATALOG_SIGNATURE_UNTRUSTED"}


def test_source_digest_matches_independent_git_object_computation(repo_root: Path) -> None:
    commit, observed, paths = source_tree_digest(
        repo_root,
        "HEAD",
        "packages/com.mastylolabs.clock/1.0.0",
    )
    leaves: list[str] = []
    for path in sorted(paths, key=lambda value: value.encode()):
        content = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        ).stdout
        leaves.append(hashlib.sha256(
            b"blob\0" + str(len(content)).encode() + b"\0" + path.encode() + b"\0" + content
        ).hexdigest())
    expected = hashlib.sha256(b"tree-v1\0" + "\n".join(leaves).encode()).hexdigest()
    assert observed == expected == "a1c7383525aa93ba1d8de44dbe7a385a73e58a99b3ec7b8c26c9d17eef0bc363"


def test_latest_path_commit_ignores_unrelated_head_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "172X Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@172x.invalid"], cwd=tmp_path, check=True)
    package = tmp_path / "packages/com.example.clock/1.0.0"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text("{}\n")
    subprocess.run(["git", "add", "packages"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "package"], cwd=tmp_path, check=True)
    package_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    (tmp_path / "README.md").write_text("unrelated\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated"], cwd=tmp_path, check=True)

    assert latest_path_commit(tmp_path, "packages/com.example.clock/1.0.0") == package_commit


def test_trust_cli_checks_without_exposing_a_production_key(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--repo-root", str(repo_root), "trust-fixture", "--check"]) == 0
    output = capsys.readouterr().out
    assert '"status": "verified"' in output
    assert "production" not in (repo_root / TEST_KEY_FIXTURE).read_text().lower()
