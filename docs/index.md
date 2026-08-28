# Private Wave 1 developer contracts

This repository contains the private, local Wave 1 contract release for the planned 172X Command
extension ecosystem. Nothing in this documentation is a public catalog, supported SDK, installable
marketplace, package release, provider deployment, or Command host integration.

The technical taxonomy is fixed for v1:

- **Extension** is the ecosystem umbrella.
- **Theme** is the manifest type; **Skin** is only a one-to-one user-facing synonym.
- **Theme** and **Panel** use inert `declarative-data` delivery.
- **Widget** uses `host-bundled-source`; marketplace bytes are never runtime-loaded.
- **Command** and all executable delivery modes are unsupported in v1.

Start with the [v1 release identity](contracts/v1/index.md), then use the type-specific and
validation references. Product/user documentation remains owned by `172x-command-site`; this site
is the canonical repository-local source only for marketplace developer contracts.
