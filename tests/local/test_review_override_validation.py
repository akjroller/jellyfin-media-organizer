"""Reviewed override decisions refuse every malformed or conflicting entry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from jellyfin_show_organizer.destination import (
    JellyfinProvider,
    JellyfinProviderIdentifier,
)
from jellyfin_show_organizer.models import ProviderIdentity
from jellyfin_show_organizer.overrides import ShowOverride, SourceHoldOverride
from jellyfin_show_organizer.review_overrides import (
    ExplicitExtraOverride,
    ReviewedEpisodeOverride,
    ReviewOverrideCatalog,
    ShowJellyfinIdentifiers,
)

pytestmark = pytest.mark.local

BINDING = "a" * 64
SHOW = ProviderIdentity.tvmaze(1)


def _episode(**overrides: object) -> ReviewedEpisodeOverride:
    values: dict[str, object] = {
        "source": "Show/episode.mkv",
        "source_binding_sha256": BINDING,
        "show_provider_identity": SHOW,
        "episode_provider_identity": ProviderIdentity.tvmaze(11),
        "season": 1,
        "number": 1,
        "title": "Pilot",
        "airdate": None,
        "lookup_mode": "coordinate",
    }
    values.update(overrides)
    return ReviewedEpisodeOverride(**values)  # type: ignore[arg-type]


def _extra(**overrides: object) -> ExplicitExtraOverride:
    values: dict[str, object] = {
        "source": "Show/trailer.mkv",
        "source_binding_sha256": BINDING,
        "show_provider_identity": SHOW,
        "kind": "trailer",
    }
    values.update(overrides)
    return ExplicitExtraOverride(**values)  # type: ignore[arg-type]


def _catalog(**overrides: object) -> ReviewOverrideCatalog:
    values: dict[str, object] = {"schema_version": 5, "shows": ()}
    values.update(overrides)
    return ReviewOverrideCatalog(**values)  # type: ignore[arg-type]


INVALID: dict[str, Callable[[], object]] = {
    "binding not a string": lambda: _episode(source_binding_sha256=5),
    "binding wrong length": lambda: _episode(source_binding_sha256="ab"),
    "binding not hex": lambda: _episode(source_binding_sha256="z" * 64),
    "episode from another provider": lambda: _episode(
        episode_provider_identity=ProviderIdentity("tmdb", "11")
    ),
    "negative coordinate": lambda: _episode(season=-1),
    "blank title": lambda: _episode(title="  "),
    "unknown lookup mode": lambda: _episode(lookup_mode="vibes"),
    "blank airdate": lambda: _episode(airdate=" "),
    "untrimmed reason": lambda: _episode(reasons=(" reason",)),
    "repeated reason": lambda: _episode(reasons=("same", "same")),
    "extra without kind": lambda: _extra(kind=" "),
    "extra blank display title": lambda: _extra(display_title=" "),
    "untrimmed show key": lambda: ShowJellyfinIdentifiers(" Show"),
    "repeated jellyfin provider": lambda: ShowJellyfinIdentifiers(
        "Show",
        (
            JellyfinProviderIdentifier(JellyfinProvider.TMDB, "1"),
            JellyfinProviderIdentifier(JellyfinProvider.TMDB, "2"),
        ),
    ),
    "wrong catalog schema": lambda: _catalog(schema_version=4),
    "reviewed episode overlaps a hold": lambda: _catalog(
        source_holds=(SourceHoldOverride("Show/episode.mkv"),),
        reviewed_episode_decisions=(_episode(),),
    ),
    "reviewed set with two bindings": lambda: _catalog(
        reviewed_episode_decisions=(
            _episode(),
            _episode(
                source_binding_sha256="b" * 64,
                episode_provider_identity=ProviderIdentity.tvmaze(12),
                number=2,
            ),
        )
    ),
    "reviewed set repeats an episode": lambda: _catalog(
        reviewed_episode_decisions=(_episode(), _episode(number=2))
    ),
    "reviewed set repeats a coordinate": lambda: _catalog(
        reviewed_episode_decisions=(
            _episode(),
            _episode(episode_provider_identity=ProviderIdentity.tvmaze(12)),
        )
    ),
    "extra configured twice": lambda: _catalog(extra_decisions=(_extra(), _extra())),
    "extra overlaps a reviewed episode": lambda: _catalog(
        reviewed_episode_decisions=(_episode(),),
        extra_decisions=(_extra(source="Show/episode.mkv"),),
    ),
    "identifiers for an unknown show": lambda: _catalog(
        show_jellyfin_identifiers=(ShowJellyfinIdentifiers("Missing Show"),)
    ),
    "identifiers configured twice": lambda: _catalog(
        shows=(ShowOverride("Show", tvmaze_id=1),),
        show_jellyfin_identifiers=(
            ShowJellyfinIdentifiers("Show"),
            ShowJellyfinIdentifiers("show"),
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_malformed_reviewed_overrides_are_refused(case: str) -> None:
    with pytest.raises(ValueError):
        INVALID[case]()


def test_valid_reviewed_overrides_are_normalized() -> None:
    episode = _episode(
        title=" Pilot ", lookup_mode=" Coordinate ", airdate=" 2024-01-01 "
    )
    assert (episode.title, episode.lookup_mode, episode.airdate) == (
        "Pilot",
        "coordinate",
        "2024-01-01",
    )
    extra = _extra(kind=" Trailer ", display_title=" Teaser ")
    assert (extra.kind, extra.display_title) == ("trailer", "Teaser")

    two_part = (_episode(), replace(_episode(), number=2))
    with pytest.raises(ValueError):
        # Same provider episode id twice, even at different coordinates.
        _catalog(reviewed_episode_decisions=two_part)
    catalog = _catalog(
        shows=(ShowOverride("Show", tvmaze_id=1),),
        reviewed_episode_decisions=(
            _episode(),
            _episode(episode_provider_identity=ProviderIdentity.tvmaze(12), number=2),
        ),
        extra_decisions=(extra,),
        show_jellyfin_identifiers=(
            ShowJellyfinIdentifiers(
                "Show", (JellyfinProviderIdentifier(JellyfinProvider.TVDB, "5"),)
            ),
        ),
    )
    assert len(catalog.reviewed_episodes_for("show/EPISODE.mkv")) == 2
