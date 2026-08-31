# Jellyfin Media Organizer (JMO) architecture

## Purpose

`jellyfin_show_organizer` is the standalone Python package that provides the core of Jellyfin Media Organizer (JMO). It builds a deterministic, auditable plan for TV-show media organization before any filesystem mutation is allowed.

The current project boundary is deliberately narrow:

1. authorize one Shows root;
2. inventory eligible video files read-only;
3. parse filename hints deterministically;
4. resolve each source show to one canonical provider identity;
5. resolve catalog/provider evidence through explicit data and cache layers;
6. classify explicit video extras before episode assignment;
7. assign episode identities from one canonical cached catalog per source-show group;
8. construct deterministic Jellyfin destination paths from canonical assignments and extra decisions;
9. build versioned plan records;
10. reconcile every expected source into an explained terminal status;
11. audit the completed plan before any future apply implementation.

There is currently no `apply` command.

## Package layout

- `cli.py` — operational plan-only command surface and configuration precedence.
- `planner.py` — end-to-end inventory, resolution, assignment, duplicate, companion, provenance, preflight, and audit orchestration.
- `inventory.py` — authorized-root checks and deterministic read-only video inventory.
- `filename_parser.py` — pure filename/path hint parsing without filesystem or provider access.
- `extra_classifier.py` — deterministic pre-assignment extra classification with fail-closed ambiguity handling.
- `show_resolver.py` — conservative show-level canonical TVMaze resolution with explicit ambiguity states.
- `episode_assignment.py` — deterministic show-level episode mapping from one normalized cached provider catalog using explicit numbering policies.
- `destination.py` — deterministic Jellyfin-relative destination construction and cross-platform sanitization/collision keys.
- `models.py` — typed cross-stage contracts.
- `overrides.py` — data-driven aliases, numbering modes, years, provider IDs, and title preferences.
- `reconciliation.py` — one explained terminal inventory status per expected path.
- `schema.py` — versioned manifest validation, serialization, and stable plan hashing.
- `reports.py` — atomic immutable audit-bundle serialization from the canonical plan.
- `preflight.py` — whole-plan safety validation before any future mutation stage.
- `sidecars.py` — read-only adjacent companion discovery and destination derivation.
- `tvmaze_cache.py` — persistent provider-cache primitives with explicit cache/network/error state.
- `data/` — versioned JSON/TOML contracts and synthetic default override examples.

## Determinism

Equivalent input evidence should produce equivalent typed results and stable plan hashes. Run-local timestamps, machine paths, and other environment-specific metadata do not belong in the immutable plan contract.

Filename parsing, extra classification, and destination construction are pure and offline. Provider-backed matching consumes cached/provider-shaped evidence rather than hiding network access inside parsing, classification, or naming logic.

## Extra classification contract

Explicit video extras are isolated before provider-backed episode assignment. The normalized extra kinds are:

- `creditless-opening`
- `creditless-ending`
- `trailer`
- `featurette`
- `interview`
- `behind-the-scenes`
- `deleted-scene`
- `clip`
- `extra`

The classifier uses explicit filename markers and recognized immediate extra-folder names. Strong explicit extra markers may classify a file even when its name also contains unrelated numeric noise. Strong episode evidence combined with explicit extra evidence is not guessed in either direction; it becomes suspicious for review. Conflicting extra kinds are also suspicious. Ambiguous words such as `bonus`, `special`, or `ova` remain unresolved unless later pipeline evidence can handle them safely.

Classification returns its parsed filename evidence, normalized extra decision when one exists, and stable textual reasons so later plan/report stages can preserve why the decision was made. The classifier performs no filesystem or network access.

## Episode assignment contract

Episode assignment runs only after a source-show group has one canonical provider identity and explicit video extras have been removed from ordinary episode matching. The whole group consumes one cached episode catalog; assignment never performs an independent show search or provider lookup per source file.

Supported numbering policies are explicit:

- `aired` maps exact season/episode coordinates, including season zero without rewriting specials into season one;
- `absolute` and `parenthesized-absolute` map through deterministic regular-episode catalog order while leaving specials outside that sequence;
- `segment-title` preserves segment hints and requires exact normalized catalog-title evidence.

Multi-episode sources preserve every requested episode in deterministic source order and remain one source assignment. A missing member prevents a partial match. Mixed numbering evidence, configured-policy conflicts, conflicting provider identities, malformed or duplicate catalog entries, and distinct segments that collapse to the same provider episode fail closed as suspicious or unresolved with stable reasons.

