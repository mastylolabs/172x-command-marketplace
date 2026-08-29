from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .io import MAX_JSON_BYTES, load_json_bytes, read_bounded_file, sha256_bytes
from .model import ContractError, ValidationIssue

ENVELOPE_SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "Ed25519"
SOURCE_TREE_DOMAIN = b"tree-v1\0"
SOURCE_BLOB_DOMAIN = b"blob\0"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+){2,}$")
TEST_TRUST_OUTPUT = "registry/trust/v1/test"
TEST_KEY_FIXTURE = "fixtures/crypto/test-signing-keys.json"
TEST_KEY_RING = "fixtures/crypto/trusted-key-ring-v1.json"
TEST_ISSUED_AT = "2026-08-28T00:00:00Z"
TEST_EXPIRES_AT = "2027-08-28T00:00:00Z"


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str, *, expected_bytes: int, path: str) -> bytes:
    if not isinstance(value, str) or "=" in value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ContractError([ValidationIssue("SIGNATURE_ENCODING_INVALID", path, "value must use unpadded Base64URL")])
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ContractError([ValidationIssue("SIGNATURE_ENCODING_INVALID", path, "value is not valid Base64URL")]) from exc
    if len(decoded) != expected_bytes:
        raise ContractError([ValidationIssue("SIGNATURE_ENCODING_INVALID", path, "decoded value has the wrong byte length")])
    return decoded


def canonical_json(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError) as exc:
        raise ContractError([ValidationIssue("CANONICAL_JSON_INVALID", "$", "value cannot be encoded as RFC 8785 JSON")]) from exc


def _run_git(repo_root: Path, arguments: list[str], *, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=not binary,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ContractError([ValidationIssue("SOURCE_IDENTITY_UNAVAILABLE", "sourceBinding", "pinned Git source could not be read")])
    return completed.stdout


def resolve_commit(repo_root: Path, revision: str) -> str:
    resolved = str(_run_git(repo_root, ["rev-parse", "--verify", f"{revision}^{{commit}}"])) .strip()
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise ContractError([ValidationIssue("SOURCE_IDENTITY_INVALID", "sourceBinding.commit", "Git commit must be a full SHA-1 identity")])
    return resolved


def source_tree_digest(repo_root: Path, revision: str, package_path: str) -> tuple[str, str, tuple[str, ...]]:
    posix_path = PurePosixPath(package_path)
    if posix_path.is_absolute() or any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ContractError([ValidationIssue("SOURCE_PATH_INVALID", "sourceBinding.packagePath", "package path must be a canonical repository-relative path")])
    commit = resolve_commit(repo_root, revision)
    listing = _run_git(repo_root, ["ls-tree", "-r", "-z", "--full-tree", commit, "--", package_path], binary=True)
    assert isinstance(listing, bytes)
    paths: list[str] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, kind, _object_id = metadata.split(b" ", 2)
            path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContractError([ValidationIssue("SOURCE_TREE_INVALID", "sourceBinding", "Git tree entry is malformed")]) from exc
        if kind != b"blob" or mode == b"120000":
            raise ContractError([ValidationIssue("SOURCE_TREE_INVALID", path, "source tree accepts tracked regular blobs only")])
        paths.append(path)
    paths.sort(key=lambda value: value.encode("utf-8"))
    if not paths:
        raise ContractError([ValidationIssue("SOURCE_TREE_EMPTY", "sourceBinding", "pinned package source contains no tracked files")])
    leaf_digests: list[str] = []
    for path in paths:
        content = _run_git(repo_root, ["show", f"{commit}:{path}"], binary=True)
        assert isinstance(content, bytes)
        leaf = hashlib.sha256(
            SOURCE_BLOB_DOMAIN
            + str(len(content)).encode("ascii")
            + b"\0"
            + path.encode("utf-8")
            + b"\0"
            + content
        ).hexdigest()
        leaf_digests.append(leaf)
    tree = hashlib.sha256(SOURCE_TREE_DOMAIN + "\n".join(leaf_digests).encode("ascii")).hexdigest()
    return commit, tree, tuple(paths)


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    status: str
    public_key: bytes
    not_before: str
    not_after: str | None


def load_trusted_key_ring(content: bytes) -> tuple[TrustedKey, ...]:
    value = load_json_bytes(content, target="trusted-key-ring")
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "keys"} or value.get("schemaVersion") != 1:
        raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", "$", "trusted key ring shape is invalid")])
    keys = value.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", "$.keys", "trusted key ring must contain keys")])
    result: list[TrustedKey] = []
    seen: set[str] = set()
    for index, item in enumerate(keys):
        path = f"$.keys[{index}]"
        if not isinstance(item, dict) or set(item) != {"algorithm", "keyId", "notAfter", "notBefore", "publicKey", "status"}:
            raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", path, "trusted key entry shape is invalid")])
        key_id = item.get("keyId")
        status = item.get("status")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id) or key_id in seen:
            raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", f"{path}.keyId", "key identity is invalid or duplicated")])
        if item.get("algorithm") != SIGNATURE_ALGORITHM or status not in {"active", "next", "retired", "revoked"}:
            raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", path, "key algorithm or status is invalid")])
        not_before = item.get("notBefore")
        not_after = item.get("notAfter")
        if not isinstance(not_before, str) or (not_after is not None and not isinstance(not_after, str)):
            raise ContractError([ValidationIssue("TRUSTED_KEY_RING_INVALID", path, "key validity interval is invalid")])
        seen.add(key_id)
        result.append(TrustedKey(key_id, status, b64url_decode(item.get("publicKey"), expected_bytes=32, path=f"{path}.publicKey"), not_before, not_after))
    return tuple(result)


