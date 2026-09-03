from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected one match in {path}, found {text.count(old)}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# First-class held video status.
replace_once(
    "jellyfin_show_organizer/models.py",
    '    DUPLICATE = "duplicate"\n    SUSPICIOUS = "suspicious"\n',
    '    DUPLICATE = "duplicate"\n    HELD = "held"\n    SUSPICIOUS = "suspicious"\n',
)
replace_once(
    "jellyfin_show_organizer/models.py",
    '        if self.status is TerminalStatus.DUPLICATE and self.duplicate is None:\n            raise ValueError("duplicate plan records require a duplicate decision")\n',
    '        if self.status is TerminalStatus.DUPLICATE and self.duplicate is None:\n            raise ValueError("duplicate plan records require a duplicate decision")\n        if self.status is TerminalStatus.HELD:\n            if self.destination is not None or self.extra is not None or self.duplicate is not None:\n                raise ValueError("held plan records must be non-moving and non-duplicate")\n            if self.provider_episodes:\n                raise ValueError("held plan records cannot carry provider episodes")\n            if self.evidence is None or self.reason is None:\n                raise ValueError("held plan records require audit evidence and a reason")\n',
)

# Override schema v4 and exact source holds.
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "SUPPORTED_OVERRIDE_SCHEMA_VERSIONS = frozenset({1, 2, 3})",
    "SUPPORTED_OVERRIDE_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4})",
)
insert = '''\n\n@dataclass(frozen=True, slots=True)\nclass SourceHoldOverride:\n    \"\"\"One exact-source local decision to leave a video untouched.\"\"\"\n\n    source: str\n    reasons: tuple[str, ...] = (\"explicit local leave-in-place decision\",)\n\n    def __post_init__(self) -> None:\n        object.__setattr__(\n            self,\n            \"source\",\n            _normalize_source_reference(self.source, label=\"source hold source\"),\n        )\n        if not self.reasons or any(\n            not reason or reason != reason.strip() for reason in self.reasons\n        ):\n            raise ValueError(\n                \"source hold reasons must contain non-empty trimmed strings\"\n            )\n        normalized_reasons = [\n            unicodedata.normalize(\"NFKC\", reason).casefold() for reason in self.reasons\n        ]\n        if len(normalized_reasons) != len(set(normalized_reasons)):\n            raise ValueError(\"source hold reasons must be unique\")\n'''
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "\n\ndef _decision_family(parse: ParseResult) -> str:\n",
    insert + "\n\ndef _decision_family(parse: ParseResult) -> str:\n",
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "    duplicate_preferences: tuple[DuplicatePreferenceOverride, ...] = ()\n    episode_decisions: tuple[EpisodeDecisionOverride, ...] = ()\n",
    "    duplicate_preferences: tuple[DuplicatePreferenceOverride, ...] = ()\n    episode_decisions: tuple[EpisodeDecisionOverride, ...] = ()\n    source_holds: tuple[SourceHoldOverride, ...] = ()\n",
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    '        if self.schema_version < 3 and self.episode_decisions:\n            raise ValueError("episode decisions require override schema_version 3")\n',
    '        if self.schema_version < 3 and self.episode_decisions:\n            raise ValueError("episode decisions require override schema_version 3")\n        if self.schema_version < 4 and self.source_holds:\n            raise ValueError("source holds require override schema_version 4")\n',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "            decision_sources[normalized] = decision.source\n\n    def get(self, key: str) -> ShowOverride | None:\n",
    '''            decision_sources[normalized] = decision.source\n\n        hold_sources: dict[str, str] = {}\n        for hold in self.source_holds:\n            normalized = _source_reference_key(hold.source)\n            owner = hold_sources.get(normalized)\n            if owner is not None:\n                raise ValueError(\n                    \"source hold is configured more than once: \"\n                    f\"{hold.source!r} conflicts with {owner!r}\"\n                )\n            if normalized in duplicate_sources or normalized in decision_sources:\n                raise ValueError(\n                    \"source hold cannot overlap an episode decision or duplicate preference\"\n                )\n            hold_sources[normalized] = hold.source\n\n    def get(self, key: str) -> ShowOverride | None:\n''',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "    def canonical_bytes(self) -> bytes:\n",
    '''    def source_hold_for(self, source_relative_path: str) -> SourceHoldOverride | None:\n        normalized = _source_reference_key(source_relative_path)\n        return next(\n            (\n                hold\n                for hold in self.source_holds\n                if _source_reference_key(hold.source) == normalized\n            ),\n            None,\n        )\n\n    def canonical_bytes(self) -> bytes:\n''',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "        return json.dumps(\n            payload,\n",
    '''        if self.schema_version >= 4:\n            payload[\"source_holds\"] = [\n                {\n                    \"reasons\": sorted(\n                        hold.reasons,\n                        key=lambda reason: (\n                            unicodedata.normalize(\"NFKC\", reason).casefold(),\n                            reason,\n                        ),\n                    ),\n                    \"source\": hold.source,\n                }\n                for hold in sorted(\n                    self.source_holds,\n                    key=lambda item: (_source_reference_key(item.source), item.source),\n                )\n            ]\n        return json.dumps(\n            payload,\n''',
)
parse_hold = '''\n\ndef _parse_source_hold(raw: dict[str, Any]) -> SourceHoldOverride:\n    allowed = {\"source\", \"reasons\"}\n    unknown = set(raw) - allowed\n    if unknown:\n        raise ValueError(f\"unknown source hold fields: {sorted(unknown)}\")\n    source = raw.get(\"source\")\n    reasons = raw.get(\"reasons\", [\"explicit local leave-in-place decision\"])\n    if not isinstance(source, str):\n        raise ValueError(\"source hold source must be a string\")\n    if not isinstance(reasons, list) or not all(\n        isinstance(reason, str) for reason in reasons\n    ):\n        raise ValueError(\"source hold reasons must be a list of strings\")\n    return SourceHoldOverride(source=source, reasons=tuple(reasons))\n'''
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "\n\ndef load_overrides(path: Path | None = None) -> OverrideCatalog:\n",
    parse_hold + "\n\ndef load_overrides(path: Path | None = None) -> OverrideCatalog:\n",
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    '        "episode_decisions",\n    }\n',
    '        "episode_decisions",\n        "source_holds",\n    }\n',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    '    episode_decisions = raw.get("episode_decisions", [])\n',
    '    episode_decisions = raw.get("episode_decisions", [])\n    source_holds = raw.get("source_holds", [])\n',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    '    if schema_version < 3 and "episode_decisions" in raw:\n        raise ValueError("episode decisions require override schema_version 3")\n\n    return OverrideCatalog(\n',
    '''    if schema_version < 3 and \"episode_decisions\" in raw:\n        raise ValueError(\"episode decisions require override schema_version 3\")\n    if not isinstance(source_holds, list) or not all(\n        isinstance(hold, dict) for hold in source_holds\n    ):\n        raise ValueError(\"source_holds must be an array of tables\")\n    if schema_version < 4 and \"source_holds\" in raw:\n        raise ValueError(\"source holds require override schema_version 4\")\n\n    return OverrideCatalog(\n''',
)
replace_once(
    "jellyfin_show_organizer/overrides.py",
    "        episode_decisions=tuple(\n            _parse_episode_decision(decision) for decision in episode_decisions\n        ),\n    )\n",
    "        episode_decisions=tuple(\n            _parse_episode_decision(decision) for decision in episode_decisions\n        ),\n        source_holds=tuple(_parse_source_hold(hold) for hold in source_holds),\n    )\n",
)

