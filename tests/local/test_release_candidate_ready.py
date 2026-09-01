from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from jellyfin_show_organizer.models import CompanionStatus, TerminalStatus
from jellyfin_show_organizer.planner import PlanningConfig, execute_plan
from jellyfin_show_organizer.tvmaze_cache import JsonGetter

pytestmark = pytest.mark.local
FIXED_MTIME_NS = 1_700_000_000_000_000_000
APPROVED_PLAN_SHA256 = (
    "53bac569b37ff5257abc09190d86e895955a8895052ca967a068b83737943769"
)


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _episode(
    episode_id: int,
    season: int,
    number: int,
    title: str,
    *,
    airdate: str | None = None,
    episode_type: str = "regular",
) -> dict[str, object]:
    return {
        "id": episode_id,
        "season": season,
        "number": number,
        "name": title,
        "airdate": airdate,
        "type": episode_type,
    }


CATALOGS = {
    501: [
        _episode(50100, 0, 1, "Preview", episode_type="significant_special"),
        _episode(50101, 1, 1, "Arrival"),
        _episode(50102, 1, 2, "Crossing One"),
        _episode(50103, 1, 3, "Crossing Two"),
    ],
    502: [
        _episode(50201, 1, 1, "Launch"),
        _episode(50202, 1, 2, "Orbit"),
    ],
    503: [
        _episode(50301, 1, 1, "First Mark"),
        _episode(50302, 1, 2, "Second Mark"),
    ],
    504: [
        _episode(50401, 1, 1, "Red Kite"),
        _episode(50402, 1, 2, "Blue Boat"),
    ],
    505: [_episode(50501, 0, 1, "OVA - Moonlight", episode_type="special")],
    506: [_episode(50601, 1, 5, "Broadcast", airdate="2024-03-14")],
    507: [_episode(50701, 1, 1, "Legacy Arrival")],
    508: [_episode(50801, 1, 1, "CON: Arrival?")],
    509: [_episode(50901, 1, 1, "Provider Arrival")],
}


class ReadyProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        match = re.search(r"/shows/(\d+)/episodes$", url)
        if match is not None:
            return CATALOGS[int(match.group(1))]
        if "search/shows" in url and dict(params or {}).get("q") == "provider matched":
            return [
                {
                    "show": {
                        "id": 509,
                        "name": "Provider Matched",
                        "premiered": "2022-01-01",
                    }
                }
            ]
        raise AssertionError(f"unexpected synthetic provider request: {url}")


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))


