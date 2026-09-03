from pathlib import Path

path = Path("jellyfin_show_organizer/filename_parser.py")
text = path.read_text(encoding="utf-8")
old = '''    if _SEASON_NOISE.search(stem[: match.start()]) is not None:\n        return False\n'''
new = '''    if re.search(\n        r"(?i)(?:^|[ ._-])(?:s|season)[ ._-]*\\d{1,2}(?=$|[ ._-])",\n        stem[: match.start()],\n    ) is not None:\n        return False\n'''
if text.count(old) != 1:
    raise SystemExit("season-only bare absolute guard integration point changed")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
