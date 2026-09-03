from __future__ import annotations

from pathlib import Path

OLD_APPROVED_HASH = "53bac569b37ff5257abc09190d86e895955a8895052ca967a068b83737943769"
NEW_APPROVED_HASH = "0a3574b94ef9ba0b6bbfa115678ada2b9cf82291bdd23cdeae7df4015b36beff"

for path in sorted(Path("tests/local").glob("test_*.py")):
    text = path.read_text(encoding="utf-8")
    updated = text.replace("schema_version=1", "schema_version=2")
    updated = updated.replace("schema_version = 1", "schema_version = 2")
    updated = updated.replace('["schema_version"]["const"] == 1', '["schema_version"]["const"] == 2')
    updated = updated.replace(OLD_APPROVED_HASH, NEW_APPROVED_HASH)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
