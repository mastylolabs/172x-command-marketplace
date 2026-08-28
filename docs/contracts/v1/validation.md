# Validator and static builder v0.1.0

The repository-local `marketplacectl` validates the exact v1 schemas, semantic invariants,
integrity bindings, source packages, fixture expectations, docs synchronization, and generated
catalog snapshot. It emits deterministic JSON, stable reason codes, exit `0` for success, exit `1`
for contract failure, and exit `2` for usage or unexpected tooling failure.

Setup and focused commands:

```sh
uv sync --locked --all-groups --no-install-project
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli schemas
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli fixtures
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli package packages/org.catppuccin.mocha/1.0.0/manifest.json
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli package packages/com.mastylolabs.clock/1.0.0/manifest.json
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli build --check
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli docs
```

The complete local gate is:

```sh
PYTHONPATH=src uv run --no-sync python scripts/gate.py
```

It runs repository verification, the complete pytest suite, MkDocs with `--strict`, and a
post-build site-content check. The checked-in CI workflow uses immutable action commit identities
and runs that same no-build command without secrets or a package build backend. This is local
implementation evidence only;
it is not remote CI, independent QA, security review, PR approval, publication, or release.

## Stable reason families

The exact [v1 reason-code registry](reason-codes.md) distinguishes malformed or duplicate JSON/schema, unknown fields/majors/types/delivery, incompatible
axes, unsafe paths/URIs, inactive Theme/Panel violations, Widget runtime/source violations,
duplicates, digest/size alteration, catalog coherence, missing references, limits, stale generated
output, active metadata/docs, output ownership/concurrency/recovery, and immutable output revision
conflict. Tests assert the exact codes used by every fixture.

## Determinism and failure behavior

JSON is serialized with sorted keys, UTF-8, two-space indentation, and one terminal newline. Package
and issue ordering is explicit. `PYTHONHASHSEED=0` is set by the complete gate as an additional
reproducibility guard. The builder validates all inputs and staged outputs before atomically
replacing `current.json`; a failed rebuild preserves the prior generated output byte for byte.
Snapshot validation checks every authoritative `generatedFrom` file digest against current
repository bytes and checks fixed artifact/source/version identities. Builder staging invokes this
same complete snapshot validator before any output publication step.

All JSON parsing recursively rejects duplicate member names before schema interpretation. Public
diagnostics use repository-relative logical targets or `external-input`, fixed bounded messages,
sanitized control characters, stable reason codes, and useful JSON paths; they do not echo rejected
values or absolute repository roots. Unexpected CLI exceptions map to one fixed internal failure.

The MkDocs input set is allow-bounded to developer contract/example pages. Fixed
`docs/architecture/**` and `docs/README.md` remain repository review inputs but are excluded
automatically from site output. Internal validation evidence stays outside the Git-visible docs
tree. Source checks reject raw HTML, active or scriptable
Markdown/HTML constructs, encoded equivalents, and disallowed controls while retaining benign
Unicode and punctuation. The post-build check independently constrains rendered developer-content
tags and attributes and rejects active/raw content, review-only routes, and known private
Command/root/branch/IPC markers.
