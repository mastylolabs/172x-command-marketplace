# Lifecycle, trust, and non-guarantees

Wave 1 packages are private source artifacts in `accepted-unpublished` state. They are not public,
installed, enabled, active, placed, supported, released, or provider-hosted.

Trust is a vector. Source availability, author evidence, package license, provenance/integrity,
review, classification, maturity, maintenance, compatibility, requested capabilities, lifecycle,
and security posture remain independent. Catalog inclusion, source availability, validation, a
digest, compilation, or review is not a safety guarantee or an endorsement.

No self-approval is permitted. Package data can declare source/authorship/license/capability facts,
but `official` is fixed false and package trust values are restricted to least-claiming states in
private manifests. Package bytes cannot assert Mastylo maintenance, active maintenance, build
correlation, automation/independent review, or completed security review. The release
descriptor—not package payloads—sets private publication/classification/maturity metadata.
Independent QA and security review are the next receivers; a later independent PR reviewer and
human retain approval/publication decisions.

Digest mismatch detects alteration relative to recorded bytes. It cannot prove publisher identity,
author control, legal sufficiency, absence of vulnerabilities, compatibility beyond named axes, or
survival of combined source/publication compromise. Offline consumers cannot learn new revocations;
that later host risk remains explicit.

No public/provider/commerce/host action is activated by these contracts. Catalog/docs/provider
failure must never become authority over local files, terminals, projects, processes, recovery, or
free-core behavior.
