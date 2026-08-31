from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from jellyfin_show_organizer.models import CompanionStatus, TerminalStatus
from jellyfin_show_organizer.planner import PlanningConfig, execute_plan
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
    JsonGetter,
)

pytestmark = pytest.mark.local


EPISODES = {
    101: [
        {
            "id": 1001,
            "season": 1,
            "number": 1,
            "name": "Pilot",
            "airdate": "2024-01-01",
            "type": "regular",
        },
        {
            "id": 1002,
            "season": 1,
            "number": 2,
            "name": "First Current",
            "airdate": "2024-01-08",
            "type": "regular",
        },
        {
            "id": 1003,
            "season": 1,
            "number": 3,
            "name": "Second Current",
            "airdate": "2024-01-15",
            "type": "regular",
        },
    ],
    202: [
        {
            "id": 2001,
            "season": 0,
            "number": 1,
            "name": "OVA - Bonus Flight",
            "airdate": "2024-02-01",
            "type": "significant_special",
        }
    ],
    303: [
        {
            "id": 3005,
            "season": 1,
            "number": 5,
            "name": "Broadcast",
            "airdate": "2024-03-14",
            "type": "regular",
        }
    ],
}

SEARCHES = {
    "Ambiguous City": [
        {
            "score": 1.0,
            "show": {
                "id": 401,
                "name": "Ambiguous City",
                "premiered": "2005-01-01",
            },
        },
        {
            "score": 1.0,
            "show": {
                "id": 402,
                "name": "Ambiguous City",
                "premiered": "2024-01-01",
            },
        },
    ],
    "Unmatched Harbor": [
        {
            "score": 0.2,
            "show": {
                "id": 999,
                "name": "Different Program",
                "premiered": "2010-01-01",
            },
        }
    ],
}


class StressGetter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        if url == TVMAZE_SEARCH_URL:
            query = dict(params or {}).get("q", "")
            return SEARCHES[query]
        for tvmaze_id, episodes in EPISODES.items():
            if url == TVMAZE_EPISODES_URL.format(tvmaze_id=tvmaze_id):
                return episodes
        raise AssertionError(f"unexpected synthetic provider request: {url}")


def _touch(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _build_stress_library(tmp_path: Path) -> tuple[Path, Path, Path]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir()

    aired = shows / "Aired Harbor"
    _touch(aired / "Aired.Harbor.S01E01.Pilot.mkv", b"aired-episode-1")
    _touch(
        aired / "Aired.Harbor.S01E02-E03.Double.Current.mkv",
        b"aired-multi-episode",
    )
    _touch(aired / "Aired.Harbor.Trailer.01.1080p-SYNTH.mkv", b"aired-trailer")
    _touch(
        aired / "Aired.Harbor.S01E04.Cast.Interview.mkv",
        b"conflicting-extra-evidence",
    )
    _touch(aired / "Aired.Harbor.S01E01.Pilot.en.srt", b"subtitle")
    _touch(aired / "tvshow.nfo", b"synthetic metadata")
    _touch(aired / "release-notes.txt", b"unsupported adjacent file")

    _touch(
        shows / "Special Flight" / "Special Flight OVA 01 - Bonus Flight.mkv",
        b"special-episode",
    )
    _touch(
        shows / "Date Desk (2024)" / "Date Desk (2024) 2024-03-14 Broadcast.mkv",
        b"date-episode",
    )
    _touch(
        shows / "Ambiguous City" / "Ambiguous City S01E01 Pilot.mkv",
        b"ambiguous-show",
    )
    _touch(
        shows / "Unmatched Harbor" / "Unmatched Harbor S01E01 Pilot.mkv",
        b"unmatched-show",
    )

    overrides = tmp_path / "overrides.toml"
    overrides.write_text(
        """schema_version = 2

[[shows]]
key = "Aired Harbor"
tvmaze_id = 101
year = 2024
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Aired Harbor"

[[shows]]
key = "Special Flight"
tvmaze_id = 202
year = 2024
numbering_mode = "special"
title_preference = "override"
preferred_title = "Special Flight"

[[shows]]
key = "Date Desk (2024)"
tvmaze_id = 303
year = 2024
numbering_mode = "date"
title_preference = "override"
preferred_title = "Date Desk"
""",
        encoding="utf-8",
    )
    return shows, destination, overrides


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _config(
    tmp_path: Path,
    shows: Path,
    destination: Path,
    overrides: Path,
    *,
    output_name: str,
    offline: bool = False,
) -> PlanningConfig:
    return PlanningConfig(
        shows_root=shows,
        destination_root=destination,
        output_dir=tmp_path / output_name,
        cache_dir=tmp_path / "cache",
        overrides_path=overrides,
        offline=offline,
    )


def test_synthetic_release_candidate_accounts_for_every_video_and_replays_offline(
    tmp_path: Path,
) -> None:
    shows, destination, overrides = _build_stress_library(tmp_path)
    before = _snapshot(shows)
    getter = StressGetter()

    first = execute_plan(
        _config(
            tmp_path,
            shows,
            destination,
            overrides,
            output_name="audit-cold",
        ),
        getter,
    )

    counts = Counter(record.status for record in first.plan.records)
    assert len(first.plan.records) == 8
    assert counts[TerminalStatus.MATCHED] == 4
    assert counts[TerminalStatus.EXTRA] == 1
    assert counts[TerminalStatus.SUSPICIOUS] + counts[TerminalStatus.UNRESOLVED] == 3
    blocked = {TerminalStatus.SUSPICIOUS, TerminalStatus.UNRESOLVED}
    status_by_source = {
        record.source.relative_path: record.status for record in first.plan.records
    }
    assert (
        status_by_source["Aired Harbor/Aired.Harbor.S01E04.Cast.Interview.mkv"]
        in blocked
    )
    assert status_by_source["Ambiguous City/Ambiguous City S01E01 Pilot.mkv"] in blocked
    assert (
        status_by_source["Unmatched Harbor/Unmatched Harbor S01E01 Pilot.mkv"]
        in blocked
    )
    assert all(record.status in TerminalStatus for record in first.plan.records)
    assert not first.preflight.ready
    assert first.provider_failure
    assert len(getter.calls) == 5

    companion_counts = Counter(record.status for record in first.plan.companions)
    assert companion_counts == Counter(
        {
            CompanionStatus.ASSOCIATED: 1,
            CompanionStatus.IGNORED: 1,
            CompanionStatus.UNRESOLVED: 1,
        }
    )

    assert _snapshot(shows) == before
    assert list(destination.iterdir()) == []
    assert first.bundle.unresolved_csv.startswith(b"\xef\xbb\xbf")
    assert first.bundle.sidecars_csv.startswith(b"\xef\xbb\xbf")

    def reject_network(
        _url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        raise AssertionError("warmed offline stress replay attempted provider access")

    second = execute_plan(
        _config(
            tmp_path,
            shows,
            destination,
            overrides,
            output_name="audit-warm",
            offline=True,
        ),
        cast(JsonGetter, reject_network),
    )

    assert second.preflight.plan_hash == first.preflight.plan_hash
    assert second.bundle.plan_json == first.bundle.plan_json
    assert second.bundle.mapping_csv == first.bundle.mapping_csv
    assert second.bundle.unresolved_csv == first.bundle.unresolved_csv
    assert second.bundle.sidecars_csv == first.bundle.sidecars_csv
    assert _snapshot(shows) == before
    assert list(destination.iterdir()) == []
