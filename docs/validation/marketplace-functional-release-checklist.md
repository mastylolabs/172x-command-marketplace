# Marketplace functional-release checklist

This checklist separates the repository’s current private contract/test state from a functional
public marketplace release. It is an operational handoff, not a publication approval.

## Current implementation state

- The repository validates v1 manifests, package proposals, generated catalog snapshots, revocations,
  documentation, and trust fixtures.
- `registry/trust/v1/test/bundle-v1.json` now packages the exact test envelope, detached signature,
  catalog, and revocation bytes expected by Command’s loopback remote-catalog transport.
- Command currently has a private bundled catalog, native exact-byte verification, and test-only
  remote-bundle verification. The application does not yet use a production marketplace endpoint
  or download arbitrary package runtime code.
- Widget delivery remains `host-bundled-source`; Theme and Panel payloads remain inert declarative
  data. A catalog entry does not itself create an install, permission, host binding, or runtime.

## Required before public catalog publication

1. Approve the v1 public contract release and change the release unit from `private` to `public`
   only through a reviewed, versioned repository change. Update package publication/classification/
   maturity claims from `accepted-unpublished` only when the evidence supports them.
2. Complete independent product, architecture, UX, threat/security, accessibility, QA, and PR
   review for the exact Command host integration and package set. Compilation or repository CI is
   not that approval.
3. Create a production Ed25519 signing key in an approved secret store. Keep the private seed out
   of Git, artifacts, logs, and developer machines after the controlled signing setup. Publish only
   the public key and key status/validity interval in the trusted key ring consumed by Command.
4. Define key rotation, compromise, retirement, and emergency revocation owners. Test active/next,
   retired, revoked, expired, altered, mixed-revision, and unavailable-catalog paths before release.
5. Generate one immutable release revision containing the catalog, revocations, manifests, and
   required inert payload bytes. Generate the envelope and detached production signature, then
   verify every digest and source binding from a clean checkout.
6. Publish the revision to one approved static HTTPS origin. Serve `bundle-v1.json` and immutable
   revision resources as read-only bytes with correct content types, cache behavior, TLS, and
   bounded response sizes. A provider account or URL is not implied by this repository.
7. Wire the production Command build to the approved endpoint and production trusted key ring.
   The host must fetch, verify, cache, and atomically replace one complete snapshot; offline or
   invalid refresh must preserve local operation and the last known-good state.
8. Implement and test the host-owned lifecycle needed for the chosen public package set. For
   declarative Themes/Panels this includes bounded download, digest validation, schema validation,
   staging, preview/apply/enable, persistence, rollback, uninstall, and explicit compatibility
   results. For Widgets, source can remain public but only an exact reviewed source tree compiled
   into a compatible Command build may execute.
9. Add a release record binding the marketplace revision, catalog digest, trusted key ID, Command
   host inventory, package manifests, and exact Command build. Promote only that immutable pair.
10. Publish developer docs and the user-facing site projection from the same approved revision.
    The site must distinguish browsing, catalog presence, compatibility, installation, support, and
    Official/Curated status.
11. Assign support and incident owners. Exercise a real revocation/takedown, rollback, cache
    expiry, origin outage, corrupted response, key rotation, and clean recovery before invitation.

## Explicit non-goals for v1

- No downloaded JavaScript, native binary, shell command, terminal action, project/filesystem
  authority, credential provider, unrestricted network, or arbitrary Command Palette callback.
- No Stripe payment or entitlement dependency for marketplace discovery or local use.
- No database, account/session service, write API, telemetry path, or automatic package execution.

## Human decisions still required

- Product owner: which exact v1 package identities and publication labels are supported publicly.
- Architecture/security owners: whether the first public release is browse-only, bundled-host actions,
  or includes declarative payload installation; the latter requires the host lifecycle work above.
- Infrastructure owner: approved static origin, DNS, cache, availability, and secret/signing
  operating model.
- Release owner: exact Command version/platform matrix, release record, beta cohort, rollback, and
  publication approval.
