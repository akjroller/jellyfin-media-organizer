from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache


class CountingGetter:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.response


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Fabricated Segment Series",
        tvmaze_id=4242,
        title="Fabricated Segment Series",
        year=2024,
        numbering_mode=NumberingMode.SEGMENT_TITLE,
    )


def _source(title: str) -> SourceEpisodeInput:
    return SourceEpisodeInput(
        "fabricated-segment-b.mkv",
        ParseResult(
            season=1,
            episodes=(20,),
            segment_hint="b",
            title_hint=title,
        ),
    )


def _assign(tmp_path: Path, title: str, catalog: object):
    getter = CountingGetter(catalog)
    result = assign_episode_group(
        _show(),
        (_source(title),),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )
    return result, getter


def test_segment_title_recovers_provider_leading_the(tmp_path: Path) -> None:
    result, getter = _assign(
        tmp_path,
        "Bright Morning",
        [{"id": 3001, "season": 1, "number": 8, "name": "The Bright Morning"}],
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3001
    assert "segment-title-leading-article-match:bright morning" in (
        assignment.evidence.reasons
    )
    assert len(getter.calls) == 1


def test_segment_title_recovers_source_leading_the(tmp_path: Path) -> None:
    result, _getter = _assign(
        tmp_path,
        "The Bright Morning",
        [{"id": 3002, "season": 1, "number": 8, "name": "Bright Morning"}],
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3002
    assert "segment-title-leading-article-match:the bright morning" in (
        assignment.evidence.reasons
    )


def test_segment_title_article_equivalence_fails_closed_when_ambiguous(
    tmp_path: Path,
) -> None:
    result, _getter = _assign(
        tmp_path,
        "Bright Morning",
        [
            {"id": 3003, "season": 1, "number": 8, "name": "The Bright Morning"},
            {"id": 3004, "season": 1, "number": 9, "name": "The Bright Morning"},
        ],
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.SUSPICIOUS
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert not assignment.episodes
    assert "ambiguous-segment-title-leading-article-match:bright morning" in (
        assignment.evidence.reasons
    )


def test_segment_title_article_equivalence_does_not_recover_unrelated_title(
    tmp_path: Path,
) -> None:
    result, _getter = _assign(
        tmp_path,
        "Faraway Midnight Journey",
        [{"id": 3005, "season": 1, "number": 8, "name": "The Bright Morning"}],
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert not assignment.episodes
    assert "missing-segment-title-match:faraway midnight journey" in (
        assignment.evidence.reasons
    )
