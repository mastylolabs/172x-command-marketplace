# Package authoring

External source contributions are open. These steps describe reproducible work on package
proposals; validation or merge does not make a package installable or published.

1. Choose one v1 type/delivery pair: Theme/Panel=`declarative-data` or
   Widget=`host-bundled-source`.
2. Create immutable package ID/version paths and strict manifest/type JSON.
3. Record exact author, source, revision, package license, third-party attribution, lifecycle,
   trust, compatibility, capabilities, and docs facts without claiming Official/public/support.
4. Bind every payload, license, notice, documentation file, and Widget review source by SHA-256 and
   byte size using repository-root relative references.
5. Run the package validator, fixture/docs checks, builder synchronization check, tests, and strict
   MkDocs gate.

Do not add downloaded runtime code, dynamic import, raw CSS, package-defined Panel controllers,
Command type, native/shell/terminal/filesystem/project/credential/network authority, telemetry,
private Command interfaces, provider settings, secrets, or public URLs.

A local validation pass means only that the exact bytes met this contract. It does not accept a
contribution, publish a package, grant trademark permission, prove safety, or authorize host use.
