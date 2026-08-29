# Security Policy

## Current support state

172X Command Marketplace is a public-source developer preview. It has strict v1 contracts, local
validation/build tooling, fixtures, and reviewed package proposals. It has no public installable
catalog, production signing service, downloadable executable runtime, or supported package release.

| Release | Security support |
| --- | --- |
| Public-source developer preview | Reports are handled privately on a best-effort basis |

This policy will be updated with exact supported package versions and response expectations before
public installation is enabled.

## Reporting a vulnerability

Do not report vulnerabilities in a public issue, discussion, pull request, screenshot, or social
post.

Use GitHub private vulnerability reporting:

<https://github.com/mastylolabs/172x-command-marketplace/security/advisories/new>

If GitHub private reporting is unavailable, email [support@172x.ai](mailto:support@172x.ai). Do not
send exploit details through a public issue. Mailbox monitoring must be verified before a supported
package release is announced.

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
