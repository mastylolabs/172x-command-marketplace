# Documentation

This directory contains the private Wave 1, versioned developer-contract documentation for 172X
Command Marketplace. It builds locally and in repository CI only. No public Read the Docs project,
custom domain, package catalog, or supported developer release exists.

The current v1 private documentation includes:

- extension manifest and package schemas;
- compatibility and versioning policy;
- capability and permission reference;
- Theme, Widget, and Panel contracts; Command remains deferred and unsupported;
- local validation and testing;
- package review, trust labels, deprecation, takedown, and security response;
- registry/catalog format and release process.

The machine-readable release owner is `contracts/v1/release.json`; start at
`docs/contracts/v1/index.md`. Examples and representative packages are private validation inputs,
not supported 172X Command behavior. Product/user documentation remains owned by
`172x-command-site` and must link to, not duplicate, these developer contracts.