def _parse_time(value: str, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError([ValidationIssue("TIME_INVALID", path, "timestamp must be RFC 3339")]) from exc
    if parsed.tzinfo is None:
        raise ContractError([ValidationIssue("TIME_INVALID", path, "timestamp must include a timezone")])
    return parsed.astimezone(timezone.utc)


def validate_envelope(value: Any) -> dict[str, Any]:
    required = {
        "schemaVersion", "catalogUri", "catalogSha256", "catalogRevision", "issuedAt", "expiresAt",
        "revocationsUri", "revocationsSha256", "sourceBindings",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("schemaVersion") != ENVELOPE_SCHEMA_VERSION:
        raise ContractError([ValidationIssue("CATALOG_ENVELOPE_INVALID", "$", "catalog envelope shape is invalid")])
    for field in ("catalogSha256", "revocationsSha256"):
        if not isinstance(value.get(field), str) or not _HEX_SHA256.fullmatch(value[field]):
            raise ContractError([ValidationIssue("CATALOG_ENVELOPE_INVALID", f"$.{field}", "digest must be lowercase SHA-256")])
    issued = _parse_time(value.get("issuedAt"), "$.issuedAt") if isinstance(value.get("issuedAt"), str) else None
    expires = _parse_time(value.get("expiresAt"), "$.expiresAt") if isinstance(value.get("expiresAt"), str) else None
    if issued is None or expires is None or expires <= issued:
        raise ContractError([ValidationIssue("CATALOG_ENVELOPE_INVALID", "$.expiresAt", "catalog expiration must follow issuance")])
    bindings = value.get("sourceBindings")
    if not isinstance(bindings, list):
        raise ContractError([ValidationIssue("CATALOG_ENVELOPE_INVALID", "$.sourceBindings", "source bindings must be an array")])
    for index, binding in enumerate(bindings):
        expected = {"packageId", "packageVersion", "repositoryCommitSha", "packagePath", "manifestSha256", "sourceTreeSha256"}
        if not isinstance(binding, dict) or set(binding) != expected:
            raise ContractError([ValidationIssue("SOURCE_BINDING_INVALID", f"$.sourceBindings[{index}]", "source binding shape is invalid")])
        if not _PACKAGE_ID.fullmatch(str(binding.get("packageId", ""))):
            raise ContractError([ValidationIssue("SOURCE_BINDING_INVALID", f"$.sourceBindings[{index}].packageId", "package identity is invalid")])
        for field in ("manifestSha256", "sourceTreeSha256"):
            if not _HEX_SHA256.fullmatch(str(binding.get(field, ""))):
                raise ContractError([ValidationIssue("SOURCE_BINDING_INVALID", f"$.sourceBindings[{index}].{field}", "digest is invalid")])
    return value


def sign_envelope(envelope: dict[str, Any], key_id: str, private_seed: bytes) -> tuple[bytes, bytes]:
    if not _KEY_ID.fullmatch(key_id) or len(private_seed) != 32:
        raise ContractError([ValidationIssue("SIGNING_KEY_INVALID", "signingKey", "Ed25519 test key is invalid")])
    validate_envelope(envelope)
    envelope_bytes = canonical_json(envelope)
    signature = Ed25519PrivateKey.from_private_bytes(private_seed).sign(envelope_bytes)
    signature_set = {
        "schemaVersion": 1,
        "signatures": [{"algorithm": SIGNATURE_ALGORITHM, "keyId": key_id, "signature": b64url_encode(signature)}],
    }
    return envelope_bytes, canonical_json(signature_set)


def verify_envelope(envelope_bytes: bytes, signature_bytes: bytes, key_ring_bytes: bytes, *, now: datetime) -> dict[str, Any]:
    signature_set = load_json_bytes(signature_bytes, target="catalog-signatures")
    if not isinstance(signature_set, dict) or set(signature_set) != {"schemaVersion", "signatures"} or signature_set.get("schemaVersion") != 1:
        raise ContractError([ValidationIssue("CATALOG_SIGNATURE_INVALID", "$", "signature set shape is invalid")])
    signatures = signature_set.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ContractError([ValidationIssue("CATALOG_SIGNATURE_INVALID", "$.signatures", "at least one signature is required")])
    key_ring = {key.key_id: key for key in load_trusted_key_ring(key_ring_bytes)}
    authorized = False
    for index, item in enumerate(signatures):
        if not isinstance(item, dict) or set(item) != {"algorithm", "keyId", "signature"} or item.get("algorithm") != SIGNATURE_ALGORITHM:
            continue
        key = key_ring.get(str(item.get("keyId")))
        if key is None or key.status not in {"active", "next"}:
            continue
        instant = now.astimezone(timezone.utc)
        if instant < _parse_time(key.not_before, "trustedKey.notBefore") or (key.not_after and instant >= _parse_time(key.not_after, "trustedKey.notAfter")):
            continue
        signature = b64url_decode(item.get("signature"), expected_bytes=64, path=f"$.signatures[{index}].signature")
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(signature, envelope_bytes)
            authorized = True
            break
        except InvalidSignature:
            continue
    if not authorized:
        raise ContractError([ValidationIssue("CATALOG_SIGNATURE_UNTRUSTED", "$.signatures", "no active or next trusted signature verified")])
    envelope = validate_envelope(load_json_bytes(envelope_bytes, target="catalog-envelope"))
    if canonical_json(envelope) != envelope_bytes:
        raise ContractError([ValidationIssue("CATALOG_ENVELOPE_NOT_CANONICAL", "$", "envelope bytes are not RFC 8785 canonical JSON")])
    instant = now.astimezone(timezone.utc)
    if instant < _parse_time(envelope["issuedAt"], "$.issuedAt") or instant >= _parse_time(envelope["expiresAt"], "$.expiresAt"):
        raise ContractError([ValidationIssue("CATALOG_ENVELOPE_EXPIRED", "$.expiresAt", "catalog envelope is not currently valid")])
    return envelope


def private_test_key(seed: bytes) -> tuple[bytes, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return seed, public


def _test_key(repo_root: Path, key_id: str) -> bytes:
    fixture_path = repo_root / TEST_KEY_FIXTURE
    value = load_json_bytes(
        read_bounded_file(fixture_path, max_bytes=MAX_JSON_BYTES, target=TEST_KEY_FIXTURE),
        target=TEST_KEY_FIXTURE,
    )
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or value.get("fixtureOnly") is not True:
        raise ContractError([ValidationIssue("TEST_KEY_FIXTURE_INVALID", TEST_KEY_FIXTURE, "test signing fixture is not explicitly fixture-only")])
    keys = value.get("keys")
    if not isinstance(keys, list):
        raise ContractError([ValidationIssue("TEST_KEY_FIXTURE_INVALID", TEST_KEY_FIXTURE, "test signing key list is invalid")])
    for item in keys:
        if isinstance(item, dict) and item.get("keyId") == key_id:
            seed = b64url_decode(item.get("privateSeed"), expected_bytes=32, path="privateSeed")
            _, public = private_test_key(seed)
            declared = b64url_decode(item.get("publicKey"), expected_bytes=32, path="publicKey")
            if public != declared:
                raise ContractError([ValidationIssue("TEST_KEY_FIXTURE_INVALID", TEST_KEY_FIXTURE, "test public key does not match its seed")])
            return seed
    raise ContractError([ValidationIssue("TEST_KEY_FIXTURE_INVALID", TEST_KEY_FIXTURE, "requested test key is missing")])


def compose_test_trust_bundle(repo_root: Path) -> dict[str, bytes]:
    current_path = repo_root / "registry/generated/v1/current.json"
    current = load_json_bytes(read_bounded_file(current_path, max_bytes=MAX_JSON_BYTES, target="generated-current"), target="generated-current")
    if not isinstance(current, dict) or not isinstance(current.get("snapshotUri"), str):
        raise ContractError([ValidationIssue("CATALOG_POINTER_INVALID", "generated-current", "catalog pointer is invalid")])
    catalog_path = current_path.parent / current["snapshotUri"]
    revocations_path = catalog_path.parent / "revocations.json"
    catalog_bytes = read_bounded_file(catalog_path, max_bytes=MAX_JSON_BYTES, target="catalog")
    revocations_bytes = read_bounded_file(revocations_path, max_bytes=MAX_JSON_BYTES, target="revocations")
    catalog = load_json_bytes(catalog_bytes, target="catalog")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("entries"), list):
        raise ContractError([ValidationIssue("CATALOG_INVALID", "catalog", "catalog entries are unavailable")])
    source_bindings: list[dict[str, str]] = []
    for entry in catalog["entries"]:
        if not isinstance(entry, dict):
            raise ContractError([ValidationIssue("CATALOG_INVALID", "catalog.entries", "catalog entry is invalid")])
        manifest_uri = entry.get("manifestUri")
        if not isinstance(manifest_uri, str):
            raise ContractError([ValidationIssue("CATALOG_INVALID", "catalog.entries.manifestUri", "manifest reference is invalid")])
        package_path = str(PurePosixPath(manifest_uri).parent)
        commit, tree_digest, _paths = source_tree_digest(repo_root, "HEAD", package_path)
        source_bindings.append(
            {
                "manifestSha256": str(entry["manifestSha256"]),
                "packageId": str(entry["packageId"]),
                "packagePath": package_path,
                "packageVersion": str(entry["packageVersion"]),
                "repositoryCommitSha": commit,
                "sourceTreeSha256": tree_digest,
            }
        )
    source_bindings.sort(key=lambda item: (item["packageId"], item["packageVersion"]))
    envelope = {
        "catalogRevision": str(catalog["revision"]),
        "catalogSha256": sha256_bytes(catalog_bytes),
        "catalogUri": catalog_path.relative_to(repo_root).as_posix(),
        "expiresAt": TEST_EXPIRES_AT,
        "issuedAt": TEST_ISSUED_AT,
        "revocationsSha256": sha256_bytes(revocations_bytes),
        "revocationsUri": revocations_path.relative_to(repo_root).as_posix(),
        "schemaVersion": 1,
        "sourceBindings": source_bindings,
    }
    key_id = "mkt-test-ed25519-2026-01"
    envelope_bytes, signatures_bytes = sign_envelope(envelope, key_id, _test_key(repo_root, key_id))
    return {
        "catalog-envelope-v1.json": envelope_bytes,
        "catalog-envelope-v1.signatures.json": signatures_bytes,
        "source-bindings-v1.json": canonical_json({"schemaVersion": 1, "sourceBindings": source_bindings}),
    }


def build_test_trust_bundle(repo_root: Path, output: Path | None = None) -> dict[str, Any]:
    destination = output or repo_root / TEST_TRUST_OUTPUT
    expected = compose_test_trust_bundle(repo_root)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in expected.items():
        target = destination / name
        temporary = destination / f".{name}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    output_name = destination.relative_to(repo_root).as_posix() if destination.is_relative_to(repo_root) else "external-output"
    return {"output": output_name, "files": sorted(expected), "status": "synchronized"}


def check_test_trust_bundle(repo_root: Path, output: Path | None = None) -> dict[str, Any]:
    destination = output or repo_root / TEST_TRUST_OUTPUT
    expected = compose_test_trust_bundle(repo_root)
    for name, content in expected.items():
        path = destination / name
        observed = read_bounded_file(path, max_bytes=MAX_JSON_BYTES, target=f"trust/{name}")
        if observed != content:
            raise ContractError([ValidationIssue("TRUST_BUNDLE_OUT_OF_SYNC", f"trust/{name}", "checked-in trust fixture differs from pinned catalog/source bytes")])
    envelope_bytes = expected["catalog-envelope-v1.json"]
    signatures_bytes = expected["catalog-envelope-v1.signatures.json"]
    key_ring_bytes = read_bounded_file(repo_root / TEST_KEY_RING, max_bytes=MAX_JSON_BYTES, target=TEST_KEY_RING)
    envelope = verify_envelope(
        envelope_bytes,
        signatures_bytes,
        key_ring_bytes,
        now=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
    )
    catalog_bytes = read_bounded_file(repo_root / envelope["catalogUri"], max_bytes=MAX_JSON_BYTES, target="catalog")
    revocations_bytes = read_bounded_file(repo_root / envelope["revocationsUri"], max_bytes=MAX_JSON_BYTES, target="revocations")
    if sha256_bytes(catalog_bytes) != envelope["catalogSha256"] or sha256_bytes(revocations_bytes) != envelope["revocationsSha256"]:
        raise ContractError([ValidationIssue("DIGEST_MISMATCH", "trust", "catalog or revocation bytes differ from the signed envelope")])
    return {"output": TEST_TRUST_OUTPUT, "files": sorted(expected), "status": "verified"}