Assignment evidence records the active numbering policy, cached catalog request identity, and provider episode mappings so later immutable plans and audit reports can explain the decision without rerunning provider discovery.

## Destination construction contract

Destination construction runs after episode/extra identity is known and never reparses source filenames. Its default series layout is Jellyfin-oriented and host-independent:

```text
Series Title (2024)/
  Season 00/
    Series Title (2024) S00E01 - Special Title.mkv
  Season 01/
    Series Title (2024) S01E01 - Episode Title.mkv
    Series Title (2024) S01E02-E03 - Part One + Part Two.mkv
  trailers/
    Trailer.mkv
  featurettes/
    Featurette.mkv
```

The year is included when known by default. Supported Jellyfin provider tags (`tmdb`, `tvdb`, and `imdb`) may be attached to the series folder when explicitly available. TVMaze remains JMO's canonical/audit identity and is not emitted as an invented Jellyfin provider tag.

Episodes use the canonical show title, assigned provider season/episode coordinates, provider episode title, and the normalized source extension. Season zero stays `Season 00`. A multi-episode source must represent one contiguous ascending range within one season; cross-season or gapped assignments fail closed instead of producing a misleading filename.

Extra decisions map to explicit Jellyfin-compatible folders. Known categories such as trailers, featurettes, interviews, behind-the-scenes material, deleted scenes, and clips receive stable folders; generic or currently unmapped extra kinds fall back to `extras` with an explanatory reason.

Every path component is Unicode-normalized and sanitized against Windows-invalid characters, control characters, trailing dots/spaces, and reserved device names while remaining valid on POSIX. Invalid characters are encoded rather than merely dropped so unrelated logical names do not silently collapse to the same sanitized component. Literal escape characters are themselves escaped, making this mapping deterministic. Configurable component/path limits use deterministic hash-suffixed shortening. If the configured limits cannot produce a safe path, destination construction returns unresolved rather than guessing.

Each ready destination carries a Unicode-normalized, case-folded collision key. This means collisions are detected consistently during planning on Windows and POSIX, including case-only differences. Collision discovery reports every source/path set that converges on a logical destination; it does not choose winners or mutate files. Duplicate resolution remains a later stage.

The destination layer performs no filesystem access, creates no directories, and writes no media.

## Safety boundary

The current implementation is Shows-only. A caller must authorize the exact Shows directory rather than a parent media-library root. Symlinks and junctions are not followed outside that boundary.

Inventory is video-led for `.mkv`, `.mp4`, and `.avi`. Supported subtitle companions are discovered separately, joined to their video operation groups, and included in preflight. Artwork, metadata, Movies, and unrelated directories are not primary organizer inputs at this stage.

Planning and mutation remain separate. Generating a plan never implies approval, and approval must never be inferred from a successful scan or test run.

## Data-driven exceptions

Show-specific aliases and numbering behavior belong in versioned override data or focused strategy code. Generic parsing code must not accumulate hidden `if show == ...` branches.

Checked-in override examples and regression fixtures are synthetic. Deployment-specific aliases, provider IDs, years, filenames, and mappings belong in local untracked data.

## Public repository privacy

Source, tests, docs, examples, issues, and committed fixtures must not contain real environment details such as personal absolute paths, usernames, machine/host names, network addresses, share names, directory inventories, account identifiers, private filenames, production logs, or copied library metadata.

A problem found against a private library should be reduced to the smallest synthetic reproduction before it is committed.

Generated inventory exports, caches, manifests, plans, reports, copied media roots, and common video files are excluded by `.gitignore`.

## Testing

The committed test suite is deterministic and offline. Tests use synthetic names, temporary directories, zero-byte media stand-ins, and minimal provider-shaped fixtures.

The normal development gate is:

```bash
python -m pytest
python -m ruff check jellyfin_show_organizer tests
python -m ruff format --check jellyfin_show_organizer tests
jmo plan --help
```

The `organizer` console command remains a compatibility alias, and direct package execution remains supported.

## Upstream foundation

JMO began from the MIT-licensed `jkwill87/mnamer` project by Jessy Williams and has since diverged into a Jellyfin-focused architecture. The retained attribution and independence statement are documented in `ACKNOWLEDGMENTS.md`.

## License

MIT. The original upstream copyright and permission notice is retained in `LICENSE.txt`.
