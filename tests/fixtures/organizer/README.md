# Synthetic adversarial organizer fixtures

`adversarial_filenames.json` is a fabricated filename grammar corpus for offline
organizer regression tests. It is source-controlled test input, not a library
inventory or audit export.

Every path is relative and rooted beneath the literal `synthetic/` directory.
Tests may materialize these files only beneath pytest's `tmp_path`, using empty
files. The corpus intentionally contains no contributor paths, account details,
host information, network data, real media inventory, provider dumps, file
hashes, or copied production logs.

The expected fields describe parser/planner behavior that later organizer work
can target. They are test contracts for invented examples only.
