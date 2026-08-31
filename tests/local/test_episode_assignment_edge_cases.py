from pathlib import Path

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache

CATALOG = [
    {"id": 1001, "season": 1, "number": 1, "name": "Part Alpha"},
    {"id": 1002, "season": 1, "number": 2, "name": "Part Beta"},
]


def _show(mode: NumberingMode = NumberingMode.AIRED) -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        tvmaze_id=4242,
        title="Example Series",
        year=2024,
        numbering_mode=mode,
    )


def _getter(url: str, params: object = None) -> object:
    del url, params
    return CATALOG


def test_segment_hint_does_not_mask_combined_aired_and_absolute_evidence(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "conflict.mkv",
                ParseResult(
                    season=1,
                    episodes=(1,),
                    absolute_episode=1,
                    segment_hint="a",
                    title_hint="Part Alpha",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        _getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert "mixed-numbering-evidence:conflict" in result.assignments[0].evidence.reasons


def test_assignment_order_is_independent_of_caller_iteration_order(
    tmp_path: Path,
) -> None:
    sources = (
        SourceEpisodeInput("zeta.mkv", ParseResult(season=1, episodes=(2,))),
        SourceEpisodeInput("Alpha.mkv", ParseResult(season=1, episodes=(1,))),
    )

    forward = assign_episode_group(
        _show(),
        sources,
        TvmazeCatalogCache(tmp_path / "forward"),
        _getter,
    )
    reverse = assign_episode_group(
        _show(),
        reversed(sources),
        TvmazeCatalogCache(tmp_path / "reverse"),
        _getter,
    )

    assert forward == reverse
    assert [assignment.source_key for assignment in forward.assignments] == [
        "Alpha.mkv",
        "zeta.mkv",
    ]


def test_segment_collapse_preserves_catalog_mapping_evidence(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "a.mkv",
                ParseResult(segment_hint="a", title_hint="Part Alpha"),
            ),
            SourceEpisodeInput(
                "b.mkv",
                ParseResult(segment_hint="b", title_hint="Part Alpha"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        _getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    for assignment in result.assignments:
        assert "numbering-mode:segment-title" in assignment.evidence.reasons
        assert "catalog-request:episodes:4242" in assignment.evidence.reasons
        assert "tvmaze-episode:1001" in assignment.evidence.reasons
        assert (
            "distinct-segments-collapse-to-same-catalog-episode"
            in assignment.evidence.reasons
        )
