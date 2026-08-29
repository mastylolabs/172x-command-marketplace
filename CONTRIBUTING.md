# Contributing to 172X Command Marketplace

Thank you for your interest in the 172X Command extension ecosystem.

> **Current state:** Public-source contributions are open for the bounded areas below. Package
> installation and production catalog publication remain closed until their separate release gates
> pass.

## Before contributing

Read:

- [README.md](README.md) for the repository boundary;
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for participation standards;
- [SECURITY.md](SECURITY.md) for private vulnerability reporting;
- [GOVERNANCE.md](GOVERNANCE.md) for decision ownership;
- [TRADEMARKS.md](TRADEMARKS.md) for naming and branding rules;
- [DCO.md](DCO.md) for contribution certification.

Do not open a public issue or pull request for a suspected vulnerability, leaked credential,
private user data, or active abuse report.

## Supported contribution areas

Contributions may include:

- declarative Themes;
- Widget source compatible with the host-bundled contract; marketplace bytes are never runtime-loaded;
- declarative Panels and compatible Widget arrangements;
- schemas, validators, examples, documentation, and tests;
- fixes to marketplace metadata and contributor tooling.

Command packages and executable delivery modes are deferred and unsupported by v1. Any later type
requires a separately versioned contract and the product, architecture, UX, security, review, and
human gates named by the project.

Native binaries, unrestricted shell actions, credential providers, arbitrary project/filesystem
access, hidden network behavior, and downloaded code intended for the main 172X Command renderer
are not accepted extension mechanisms.

## Start with an issue

Use the appropriate issue form before implementing a new package type, capability, public contract,
or compatibility change. Small documentation and clearly bounded defect corrections may proceed
directly to a pull request when the behavior is already established.

An accepted proposal is not a promise of inclusion, release, maintenance, endorsement, or a
specific review schedule.

## Package requirements

Every package submission must eventually include the exact artifacts required by its published
contract, including:

- stable namespaced identity and semantic version;
- package and extension API compatibility;
- author, source, license, and issue tracker;
- requested capabilities and plain-language reasons;
- storage schema and migration behavior when storage is used;
- supported and unsupported platforms where relevant;
- deterministic success, loading, empty, unavailable, error, and recovery behavior;
- keyboard, screen-reader, increased-text, reduced-motion, and contrast considerations;
- tests and provenance for source, artwork, screenshots, fonts, and dependencies;
- uninstall, update, rollback, and deprecation behavior where applicable.

Do not include secrets, personal data, production credentials, private source, copied proprietary
assets, telemetry without an approved contract, or code/content you do not have the right to submit.

## Development and validation

The v1 toolchain is Python 3.14 plus repository-local, locked uv dependencies:

```sh
uv sync --locked --all-groups --no-install-project
PYTHONPATH=src uv run --no-sync python scripts/gate.py
```

The complete command validates schemas, fixture expectations, representative and proposed
packages, generated catalog synchronization, documentation/examples/links, pytest, and MkDocs
strict mode. Focused commands and implementation bounds are documented under
`docs/contracts/v1/`. CI uses the same complete command and no secrets.

CI and local tooling fail closed for malformed, duplicate, incompatible, oversized, altered,
unsafe, unknown, incoherent, prohibited, or insufficiently attributed package content. Passing
automation does not self-approve, accept, publish, classify as Official, or support a package.

## Commits and DCO sign-off

Each commit must carry a Developer Certificate of Origin sign-off:

```text
Signed-off-by: Your Name <your-email@example.com>
```

Create it with:

```sh
git commit -s
```

By signing off, you certify the statements in [DCO.md](DCO.md). The sign-off name and email become
part of the permanent public Git history. A pull-request checkbox is not a substitute for commit
sign-off.

## AI-assisted contributions

Contributors remain responsible for every submitted line, asset, dependency, license, test, and
claim regardless of whether an AI tool assisted. Do not send repository secrets, private reports,
user data, or material you cannot disclose to an AI provider. Disclose substantial generated code
or assets when provenance or licensing review could be affected.

## Pull requests

Pull requests should be focused and must use the repository template. Include:

- the problem and observable result;
- linked issue or approved proposal when required;
- package IDs, versions, capabilities, and compatibility affected;
- tests and manual evidence actually run;
- accessibility, security, privacy, migration, and failure/recovery effects;
- dependency, source, license, and asset provenance;
- screenshots only when they materially demonstrate the change.

Maintainers may request changes, split scope, defer work, reject unsupported authority, or close
stale proposals. Review comments and CI checks do not themselves publish a package or grant an
official 172X designation.

## Review outcomes

A submission may be:

- accepted for a future reviewed release;
- accepted as Experimental;
- returned for revision;
- deferred pending a contract or capability decision;
- rejected as incompatible, unsafe, out of scope, unmaintainable, or insufficiently licensed;
- removed or deprecated later under the documented security and compatibility policies.

Only an authorized maintainer may merge. No contributor or automated reviewer approves its own
work for release.