# Planner: validate and materialize held records before show resolution.
replace_once(
    "jellyfin_show_organizer/planner.py",
    "def _validate_episode_decision_sources(\n",
    '''def _configured_source_hold_keys(overrides: OverrideCatalog) -> set[str]:\n    return {_path_key(hold.source)[0] for hold in overrides.source_holds}\n\n\ndef _validate_source_hold_sources(\n    sources: tuple[SourceFile, ...],\n    overrides: OverrideCatalog,\n) -> None:\n    configured = _configured_source_hold_keys(overrides)\n    available = {_path_key(source.relative_path)[0] for source in sources}\n    if configured - available:\n        raise PlanningConfigurationError(\"source hold references an unknown source\")\n\n\ndef _validate_episode_decision_sources(\n''',
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "    _validate_episode_decision_sources(sources, overrides)\n    sidecars = discover_sidecars(source_root, sources)\n",
    "    _validate_episode_decision_sources(sources, overrides)\n    _validate_source_hold_sources(sources, overrides)\n    sidecars = discover_sidecars(source_root, sources)\n",
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "    groups: dict[str, list[SourceFile]] = defaultdict(list)\n    for source in sources:\n        groups[_show_group_key(source, classifications[source.relative_path])].append(\n            source\n        )\n\n    destination_policy = DestinationPolicy(\n",
    '''    groups: dict[str, list[SourceFile]] = defaultdict(list)\n    held_records: list[PlanRecord] = []\n    for source in sources:\n        hold = overrides.source_hold_for(source.relative_path)\n        if hold is not None:\n            evidence = MatchEvidence(\n                method=\"source-hold-override\",\n                confidence=1.0,\n                reasons=hold.reasons,\n            )\n            held_records.append(\n                PlanRecord(\n                    source=source,\n                    status=TerminalStatus.HELD,\n                    parse=classifications[source.relative_path].parse,\n                    evidence=evidence,\n                    operation_group_id=_operation_group_id(source.relative_path),\n                    reason=_reason(evidence),\n                )\n            )\n            continue\n        groups[_show_group_key(source, classifications[source.relative_path])].append(\n            source\n        )\n\n    destination_policy = DestinationPolicy(\n''',
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "    records: list[PlanRecord] = []\n    for source_key in sorted(groups, key=_path_key):\n",
    "    records: list[PlanRecord] = list(held_records)\n    for source_key in sorted(groups, key=_path_key):\n",
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "    if record.status is TerminalStatus.DUPLICATE:\n        return PreflightStatus.NON_MOVING\n",
    "    if record.status in {TerminalStatus.DUPLICATE, TerminalStatus.HELD}:\n        return PreflightStatus.NON_MOVING\n",
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "        status = (\n            CompanionStatus.DUPLICATE\n            if video.status is TerminalStatus.DUPLICATE\n            else CompanionStatus.UNRESOLVED\n        )\n",
    '''        if video.status is TerminalStatus.DUPLICATE:\n            status = CompanionStatus.DUPLICATE\n        elif video.status is TerminalStatus.HELD:\n            status = CompanionStatus.IGNORED\n        else:\n            status = CompanionStatus.UNRESOLVED\n''',
)

