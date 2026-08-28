# Contract release v1

| Field | Value |
| --- | --- |
| Artifact | `172X-MKT-CONTRACTS-001` |
| Contract version | `v1` |
| Date | `2026-08-27` |
| State | **private** |
| Source identity | `172X-W1-PRIVATE-CONTRACTS-v0.1` |
| Validator version | `0.1.0` |
| Developer-doc state | `private-local-ci-only` |

The machine-readable owner is `contracts/v1/release.json`. It binds this docs version to the
catalog, revocations, manifest, Theme, Widget, and Panel schemas under `schemas/v1/` and to the
validator version. Generated catalog metadata records SHA-256 identities for those exact files,
the source release descriptor, this page, the fixed architecture, and the private build gate.

SHA-256 detects alteration relative to the recorded release, but it does not prove publisher
identity or package safety. Source availability, authorship, license, integrity, review,
classification, maturity, maintenance, compatibility, capabilities, lifecycle, and security are
separate facts; no single field is a general trust guarantee.

## Canonical ownership

- Catalog, revocations, manifest, package-type schemas, validator, builder, examples, and these
  developer docs are owned canonically in this repository.
- Actual local lifecycle, compatibility decisions, grants, activation, placement, and recovery
  remain private-host responsibilities and are not implemented in Wave 1.
- Product/user Docs remain site-owned and may link to a later published exact version; they must
  not duplicate these contracts.

## Current release contents

The source descriptor lists exactly two private representative packages: Catppuccin Mocha Theme
and Clock Widget source. There is no representative Panel package and no Command integration.

Continue with [catalog and revocations](catalog.md), [manifest](manifest.md), and
[validation and builder behavior](validation.md).
