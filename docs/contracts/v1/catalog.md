# Catalog and revocations v1

`catalog.schema.json` describes one complete static index. `revocations.schema.json` describes the
matching tombstone set. Both carry the same immutable `revision`; every index entry repeats it.
Mixing revisions fails with `CATALOG_MIXED_REVISION` or `REVOCATION_INCOHERENT`.

Catalog identity is the tuple of package ID, package semantic version, manifest SHA-256, type, and
delivery mode. Duplicate package ID/version tuples fail closed. Each manifest reference is a
bounded same-origin relative path, and its bytes must match `manifestSha256` before package data is
accepted. The index binds canonical revocation bytes through `revocationsSha256`.

The generated release remains `private`. Representative entries are `accepted-unpublished`,
`community`, and `experimental`; those independent values are not endorsement, public inclusion,
support, or safety claims.

The builder sorts by case-folded display name, package ID, then package version. Identical inputs
therefore produce byte-identical canonical JSON and `SHA256SUMS` files.
Snapshot validation resolves every `generatedFrom` contract-release, developer-doc, schema, and
release-source binding to its exact repository path and current SHA-256 bytes. It also verifies the
fixed architecture, build-gate, source-identity, and validator-version identities.

## Recovery

The builder creates and completely validates an isolated snapshot before changing repository
output. It requires an existing operator-owned output parent that is not group/world writable,
rejects symlink and non-regular components or leaves, and holds one atomic sibling ownership lock
for the complete read/build/publish/recovery interval. A process with authority to mutate that
protected parent while ignoring the lock is outside the supported single-writer assumption; the
builder still detects observed identity conflicts and does not delete conflicting data.

Any staged/output write, fsync, snapshot rename, pointer write, pointer replacement, or cleanup
failure returns nonzero. Recovery journals only paths created or replaced by the invocation and
checks their filesystem identity before rollback. With no violating foreign writer, a failed first
build leaves no generated output root and a failed rebuild restores prior paths and bytes exactly.
If a foreign entry appears concurrently, it is preserved byte for byte, the build reports an
output-concurrency failure, and the output root may remain because deleting it would delete foreign
data. Lock contention fails before reading or changing the output namespace.