# Plan schema v2: first-class held status.
replace_once(
    "jellyfin_show_organizer/schema.py",
    'PLAN_SCHEMA_VERSION = 1\nPLAN_SCHEMA_RESOURCE = "data/plan-schema-v1.json"',
    'PLAN_SCHEMA_VERSION = 2\nPLAN_SCHEMA_RESOURCE = "data/plan-schema-v2.json"',
)
replace_once(
    "jellyfin_show_organizer/schema.py",
    '    allowed_statuses = {"matched", "extra", "duplicate", "suspicious", "unresolved"}\n',
    '    allowed_statuses = {"matched", "extra", "duplicate", "held", "suspicious", "unresolved"}\n',
)
replace_once(
    "jellyfin_show_organizer/schema.py",
    '    if status == "duplicate" and record["duplicate"] is None:\n        raise ManifestValidationError(f"{field} duplicate records require duplicate")\n',
    '''    if status == \"duplicate\" and record[\"duplicate\"] is None:\n        raise ManifestValidationError(f\"{field} duplicate records require duplicate\")\n    if status == \"held\":\n        if record[\"destination\"] is not None:\n            raise ManifestValidationError(f\"{field} held records must not move\")\n        if record[\"extra\"] is not None or record[\"duplicate\"] is not None:\n            raise ManifestValidationError(f\"{field} held records cannot be extras or duplicates\")\n        if provider_episodes:\n            raise ManifestValidationError(f\"{field} held records cannot carry provider episodes\")\n        if record[\"evidence\"] is None or record[\"reason\"] is None:\n            raise ManifestValidationError(f\"{field} held records require audit evidence\")\n''',
)

v1 = json.loads(Path("jellyfin_show_organizer/data/plan-schema-v1.json").read_text(encoding="utf-8"))
v1["$id"] = "urn:jmo:jellyfin-show-organizer:plan:v2"
v1["title"] = "Jellyfin Show Organizer Plan v2"
v1["properties"]["schema_version"]["const"] = 2
statuses = v1["$defs"]["planRecord"]["properties"]["status"]["enum"]
if "held" not in statuses:
    statuses.insert(3, "held")
v1["$defs"]["planRecord"]["allOf"].append(
    {
        "if": {"properties": {"status": {"const": "held"}}},
        "then": {
            "properties": {
                "destination": {"type": "null"},
                "extra": {"type": "null"},
                "duplicate": {"type": "null"},
                "provider_episodes": {"maxItems": 0},
                "evidence": {"type": "object"},
                "reason": {"type": "string", "minLength": 1},
            }
        },
    }
)
Path("jellyfin_show_organizer/data/plan-schema-v2.json").write_text(
    json.dumps(v1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# Apply contract explicitly excludes held videos from mutation groups.
replace_once(
    "jellyfin_show_organizer/apply_contract.py",
    '    if status == "duplicate":\n        return None\n    if status not in {"matched", "extra"}:\n',
    '    if status in {"duplicate", "held"}:\n        return None\n    if status not in {"matched", "extra"}:\n',
)
