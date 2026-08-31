# Plan-only release-candidate validation

The release-candidate gates use fabricated files created beneath temporary test directories. They never inspect a private library and never move, copy, rename, overwrite, or delete media.

`tests/local/test_release_candidate_stress.py` builds a blocked review corpus. It accounts for every video while exercising ambiguous editions, weak provider matches, conflicting extra evidence, duplicate provider-episode releases, destination collisions between different logical identities, unsupported adjacent files, and whole-plan preflight rejection.

`tests/local/test_release_candidate_ready.py` builds the corrected ready corpus. It covers provider-title matching, aliases, aired and season-zero numbering, multi-episode files, absolute and parenthesized-absolute numbering, distinct segments, specials, date matching, extras, subtitle companions, Unicode/mojibake recovery, and invalid destination-character encoding.

The ready corpus contains 15 video records and four companion records. Its approved plan-only context is:

- tool version: `0.1.0`;
- plan schema: `1`;
- provider cache: ten successful synthetic snapshots;
- immutable plan hash: `0b23404313c3f2e32c158d8edaf4b48634a589b9e632a038d0d62f141a3456f4`.

The test fixes all synthetic timestamps and the cache clock, regenerates the exact approved hash, and then repeats the plan against the warmed cache in offline mode. Offline replay must make zero provider calls and reproduce identical plan JSON, summary output, provenance, and plan hash. Both source snapshots remain unchanged and the destination roots remain empty.

This approval is only for the non-mutating planning milestone. It does not authorize media mutation or imply approval for a future apply implementation.
