# Manifest v1

Every package version has one strict `172X-MKT-MANIFEST-001` v1 manifest. Unknown fields and
unsupported majors fail closed.

Required dimensions remain separate:

- immutable namespaced ID, strict `X.Y.Z` package version, type, and delivery mode;
- display name, bounded summary, and bounded description;
- exact source path/revision, declared authors, and optional upstream attribution;
- SPDX package license, digest-bound license file, and third-party notices;
- host, extension API, type-contract, platform, dependency, and conflict compatibility axes;
- an explicit capability array, including an empty array when no package authority is requested;
- lifecycle state, data-retention meaning, and rollback mode;
- SHA-256 and size for every package payload;
- exact v1 developer-document references, also digest-bound as payloads; and
- independent source, author, provenance, review, maintenance, security, and non-Official facts.

Package-controlled trust fields are deliberately least-claiming. Package bytes may declare author
identity, limited/unknown maintenance, digest/source correlation, unreviewed or open-finding state,
and non-Official status. They cannot assert Mastylo maintenance, repository-control observation,
active maintenance, build correlation, automation/independent review, or completed security review.
Classification/publication/maturity remain maintainer-owned release-descriptor facts. There is no
package-author route to a human or maintainer decision.

Every display-oriented name, summary, description, author, attribution, project, topic, and
capability-purpose value is inert plain text at every dictionary or array nesting depth. Raw HTML,
scripts, event-handler forms, scriptable URI schemes, control characters, and entity/percent-encoded
equivalents fail with `PLAIN_TEXT_UNSAFE`. Benign Unicode and punctuation remain valid. Future
consumers must still context-escape these strings when rendering them.

Theme/Panel=`declarative-data`; Widget=`host-bundled-source`. `skin`, `command`, unknown types,
unknown delivery modes, and type/delivery reinterpretation are rejected.

All catalog, manifest, payload, license, notice, source, and developer-doc references in the
machine contract are bounded same-origin relative paths. Absolute, drive-qualified, traversal,
query, fragment, scriptable, percent-encoded, and backslash forms fail closed.
