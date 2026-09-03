from pathlib import Path

path = Path("jellyfin_show_organizer/show_structural_evidence.py")
text = path.read_text(encoding="utf-8")
old = '''def catalog_coordinate_title_rescue(\n    provider: MetadataProvider,\n    parses: tuple[ParseResult, ...],\n    ranked: tuple[CandidateEvidence, ...],\n) -> StructuralCatalogDecision | None:\n    """Confirm a borderline aired show only by exact title at the same coordinate."""\n\n    observations = _title_observations(parses)\n'''
new = '''def catalog_coordinate_title_rescue(\n    provider: MetadataProvider,\n    parses: tuple[ParseResult, ...],\n    ranked: tuple[CandidateEvidence, ...],\n) -> StructuralCatalogDecision | None:\n    """Confirm one borderline aired source by exact title at the same coordinate."""\n\n    if len(parses) != 1:\n        return None\n    parse = parses[0]\n    if (\n        parse.season is None\n        or len(parse.episodes) != 1\n        or parse.title_hint is None\n        or not parse.title_hint.strip()\n        or parse.segment_hint is not None\n        or parse.absolute_episode is not None\n        or parse.special_episode is not None\n        or parse.episode_date is not None\n    ):\n        return None\n\n    observations = _title_observations(parses)\n'''
if text.count(old) != 1:
    raise SystemExit("catalog coordinate-title rescue integration point changed")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
