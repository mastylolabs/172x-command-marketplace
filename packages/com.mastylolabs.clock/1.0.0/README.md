# Clock Widget — private representative source

This package is the Wave 1 `host-bundled-source` Widget example. The TypeScript source is present
for review and possible compilation into a separately authorized, exact compatible 172X Command
build. It is not currently bundled into a Command build.

No Widget runtime code is downloaded, installed, dynamically imported, or loaded from marketplace
bytes. Marketplace validation never imports or executes the source. Enabling requires an exact
compatible 172X Command build whose host-owned build inventory contains
`com.mastylolabs.clock@1.0.0`; otherwise the only truthful state is
`requires-compatible-command-build`.

The source renders only a host-provided clock instant. The v1 manifest declares no package
capabilities and grants no filesystem, project, terminal, shell, native, credential, storage,
network, telemetry, or private Command authority. Placement always requires an explicit user
selection of a suitable Panel slot in a later authorized host implementation.

This package is private and `accepted-unpublished`; it is not an Official package, a supported
release, an installable runtime package, or evidence of public availability or safety.
