# Stable reason codes v1

`contracts/v1/reason-codes.json` is the strict machine-readable registry. Codes are stable within
v1: a code's meaning is not repurposed, and an unrecognized condition fails closed instead of being
coerced into success.

The families are:

- `SCHEMA_*`, `JSON_INVALID`, `JSON_DUPLICATE_KEY`, and `CONTRACT_MAJOR_UNSUPPORTED` for structural/version failures;
- `TYPE_*`, `DELIVERY_UNSUPPORTED`, `SEMVER_*`, and `INCOMPATIBLE_*` for identity and compatibility;
- `PATH_*`, `URI_UNSAFE`, `FILE_TYPE_UNSAFE`, and `FILE_CHANGED` for unsafe or unstable references;
- `THEME_*`, `PANEL_*`, `WIDGET_*` for type-specific inertness, bounds, and delivery truth;
- `DIGEST_*`, `SIZE_MISMATCH`, `IDENTITY_MISMATCH`, and `MANIFEST_PAYLOAD_MISSING` for package integrity;
- `CATALOG_*`, `REVOCATION_INCOHERENT`, `DUPLICATE_*`, `SIGNATURE_*`, `TRUST_*`, and `SOURCE_*` for release coherence, signatures, and pinned source identity;
- `PLAIN_TEXT_UNSAFE`, `DOC_*`, `CONTRACT_SYNC_FAILED`, and `GENERATED_OUTPUT_STALE` for inert text and documentation/generated parity;
- `OUTPUT_*` for path ownership, lock contention, I/O, immutable revision, concurrent mutation, and cleanup behavior; and
- `FIXTURE_*`, `FILE_NOT_FOUND`, `FILE_READ_FAILED`, `LIMIT_EXCEEDED`, and `INTERNAL_ERROR` for bounded tooling failures.

CLI output is deterministic JSON containing `valid` or `status`, plus ordered issue objects with
`code`, `path`, and `message`. Consumers branch on `code`, never parse the human-readable message.
Messages are fixed, bounded, control-sanitized, and non-echoing; paths are logical JSON or
repository-relative targets, with external paths represented as `external-input`.
