# Governance

## Project ownership

172X Command Marketplace is published and governed by **Mastylo Labs LLC**. The initial repository
maintainer and CODEOWNER is [@zmastylo](https://github.com/zmastylo).

This repository develops the open extension ecosystem for the proprietary 172X Command
application. Open-source contribution does not transfer ownership of the Command core, its private
interfaces, release authority, trademarks, or commercial systems.

## Roles

### Maintainers

Maintainers may triage issues, review changes, merge pull requests, manage package status, maintain
contracts and tooling, apply security fixes, deprecate or remove packages, and prepare releases.
Maintainer access is granted and revoked by Mastylo Labs LLC.

### Contributors

Contributors submit issues, proposals, documentation, code, tests, and packages under the repository
license and DCO. Contribution does not guarantee merge, publication, continued compatibility,
maintainer status, support, or official-package designation.

### Package maintainers

A package may identify one or more maintainers responsible for its source, compatibility,
vulnerabilities, migrations, and deprecation. Marketplace maintainers retain final catalog and
security authority even when a package has separate maintainers.

## Decisions

Routine, reversible decisions are made through reviewed issues and pull requests. Material changes
to public schemas, capability authority, compatibility, signing/provenance, security policy,
trademark use, contributor terms, or release behavior require explicit approval from Mastylo Labs
LLC and may require product, architecture, security, legal, or accessibility review.

The project prefers documented evidence and the smallest compatible change. Missing evidence is not
treated as approval. Maintainers may run bounded experiments without promising the result as a
public contract.

## Review and merge

- Changes enter `main` through pull requests after repository protection is enabled.
- Required CI must pass.
- CODEOWNER review is required when the repository has enough independent maintainers to satisfy
  it without creating an impossible rule.
- Authors and automated reviewers do not approve their own work for release.
- Security-sensitive or contract-changing work may require additional independent review.
- Merging source does not automatically publish a package or release.

## Package status and removal

Packages may be labeled Official 172X, Curated Community, Community, Experimental, Deprecated, or
Incompatible according to published evidence. Open source, source review, catalog inclusion, and
official maintenance are separate properties.

Mastylo Labs LLC may reject, suspend, deprecate, delist, or remove a package from the catalog for
security, legal, trademark, compatibility, abandonment, provenance, quality, or conduct reasons.
Where safe and practical, the project will document the reason and provide migration or removal
guidance. Emergency security action may precede public explanation.

## Changes to governance

Governance changes require a pull request and explicit maintainer approval. Material contributor,
license, trademark, or security-policy changes require Mastylo Labs LLC approval and may require
professional review.
