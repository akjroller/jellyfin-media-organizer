"""Provider records refuse bad values, and malformed provider data is contained."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from jellyfin_show_organizer import providers
from jellyfin_show_organizer.models import ProviderIdentity
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)

pytestmark = pytest.mark.local

TVMAZE = ProviderIdentity.tvmaze(1)
TMDB = ProviderIdentity("tmdb", "1")
SNAPSHOT = "a" * 64


INVALID: dict[str, Callable[[], object]] = {
    "show without title": lambda: ProviderShow(TVMAZE, " ", None),
    "show from the far past": lambda: ProviderShow(TVMAZE, "Show", 1700),
    "episode without identity": lambda: ProviderEpisode(title="Pilot"),
    "episode with conflicting identity": lambda: ProviderEpisode(
        TVMAZE, 1, 1, "Pilot", tvmaze_episode_id=2
    ),
    "episode negative season": lambda: ProviderEpisode(TVMAZE, -1, 1, "Pilot"),
    "episode negative number": lambda: ProviderEpisode(TVMAZE, 1, -1, "Pilot"),
    "episode without title": lambda: ProviderEpisode(TVMAZE, 1, 1, ""),
    "episode invalid airdate": lambda: ProviderEpisode(TVMAZE, 1, 1, "P", "2024-02-30"),
    "episode non-canonical airdate": lambda: ProviderEpisode(
        TVMAZE, 1, 1, "P", "2024-1-01"
    ),
    "episode blank type": lambda: ProviderEpisode(TVMAZE, 1, 1, "P", None, " "),
    "search provider not normalized": lambda: ProviderSearchSnapshot(
        "TVMaze", "search:x", SNAPSHOT, ()
    ),
    "search without request key": lambda: ProviderSearchSnapshot(
        "tvmaze", "", SNAPSHOT, ()
    ),
    "search with foreign show": lambda: ProviderSearchSnapshot(
        "tvmaze", "search:x", SNAPSHOT, (ProviderShow(TMDB, "Show", None),)
    ),
    "unresolved search with shows": lambda: ProviderSearchSnapshot(
        "tvmaze",
        "search:x",
        SNAPSHOT,
        (ProviderShow(TVMAZE, "Show", None),),
        unresolved_reason="timeout",
    ),
    "catalog provider not normalized": lambda: ProviderEpisodeCatalog(
        "TVMaze", "episodes:1", SNAPSHOT, TVMAZE, ()
    ),
    "catalog without snapshot": lambda: ProviderEpisodeCatalog(
        "tvmaze", "episodes:1", "", TVMAZE, ()
    ),
    "catalog with foreign show": lambda: ProviderEpisodeCatalog(
        "tvmaze", "episodes:1", SNAPSHOT, TMDB, ()
    ),
    "catalog with foreign episode": lambda: ProviderEpisodeCatalog(
        "tvmaze",
        "episodes:1",
        SNAPSHOT,
        TVMAZE,
        (ProviderEpisode(TMDB, 1, 1, "Pilot"),),
    ),
    "unresolved catalog with data": lambda: ProviderEpisodeCatalog(
        "tvmaze",
        "episodes:1",
        SNAPSHOT,
        TVMAZE,
        (),
        errors=("x",),
        unresolved_reason="timeout",
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_malformed_provider_records_are_refused(case: str) -> None:
    with pytest.raises(ValueError):
        INVALID[case]()


def test_tvmaze_search_skips_malformed_results() -> None:
    response = [
        "not an object",
        {"show": "not an object"},
        {"show": {"id": 0, "name": "Zero"}},
        {"show": {"id": 2, "name": " "}},
        {"show": {"id": 3, "name": "Kept", "premiered": "2020-05-01"}},
        {"show": {"id": 4, "name": "No Year", "premiered": "soon"}},
    ]

    shows = providers._tvmaze_show_candidates(response)

    assert [(show.identity.value, show.title, show.year) for show in shows] == [
        ("3", "Kept", 2020),
        ("4", "No Year", None),
    ]
    assert providers._tvmaze_show_candidates({"not": "a list"}) == ()


def test_tvmaze_catalog_reports_each_malformed_entry() -> None:
    response = [
        "not an object",
        {"id": True, "season": 1, "number": 1, "name": "Bool id"},
        {"id": 2, "season": -1, "number": 1, "name": "Bad season"},
        {"id": 3, "season": 1, "number": "1", "name": "Bad number"},
        {"id": 4, "season": 1, "number": 1, "name": " "},
        {"id": 5, "season": 1, "number": 2, "name": "Kept", "airdate": 2024},
        {"id": 6, "season": 1, "number": 3, "name": "Odd", "airdate": "2024-1-1"},
        {"id": 7, "season": 1, "number": 4, "name": "Typed", "type": " "},
    ]

    episodes, errors, diagnostics = providers._tvmaze_episode_catalog(response)

    assert [episode.title for episode in episodes] == ["Kept", "Odd", "Typed"]
    assert errors == (
        "invalid-catalog-entry:0",
        "invalid-catalog-episode-id:1",
        "invalid-catalog-season:2",
        "invalid-catalog-number:3",
        "invalid-catalog-title:4",
    )
    assert set(diagnostics) == {
        "invalid-catalog-airdate:5",
        "invalid-catalog-airdate:6",
        "invalid-catalog-type:7",
    }
    assert providers._tvmaze_episode_catalog({}) == (
        (),
        ("episode-catalog-is-not-a-list",),
        (),
    )


def test_tmdb_parsing_skips_and_reports_malformed_data() -> None:
    shows = providers._tmdb_show_candidates(
        {
            "results": [
                "x",
                {"id": 0, "name": "Zero"},
                {"id": 9, "name": " ", "original_name": " "},
                {"id": 8, "original_name": "Original", "first_air_date": "2019-01-01"},
            ]
        }
    )
    assert [(show.identity.key, show.title, show.year) for show in shows] == [
        ("tmdb:8", "Original", 2019)
    ]
    assert providers._tmdb_show_candidates({"results": "nope"}) == ()

    episodes, errors, diagnostics = providers._tmdb_episode_catalog(
        [
            "x",
            {"id": 0, "season_number": 1, "episode_number": 1, "name": "A"},
            {"id": 1, "season_number": -1, "episode_number": 1, "name": "B"},
            {"id": 2, "season_number": 1, "episode_number": None, "name": "C"},
            {"id": 3, "season_number": 1, "episode_number": 1, "name": ""},
            {
                "id": 4,
                "season_number": 1,
                "episode_number": 2,
                "name": "Kept",
                "air_date": "someday",
            },
        ]
    )
    assert [episode.title for episode in episodes] == ["Kept"]
    assert len(errors) == 5
    assert diagnostics == ("invalid-catalog-airdate:5",)
    assert providers._tmdb_episode_catalog("x")[1] == ("episode-catalog-is-not-a-list",)
