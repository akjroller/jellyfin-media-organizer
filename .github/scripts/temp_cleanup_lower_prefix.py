from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"integration point changed for {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jellyfin_show_organizer/release_prefix_fallback.py",
    '''_RELEASE_PREFIX = re.compile(\n    r"^(?P<prefix>[A-Z0-9][A-Z0-9._]{1,15})-(?P<title>[^-].+)$"\n)\n''',
    '''_RELEASE_PREFIX = re.compile(\n    r"^(?P<prefix>[A-Z0-9][A-Z0-9._]{1,15})-(?P<title>[^-].+)$"\n)\n_LOWER_RELEASE_PREFIX = re.compile(\n    r"^(?P<prefix>[a-z][a-z0-9._]{4,14})-(?P<title>[^-].+)$"\n)\n''',
)
replace_once(
    "jellyfin_show_organizer/release_prefix_fallback.py",
    '''    match = _RELEASE_PREFIX.fullmatch(value.strip())\n    if match is None:\n        return None\n\n    prefix = match.group("prefix")\n    title = match.group("title").strip(" ._-")\n''',
    '''    match = _RELEASE_PREFIX.fullmatch(value.strip())\n    lower_prefix = False\n    if match is None:\n        match = _LOWER_RELEASE_PREFIX.fullmatch(value.strip())\n        lower_prefix = match is not None\n    if match is None:\n        return None\n\n    prefix = match.group("prefix")\n    title = match.group("title").strip(" ._-")\n''',
)
replace_once(
    "jellyfin_show_organizer/release_prefix_fallback.py",
    '''    if _RELEASE_PREFIX.fullmatch(title) is not None:\n        return None\n\n    title_words = re.findall(r"[^\\W\\d_]+", title, flags=re.UNICODE)\n    if len(title_words) < 2:\n        return None\n''',
    '''    if (\n        _RELEASE_PREFIX.fullmatch(title) is not None\n        or _LOWER_RELEASE_PREFIX.fullmatch(title) is not None\n    ):\n        return None\n\n    title_words = re.findall(r"[^\\W\\d_]+", title, flags=re.UNICODE)\n    minimum_words = 4 if lower_prefix else 2\n    if len(title_words) < minimum_words:\n        return None\n''',
)

path = Path("tests/local/test_release_prefix_fallback.py")
text = path.read_text(encoding="utf-8")
marker = '''def test_catalog_confirmed_release_prefix_resolves() -> None:\n'''
addition = '''def test_long_lowercase_release_prefix_can_be_catalog_confirmed() -> None:\n    assert release_prefix_title("packtag-Example Long Program Title") == (\n        "packtag",\n        "Example Long Program Title",\n    )\n\n    provider = ReleasePrefixProvider(\n        {\n            "packtag-Example Long Program Title": _snapshot(\n                "packtag-Example Long Program Title"\n            ),\n            "Example Long Program Title": _snapshot(\n                "Example Long Program Title",\n                _show(ALPHA, "Example Long Program Title"),\n            ),\n        },\n        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1, "Pilot"))},\n    )\n\n    result = _resolve(\n        "packtag-Example Long Program Title S01E01.mkv",\n        provider,\n    )\n\n    assert result.status is ResolutionStatus.MATCHED\n    assert result.show is not None\n    assert result.show.provider_identity == ALPHA\n    assert "release-prefix-fallback:catalog-confirmed" in result.evidence.reasons\n\n\ndef test_lowercase_prefix_rule_rejects_short_or_two_word_natural_titles() -> None:\n    assert release_prefix_title("tag-Example Long Program Title") is None\n    assert release_prefix_title("legend-Example Series") is None\n    assert release_prefix_title("spider-man adventures") is None\n\n\ndef test_catalog_confirmed_release_prefix_resolves() -> None:\n'''
if text.count(marker) != 1:
    raise SystemExit("release-prefix test insertion point changed")
path.write_text(text.replace(marker, addition, 1), encoding="utf-8")
