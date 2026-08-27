# 172X Command Marketplace

Open extensions, widgets, panels, themes, and marketplace registry for 172X Command.

> **Status:** Private pre-release foundation. This repository does not yet publish an installable
> marketplace, supported extension SDK, or public package catalog.

## Purpose

This repository is the planned home of the open 172X Command extension ecosystem. It will define
the public package contracts, validation tooling, examples, registry metadata, and contribution
process used by compatible 172X Extensions.

Planned extension types are:

- **Themes** — declarative semantic appearance packages.
- **Widgets** — bounded cards that can be added to compatible Panels.
- **Panels** — right-side workspaces or declared arrangements of compatible Widgets.
- **Commands** — Command Palette entries backed only by approved host actions.

## Boundaries

172X Command has a proprietary application core and a separately developed open extension
ecosystem. This repository does not contain the Command core, native project or terminal authority,
credentials, signing infrastructure, payment systems, or private release tooling.

The current 172X Command application uses reviewed compile-time Widgets and declarative Themes. A
downloadable executable plugin runtime is not available and must not be inferred from this
repository. Downloaded code will not be executed in the main Command renderer merely because it is
submitted as an extension.

## Initial milestone

Before this repository becomes public, the project intends to establish:

1. a versioned extension manifest and compatibility contract;
2. deterministic package validation and CI;
3. one original declarative Theme package;
4. one small deterministic Widget package;
5. contribution, security, review, deprecation, and takedown policies;
6. end-to-end installation or activation tests through 172X Command;
7. clear Official, Curated Community, Community, Experimental, and Incompatible labels.

## Contribution status

External contributions are not open yet. The repository now records its contribution, conduct,
security, support, governance, DCO, and review policies so they can be tested before publication.
Package templates and local validation commands will be added with the first approved extension
contract. See [CONTRIBUTING.md](CONTRIBUTING.md) before preparing a submission.

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
- Planned website: <https://command.172x.ai>

## Licensing and trademarks

Unless a file states otherwise, repository contents are licensed under the
[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution information.

**172X**, **172X Command**, associated logos, and official-package labels are names and marks of
Mastylo Labs LLC. Apache-2.0 does not grant trademark rights. See
[TRADEMARKS.md](TRADEMARKS.md).

> **We Are 172X. So Are You.**