def _library(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, bytes]]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir()
    overrides = tmp_path / "overrides.toml"
    overrides.write_text(
        """schema_version = 2

[[shows]]
key = "aired-realm"
tvmaze_id = 501
aliases = ["Aired Realm"]
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Aired Realm"

[[shows]]
key = "absolute-voyage"
tvmaze_id = 502
aliases = ["Absolute Voyage"]
numbering_mode = "absolute"
title_preference = "override"
preferred_title = "Absolute Voyage"

[[shows]]
key = "parenthetical-quest"
tvmaze_id = 503
aliases = ["Parenthetical Quest"]
numbering_mode = "parenthesized-absolute"
title_preference = "override"
preferred_title = "Parenthetical Quest"

[[shows]]
key = "segment-town"
tvmaze_id = 504
aliases = ["Segment Town"]
numbering_mode = "segment-title"
title_preference = "override"
preferred_title = "Segment Town"

[[shows]]
key = "ova-harbor"
tvmaze_id = 505
aliases = ["OVA Harbor"]
numbering_mode = "special"
title_preference = "override"
preferred_title = "OVA Harbor"

[[shows]]
key = "daily-chronicle"
tvmaze_id = 506
aliases = ["Daily Chronicle"]
numbering_mode = "date"
title_preference = "override"
preferred_title = "Daily Chronicle"

[[shows]]
key = "canonical-alias"
tvmaze_id = 507
aliases = ["Legacy Alias"]
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Canonical Alias"

[[shows]]
key = "cafe-cosmos"
tvmaze_id = 508
aliases = ["CafÃ© Cosmos"]
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Café Cosmos"

[[shows]]
key = "extras-gallery"
tvmaze_id = 510
aliases = ["Extras Gallery"]
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Extras Gallery"
""",
        encoding="utf-8",
    )
    files = {
        "Aired Realm/Aired Realm S00E01.mkv": b"aired-zero",
        "Aired Realm/Aired Realm S01E01.mkv": b"aired-one",
        "Aired Realm/Aired Realm S01E02-E03.mkv": b"aired-multi",
        "Absolute Voyage/Absolute Voyage - 01 [1080p].mkv": b"absolute-one",
        "Absolute Voyage/Absolute Voyage Episode 2.mkv": b"absolute-two",
        "Parenthetical Quest/Parenthetical Quest (1) [1080p].mkv": b"parent-one",
        "Parenthetical Quest/Parenthetical Quest (2) [1080p].mkv": b"parent-two",
        "Segment Town/Segment Town S01E01A - Red Kite.mkv": b"segment-a",
        "Segment Town/Segment Town S01E01B - Blue Boat.mkv": b"segment-b",
        "OVA Harbor/OVA Harbor OVA01 - Moonlight.mkv": b"special",
        "Daily Chronicle/Daily Chronicle 2024-03-14 Broadcast.mkv": b"date",
        "Legacy Alias/Legacy Alias S01E01.mkv": b"alias",
        "CafÃ© Cosmos/CafÃ© Cosmos S01E01.mkv": b"unicode",
        "Extras Gallery/Trailers/Trailer.mkv": b"extra",
        "Provider Matched/Provider Matched S01E01.mkv": b"provider",
    }
    for relative_path, payload in files.items():
        _write(shows / relative_path, payload)
    aired = shows / "Aired Realm" / "Aired Realm S01E01"
    _write(aired.with_suffix(".en.forced.srt"), b"subtitle")
    _write(aired.with_suffix(".idx"), b"subtitle-index")
    _write(aired.with_suffix(".sub"), b"subtitle-payload")
    _write(aired.with_suffix(".nfo"), b"ignored")
    return shows, destination, overrides, files


def test_complete_ready_candidate_has_stable_approved_hash_and_zero_mutation(
    tmp_path: Path,
) -> None:
    shows, destination, overrides, files = _library(tmp_path)
    provider = ReadyProvider()
    first = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit-online",
            cache_dir=tmp_path / "cache",
            overrides_path=overrides,
        ),
        provider,
        clock=_clock,
    )

    assert first.preflight.ready
    assert first.preflight.plan_hash == APPROVED_PLAN_SHA256
    assert first.plan.schema_version == 1
    assert first.plan.provenance is not None
    assert first.plan.provenance.tool_version == "0.1.0"
    assert len(first.plan.provenance.cache_snapshots) == 10
    assert all(
        snapshot.state == "ok" for snapshot in first.plan.provenance.cache_snapshots
    )
    assert Counter(record.status for record in first.plan.records) == {
        TerminalStatus.MATCHED: 14,
        TerminalStatus.EXTRA: 1,
    }
    assert len(first.plan.records) == len(files) == 15
    assert len(provider.calls) == 10
    assert Counter(record.status for record in first.plan.companions) == {
        CompanionStatus.ASSOCIATED: 3,
        CompanionStatus.IGNORED: 1,
    }
    assert any(
        "Café Cosmos" in (record.destination or "")
        and "~003A" in (record.destination or "")
        and "~003F" in (record.destination or "")
        for record in first.plan.records
    )
    assert list(destination.iterdir()) == []
    for relative_path, payload in files.items():
        assert (shows / relative_path).read_bytes() == payload

    def reject_network(
        _url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        raise AssertionError("offline approved-plan replay attempted provider access")

    second = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit-offline",
            cache_dir=tmp_path / "cache",
            overrides_path=overrides,
            offline=True,
        ),
        cast(JsonGetter, reject_network),
        clock=_clock,
    )

    assert second.preflight.ready
    assert second.preflight.plan_hash == first.preflight.plan_hash
    assert second.plan.provenance == first.plan.provenance
    assert second.bundle.plan_json == first.bundle.plan_json
    assert second.bundle.summary_txt == first.bundle.summary_txt
    assert list(destination.iterdir()) == []
