# Panel v1

A Panel package is inert `declarative-data`; Wave 1 supplies this contract and fixtures only. No
representative Panel package, host Panel surface, automatic placement, or controller exists.

Each payload declares a host-owned layout role and one to sixteen stable slot IDs. Every slot
expresses accepted Widget type-contract/API ranges, a semantic role, bounded size hints, finite
behavior hints, conflicts, and occupancy rules. Duplicate slot IDs fail closed.
An accepted Widget API range uses `>=X.Y.Z <X.Y.Z`; its lower bound must be strictly below its upper
bound. Malformed, inverted, and empty intervals fail with `SEMVER_RANGE_INVALID`.

`automaticPlacement` is always false. Package-defined code, render/controller/module callbacks,
native authority, dock control, automatic Widget selection, and implicit placement are prohibited.
Suitability is only representable metadata until a separately authorized host implements pure
evaluation and explicit placement.

See the validated [Panel JSON example](../../examples/v1/panel.json).
