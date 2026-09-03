from pathlib import Path

target = Path("tests/local/test_consolidated_cleanup.py")
text = target.read_text(encoding="utf-8")
old = 'title_hint="Sixth Stor" if ambiguous else "Sixth Stori"'
new = 'title_hint="Sixth Stor" if ambiguous else "Sixth Storyy"'
if text.count(old) != 1:
    raise SystemExit("near-title fixture integration point changed")
target.write_text(text.replace(old, new, 1), encoding="utf-8")
