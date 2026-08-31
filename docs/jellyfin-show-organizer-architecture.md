# Jellyfin Media Organizer (JMO) architecture

## Purpose

`jellyfin_show_organizer` is the core Python package for Jellyfin Media Organizer (JMO). It builds a deterministic, auditable plan for TV-show media organization before any filesystem mutation is allowed.

The current project boundary is deliberately narrow:

1. authorize one Shows root;
2. inventory eligible video files read-only;
3. parse filename hints deterministically;
4. resolve each source show to one canonical provider identity;
5. resolve catalog/provider evidence through explicit data and cache layers;
6. build versioned plan records;
7. reconcile every expected source into an explained terminal status;
8. audit the completed plan before any future apply implementation.

There is currently no `apply` command.

## Package layout

- `cli.py` — command-line surface. Only the plan scaffold is exposed today.
- `inventory.py` — authorized-root checks and deterministic read-only video inventory.
- `filename_parser.py` — pure filename/path hint parsing without filesystem or provider access.
- `show_resolver.py` — conservative show-level canonical TVMaze resolution with explicit ambiguity states.
- `models.py` — typed cross-stage contracts.
- `overrides.py` — data-driven aliases, numbering modes, years, provider IDs, and title preferences.
- `reconciliation.py` — one explained terminal inventory status per expected path.
- `schema.py` — versioned manifest validation, serialization, and stable plan hashing.
- `tvmaze_cache.py` — persistent provider-cache primitives with explicit cache/network/error state.
- `data/` — versioned JSON/TOML contracts and synthetic default override examples.

## Determinism

Equivalent input evidence should produce equivalent typed results and stable plan hashes. Run-local timestamps, machine paths, and other environment-specific metadata do not belong in the immutable plan contract.

Filename parsing is pure and offline. Provider-backed matching consumes cached/provider-shaped evidence rather than hiding network access inside parsing logic.

## Safety boundary

The current implementation is Shows-only. A caller must authorize the exact Shows directory rather than a parent media-library root. Symlinks and junctions are not followed outside that boundary.

Inventory is video-only for `.mkv`, `.mp4`, and `.avi`. Subtitles, artwork, metadata, Movies, and unrelated directories are not primary organizer inputs at this stage. Sidecar discovery is tracked separately and must remain non-destructive.

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
