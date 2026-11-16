"""Every malformed plan manifest must be rejected before apply can use it.

Each case corrupts exactly one field of an otherwise valid manifest. The v2
cases start from the frozen compatibility fixture; the v3 cases start from a
manifest produced by the real planner over a fabricated library.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyContractError,
    derive_apply_group_ids,
)
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    execute_plan,
)
from jellyfin_show_organizer.schema import ManifestValidationError, validate_manifest

pytestmark = pytest.mark.local

Manifest = dict[str, Any]
Mutation = Callable[[Manifest], None]

V2_FIXTURE = Path(__file__).parents[1] / "fixtures" / "plan-schema-v2-duplicate.json"
SERIES = "Example Aired Series"
SEARCH = [{"show": {"id": 4242, "name": SERIES, "premiered": "2024-01-01"}}]
EPISODES = [
    {
        "id": 1001,
        "season": 1,
        "number": 1,
        "name": "Pilot",
        "airdate": "2024-01-01",
        "type": "regular",
    }
]


def _getter(url: str, params: Mapping[str, str] | None = None) -> object:
    del params
    return SEARCH if "search/shows" in url else EPISODES


@pytest.fixture(scope="module")
def v3_manifest(tmp_path_factory: pytest.TempPathFactory) -> Manifest:
    root = tmp_path_factory.mktemp("v3")
    series = root / "Shows" / SERIES
    series.mkdir(parents=True)
    (root / "Organized").mkdir()
    (series / f"{SERIES} S01E01.mkv").write_bytes(b"fabricated-video")
    (series / f"{SERIES} S01E01.en.srt").write_bytes(b"fabricated-subtitle")
    execute_plan(
        PlanningConfig(
            shows_root=root / "Shows",
            destination_root=root / "Organized",
            output_dir=root / "audit",
            cache_dir=root / "cache",
        ),
        _getter,
    )
    manifest: Manifest = json.loads(
        (root / "audit" / "plan.json").read_text(encoding="utf-8")
    )
    validate_manifest(manifest)
    return manifest


@pytest.fixture(scope="module")
def v2_manifest() -> Manifest:
    manifest: Manifest = json.loads(V2_FIXTURE.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def _record(manifest: Manifest) -> dict[str, Any]:
    return manifest["records"][0]


def _set(path: str, value: object) -> Mutation:
    """Set one dotted path (``records.0.source.extension``) to ``value``."""

    def mutate(manifest: Manifest) -> None:
        target: Any = manifest
        *parents, last = path.split(".")
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        if isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value

    return mutate


def _delete(path: str) -> Mutation:
    def mutate(manifest: Manifest) -> None:
        target: Any = manifest
        *parents, last = path.split(".")
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        del target[last]

    return mutate


def _add_field(path: str) -> Mutation:
    def mutate(manifest: Manifest) -> None:
        target: Any = manifest
        for part in path.split("."):
            target = target[int(part)] if isinstance(target, list) else target[part]
        target["unexpected"] = True

    return mutate


def _held_with_destination(manifest: Manifest) -> None:
    record = _record(manifest)
    record["status"] = "held"
    record["duplicate"] = None
    record["destination"] = "Somewhere/x.mkv"


V2_CASES: dict[str, Mutation] = {
    "unsupported schema version": _set("schema_version", 99),
    "manifest is not an object": lambda m: m.clear(),
    "record has extra field": _add_field("records.0"),
    "record status unsupported": _set("records.0.status", "moved"),
    "status not a string": _set("records.0.status", 3),
    "destination not a string": _set("records.0.destination", 7),
    "group id not a string": _set("records.0.operation_group_id", 7),
    "reason not a string": _set("records.0.reason", 7),
    "duplicate has extra field": _add_field("records.0.duplicate"),
    "duplicate key not a string": _set("records.0.duplicate.destination_key", 1),
    "duplicate needs two candidates": _set(
        "records.0.duplicate.candidates", ["only-one.mkv"]
    ),
    "duplicate candidates not strings": _set("records.0.duplicate.candidates", [1, 2]),
    "duplicate winner not a string": _set("records.0.duplicate.winner", 5),
    "duplicate losers not strings": _set("records.0.duplicate.losers", [5]),
    "duplicate confidence above one": _set("records.0.duplicate.confidence", 1.5),
    "duplicate confidence not a number": _set("records.0.duplicate.confidence", "1"),
    "duplicate evidence not strings": _set("records.0.duplicate.evidence", [1]),
    "duplicate record without duplicate": _set("records.0.duplicate", None),
    "provider episodes not an array": _set("records.0.provider_episodes", {}),
    "provider episode has extra field": _set(
        "records.0.provider_episodes",
        [
            {
                "tvmaze_episode_id": 1,
                "season": 1,
                "number": 1,
                "title": "Pilot",
                "airdate": None,
                "unexpected": True,
            }
        ],
    ),
    "provider episode id is a bool": _set(
        "records.0.provider_episodes",
        [
            {
                "tvmaze_episode_id": True,
                "season": 1,
                "number": 1,
                "title": "Pilot",
                "airdate": None,
            }
        ],
    ),
    "matched record without parse": _set("records.0.status", "matched"),
    "extra record without extra": _set("records.0.status", "extra"),
    "held record with destination": _held_with_destination,
    "held record that is a duplicate": _set("records.0.status", "held"),
    "source fingerprint size negative": _set("records.0.source.fingerprint.size", -1),
}


V3_CASES: dict[str, Mutation] = {
    "record has extra field": _add_field("records.0"),
    "record missing destination": _delete("records.0.destination"),
    "unsupported status": _set("records.0.status", "moved"),
    "matched record without destination": _set("records.0.destination", None),
    "matched record without show": _set("records.0.show", None),
    "matched record without parse": _set("records.0.parse", None),
    "matched record without evidence": _set("records.0.evidence", None),
    "source extension missing dot": _set("records.0.source.extension", "mkv"),
    "source path empty": _set("records.0.source.relative_path", ""),
    "fingerprint size negative": _set("records.0.source.fingerprint.size", -1),
    "fingerprint sha256 malformed": _set(
        "records.0.source.fingerprint.sha256", "not-a-digest"
    ),
    "fingerprint has extra field": _add_field("records.0.source.fingerprint"),
    "evidence confidence above one": _set("records.0.evidence.confidence", 2),
    "provider episode title missing": _delete("records.0.provider_episodes.0.title"),
    "provider episode season not an integer": _set(
        "records.0.provider_episodes.0.season", "1"
    ),
    "companion has extra field": _add_field("companions.0"),
    "companion status unsupported": _set("companions.0.status", "moved"),
    "companion destination not a string": _set("companions.0.destination", 9),
    "provenance has extra field": _add_field("provenance"),
    "provenance snapshot malformed": _set("provenance.cache_snapshots", [{}]),
    "overrides version wrong type": _set("overrides_version", "1"),
    "records not an array": _set("records", {}),
    "companions not an array": _set("companions", {}),
    "show has extra field": _add_field("records.0.show"),
    "show id not an integer": _set("records.0.show.tvmaze_id", "4242"),
    "show numbering mode unknown": _set("records.0.show.numbering_mode", "lunar"),
    "parse has extra field": _add_field("records.0.parse"),
    "parse episodes not an array": _set("records.0.parse.episodes", 1),
    "parse season negative": _set("records.0.parse.season", -1),
    "evidence has extra field": _add_field("records.0.evidence"),
    "evidence reasons not strings": _set("records.0.evidence.reasons", [1]),
    "evidence candidate has extra field": _add_field("records.0.evidence.candidates.0"),
    "companion extension missing dot": _set("companions.0.extension", "srt"),
    "provenance config id malformed": _set("provenance.config_snapshot_id", "x"),
    "show id zero": _set("records.0.show.tvmaze_id", 0),
    "show year too early": _set("records.0.show.year", 1700),
    "parse special kind unknown": _set("records.0.parse.special_kind", "movie"),
    "parse episode date malformed": _set("records.0.parse.episode_date", "2024/1/1"),
    "parse episode negative": _set("records.0.parse.episodes", [-1]),
    "evidence method empty": _set("records.0.evidence.method", ""),
    "evidence candidate score above one": _set(
        "records.0.evidence.candidates.0.score", 1.5
    ),
    "evidence candidate id not an integer": _set(
        "records.0.evidence.candidates.0.tvmaze_id", "4242"
    ),
    "provenance snapshot id malformed": _set(
        "provenance.cache_snapshots.0.snapshot_id", "not-a-digest"
    ),
}


def _rejected(manifest: Manifest, mutation: Mutation) -> None:
    corrupted = copy.deepcopy(manifest)
    mutation(corrupted)
    with pytest.raises((ManifestValidationError, ApplyContractError)):
        validate_manifest(corrupted)


@pytest.mark.parametrize("case", sorted(V2_CASES))
def test_corrupted_v2_manifest_is_rejected(v2_manifest: Manifest, case: str) -> None:
    _rejected(v2_manifest, V2_CASES[case])


@pytest.mark.parametrize("case", sorted(V3_CASES))
def test_corrupted_v3_manifest_is_rejected(v3_manifest: Manifest, case: str) -> None:
    _rejected(v3_manifest, V3_CASES[case])


def test_apply_group_derivation_rejects_corrupted_manifests(
    v3_manifest: Manifest,
) -> None:
    assert derive_apply_group_ids(v3_manifest) == (
        _record(v3_manifest)["operation_group_id"],
    )
    corrupted = copy.deepcopy(v3_manifest)
    _record(corrupted)["destination"] = None
    with pytest.raises((ManifestValidationError, ApplyContractError)):
        derive_apply_group_ids(corrupted)
