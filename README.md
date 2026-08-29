# 172X Command Marketplace

Open-source contracts, validation tooling, reviewed package proposals, and static catalog source
for the 172X Command extension ecosystem.

> **Status:** Public-source developer preview. Strict v1 contracts, local tooling, fixtures,
> developer documentation, and reviewed package proposals are available. Public installation,
> production catalog signing, and downloaded executable extensions are not available.

## Purpose

This repository is the canonical owner of the planned marketplace package contracts, validation
tooling, examples, static registry metadata, and developer documentation. Repository publication
does not make any package installable, supported, Official, or included in a signed release.

The v1 technical taxonomy is:

- **Extension** — ecosystem umbrella.
- **Theme** — inert semantic appearance data using `declarative-data`; **Skin** is only a
  one-to-one user-facing synonym and never a manifest type.
- **Widget** — reviewed source using `host-bundled-source`; runtime code is available only when
  compiled into an exact compatible Command build and is never downloaded from marketplace bytes.
- **Panel** — inert host-owned layout/slot descriptors using `declarative-data`.
- **Command** — deferred and unsupported by v1, along with every executable delivery mode.

## Boundaries

172X Command has a proprietary application core and a separately developed open extension
ecosystem. This repository does not contain the Command core, native project or terminal authority,
credentials, signing infrastructure, payment systems, or private release tooling.

The current 172X Command application uses reviewed compile-time Widgets and declarative Themes. A
downloadable executable plugin runtime is not available and must not be inferred from this
repository. Downloaded code will not be executed in the main Command renderer merely because it is
submitted as an extension.

## Current contents

The current coherent local release contains:

1. strict catalog, revocations, manifest, Theme, Widget, and Panel v1 schemas;
2. a deterministic validator and atomic static catalog builder with stable reason codes;
3. valid/invalid fixtures for schema, semantics, integrity, compatibility, duplicates, unsafe
   references/content, coherence, and implementation bounds;
4. one declarative Catppuccin Mocha Theme with explicit upstream/MIT attribution;
5. one Clock Widget review-source package with truthful `host-bundled-source`/not-bundled state;
6. Panel contracts and fixtures, but no Panel package or placement implementation;
7. versioned MkDocs developer docs plus local/CI synchronization and link checks; and
8. a source-controlled CI workflow that runs the same complete local gate without secrets.

No host integration, package install/enable/apply/place runtime, service, account, database,
payment, telemetry, provider configuration, public URL, or executable package path is included.

## Local validation

Python 3.14 and [uv](https://docs.astral.sh/uv/) are used through the repository lockfile:

```sh
uv sync --locked --all-groups --no-install-project
PYTHONPATH=src uv run --no-sync python scripts/gate.py
```

Create a deterministic package proposal without overwriting an existing package:

```sh
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli scaffold --type theme --id com.example.my-theme --name "My Theme"
PYTHONPATH=src uv run --no-sync python -m marketplace_contracts.cli package packages/com.example.my-theme/1.0.0/manifest.json
```

The scaffold command also accepts `widget` and `panel`. Widget output is review source only and
remains unavailable until that exact package is reviewed and compiled into a compatible Command
build. Scaffolding does not add a package to a release, sign it, or publish it.

Focused commands and stable failure behavior are documented in the
[v1 validation reference](docs/contracts/v1/validation.md). A local pass is implementation evidence,
not independent QA/security review, PR approval, public support, publication, or release.

## Contribution status

External issues and pull requests are open for the bounded contribution areas described in
[CONTRIBUTING.md](CONTRIBUTING.md). Every submission remains subject to validation, provenance,
security, accessibility, maintainer review, and DCO requirements. Acceptance or merge does not
publish a package or grant Official status.

Community and governance documents:

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Governance](GOVERNANCE.md)
- [Developer Certificate of Origin](DCO.md)
- [Trademark Policy](TRADEMARKS.md)
- [Changelog](CHANGELOG.md)

## Product links

- Product: **172X Command**
- Publisher: **Mastylo Labs LLC**
- Website: <https://command.172x.ai>

## Licensing and trademarks

Unless a file states otherwise, repository contents are licensed under the
[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution information.

**172X**, **172X Command**, associated logos, and official-package labels are names and marks of
Mastylo Labs LLC. Apache-2.0 does not grant trademark rights. See
[TRADEMARKS.md](TRADEMARKS.md).

> **We Are 172X. So Are You.**
