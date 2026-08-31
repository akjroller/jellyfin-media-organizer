# Adversarial organizer fixtures

`adversarial_filenames.json` is a fabricated filename corpus for the Jellyfin
show organizer. It preserves only parsing patterns that are useful for tests;
it is not derived from, and must never be replaced by, a private inventory or
audit export.

Every `relative_path` is rooted beneath the literal `synthetic/` directory.
Tests may materialize these paths only beneath pytest's `tmp_path`, using empty
files. The corpus intentionally contains no absolute paths, usernames, media
library roots, provider responses, file hashes, or source-machine metadata.

The `expected` values describe the safe semantic result that later parser and
planner work should target. They are synthetic regression expectations, not
authoritative mappings for a real library.
