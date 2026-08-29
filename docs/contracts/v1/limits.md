# v1 implementation bounds

These values are finite parser/fixture bounds for this v1 implementation. They are not
evidence-backed public scale, support, performance, availability, or compatibility limits.

| Item | v1 bound |
| --- | ---: |
| JSON file | 262,144 bytes |
| Individual package payload | 1,048,576 bytes |
| Total payload bytes per package | 4,194,304 bytes |
| Individual developer-documentation or MkDocs configuration input | 4,194,304 bytes |
| Relative reference | 240 characters |
| Catalog entries | 512 |
| Revocations | 1,024 |
| Manifest payload records | 16 |
| Capability declarations | 8 |
| Dependencies or conflicts | 16 each |
| Panel slots | 16 |
| Slot occupancy maximum | 8 |
| Widget/Panel grid dimensions | 1–12 |

Semantic versions are strict `X.Y.Z`. The only v1 range grammar is `>=X.Y.Z <X.Y.Z`. The current
validation context is host `0.1.0`, extension API `1.0.0`, type-contract major `1`, and the
`platform-neutral`/`host-build-defined` markers. Those values make incompatible fixtures
deterministic; they are not public 172X Command support claims.

Boundary tests cover each activated maximum and one-over rejection where applicable. Revisit any
bound only through a versioned contract change supported by measured implementation evidence.

The validator uses `lstat` and non-following bounded file reads. An individual payload or the next
payload that would exceed the cumulative package bound is rejected before content hashing,
decoding, or JSON parsing. The same open file descriptor supplies size, bytes, digest, decode, and
semantic validation, with identity checks before and after the bounded read. Very large regular and
sparse-file regressions exercise the short circuit; exact-boundary payload bytes remain accepted.

After the cumulative preflight, one non-following open descriptor supplies the accepted payload
bytes, final size/identity checks, digest, decode, and semantic input.

Source validation assumes the repository workspace and its existing real-directory ancestors are
operator-controlled while a validation invocation runs. Static symlink/non-regular sources are
rejected. An actor able to replace repository ancestors or mutate an already opened inode can still
deny service; no cross-platform hostile-filesystem or network-filesystem guarantee is claimed.

The developer-documentation preflight inventories every MkDocs-consumed source path before reading
one, rejects symbolic links and non-regular inputs, and then uses the bounded non-following reader.
The same preflight binds the exact v1 MkDocs configuration before the gate may execute
MkDocs. Review-only `docs/architecture/**` and `docs/README.md` remain outside the generated
documentation source set; internal validation evidence stays outside the Git-visible docs tree.
