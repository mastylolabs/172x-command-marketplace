# Security Policy

## Current support state

172X Command Marketplace is a private pre-release repository. It has strict private Wave 1
contracts, local validation/build tooling, fixtures, and two representative source packages. It
has no public package catalog, supported extension SDK, Command host integration, downloadable
executable runtime, or supported public release.

| Release | Security support |
| --- | --- |
| No public release | Pre-release reports are handled privately on a best-effort basis |

This policy will be updated with exact supported versions and response expectations before the
repository or packages are publicly released.

## Reporting a vulnerability

Do not report vulnerabilities in a public issue, discussion, pull request, screenshot, or social
post.

When GitHub private vulnerability reporting is enabled for this repository, use:

<https://github.com/mastylolabs/172x-command-marketplace/security/advisories/new>

While the repository remains private, invited collaborators should contact repository owner
[@zmastylo](https://github.com/zmastylo) privately through an established collaborator channel.
Before public contributions open, Mastylo Labs LLC will verify that private vulnerability reporting
works and publish a monitored fallback contact method.

Include only what is necessary to reproduce and assess the issue:

- affected package, version, contract, or commit;
- impact and required preconditions;
- minimal reproduction or proof of concept;
- whether secrets or user data may have been exposed;
- known mitigations;
- a safe way to contact the reporter.

Do not include real credentials, unrelated personal data, destructive payloads, or access beyond
what is necessary to demonstrate the issue.

## Scope

Relevant reports include vulnerabilities in:

- package schemas, validation, compatibility, or capability enforcement;
- registry integrity, provenance, signing, update, rollback, or revocation;
- contributor and release automation;
- extension isolation or unauthorized access to Command capabilities;
- cross-package storage, data leakage, or privilege escalation;
- official reference packages or tooling maintained in this repository.

Reports about the proprietary 172X Command application may require a separate private channel. Do
not disclose private Command implementation details in this marketplace repository.

## Disclosure

Mastylo Labs LLC will validate scope, coordinate remediation, and determine disclosure timing based
on available evidence and affected releases. No response time, bounty, embargo acceptance, or
payment is promised unless separately agreed in writing.
