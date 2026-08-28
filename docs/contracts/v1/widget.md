# Widget v1

Widget delivery is exactly `host-bundled-source`. Source may be reviewed and compiled into an exact
compatible 172X Command build, but no Widget runtime code is downloaded, installed, dynamically
imported, or loaded from marketplace bytes.

The descriptor requires:

- `requires-compatible-command-build` availability;
- an exact build-inventory identity and truthful bundled/not-bundled state;
- a digest-bound review-source association;
- `runtimeDownload: false` and `runtimeLoading: prohibited`;
- explicit host-provided data inputs and an explicit manifest capability declaration; and
- Panel role and bounded size requirements.

The Clock representative is currently `not-bundled` and declares no package authority. Enabling it
requires an exact compatible 172X Command build containing its inventory identity. There is no
runtime-download fallback. The validator reads and scans review source but never imports, compiles,
or executes it.

Widget placement is not implemented here. A later host must re-evaluate suitability and obtain an
explicit user-selected Panel instance and stable slot; it may not select a default Panel silently.

See the validated [Widget JSON example](../../examples/v1/widget.json).
