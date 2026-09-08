from __future__ import annotations

from collections.abc import Mapping
from io import StringIO

import pytest

from jellyfin_show_organizer.models import ProviderIdentity
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.review_wizard import (
    _prompt_choice,
    _prompt_int,
    _review_special,
    _review_specific_episode,
)

pytestmark = pytest.mark.local

SHOW = ProviderShow(
    identity=ProviderIdentity.tvmaze(4242),
    title="Fabricated Series",
    year=2024,
)
EPISODES = (
    ProviderEpisode(
        tvmaze_episode_id=9000,
        season=0,
        number=1,
        title="Launch Special",
        airdate="2023-12-15",
        episode_type="special",
    ),
    ProviderEpisode(
        tvmaze_episode_id=9001,
        season=1,
        number=1,
        title="Pilot",
        airdate="2024-01-01",
        episode_type="regular",
    ),
    ProviderEpisode(
        tvmaze_episode_id=9002,
        season=1,
        number=2,
        title="Second",
        airdate="2024-01-08",
        episode_type="regular",
    ),
)


class Provider:
    def __init__(
        self,
        *,
        shows: tuple[ProviderShow, ...] = (SHOW,),
        episodes: tuple[ProviderEpisode, ...] = EPISODES,
        search_reason: str | None = None,
        catalog_reason: str | None = None,
        catalog_errors: tuple[str, ...] = (),
    ) -> None:
        self.shows = shows
        self.episodes = episodes
        self.search_reason = search_reason
        self.catalog_reason = catalog_reason
        self.catalog_errors = catalog_errors

    @property
    def provider_name(self) -> str:
        return "tvmaze"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        if self.search_reason is not None:
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key=f"search:{title.casefold()}",
                cache_snapshot_id="1" * 64,
                shows=(),
                unresolved_reason=self.search_reason,
            )
        return ProviderSearchSnapshot(
            provider="tvmaze",
            request_key=f"search:{title.casefold()}",
            cache_snapshot_id="1" * 64,
            shows=self.shows,
        )

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        if self.catalog_reason is not None:
            return ProviderEpisodeCatalog(
                provider="tvmaze",
                request_key=f"show:{show_identity.value}",
                cache_snapshot_id="2" * 64,
                show_identity=show_identity,
                episodes=(),
                unresolved_reason=self.catalog_reason,
            )
        return ProviderEpisodeCatalog(
            provider="tvmaze",
            request_key=f"show:{show_identity.value}",
            cache_snapshot_id="2" * 64,
            show_identity=show_identity,
            episodes=self.episodes,
            errors=self.catalog_errors,
        )


def _record() -> dict[str, object]:
    return {
        "source": {
            "relative_path": "Fabricated Series/Mystery.mkv",
            "extension": ".mkv",
        },
        "parse": {
            "series_hint": "Fabricated Series",
            "season": 1,
            "episodes": [1],
            "title_hint": "Mystery",
        },
    }


def _input(*responses: str):
    iterator = iter(responses)
    return lambda _prompt: next(iterator)


def test_episode_review_supports_absolute_lookup() -> None:
    raw: dict[str, object] = {"schema_version": 4}
    output = StringIO()

    changed = _review_specific_episode(
        raw,
        _record(),
        Provider(),
        input_fn=_input("", "1", "2", "2", "n", "y"),
        output=output,
    )

    assert changed
    decision = raw["reviewed_episode_decisions"][0]  # type: ignore[index]
    assert decision["episode_provider_id"] == "9002"  # type: ignore[index]
    assert decision["lookup_mode"] == "absolute"  # type: ignore[index]
    assert "absolute episode 2" in decision["reasons"][0]  # type: ignore[index]
    assert "Destination preview:" in output.getvalue()


def test_episode_review_supports_unique_airdate_lookup() -> None:
    raw: dict[str, object] = {"schema_version": 4}

    changed = _review_specific_episode(
        raw,
        _record(),
        Provider(),
        input_fn=_input("", "1", "3", "2024-01-08", "n", "y"),
        output=StringIO(),
    )

    assert changed
    decision = raw["reviewed_episode_decisions"][0]  # type: ignore[index]
    assert decision["episode_provider_id"] == "9002"  # type: ignore[index]
    assert decision["lookup_mode"] == "date"  # type: ignore[index]
    assert decision["airdate"] == "2024-01-08"  # type: ignore[index]


def test_episode_review_defers_when_catalog_is_unavailable_or_unsafe() -> None:
    for provider in (
        Provider(catalog_reason="fabricated unavailable"),
        Provider(catalog_errors=("fabricated malformed episode",)),
    ):
        output = StringIO()
        changed = _review_specific_episode(
            {"schema_version": 4},
            _record(),
            provider,
            input_fn=_input("", "1"),
            output=output,
        )
        assert not changed
        assert "catalog is unavailable or unsafe" in output.getvalue()


def test_episode_review_defers_for_missing_or_ambiguous_identity() -> None:
    duplicate_coordinate = EPISODES + (
        ProviderEpisode(
            tvmaze_episode_id=9010,
            season=1,
            number=1,
            title="Alternate Pilot",
            airdate="2024-01-01",
        ),
    )
    output = StringIO()

    changed = _review_specific_episode(
        {"schema_version": 4},
        _record(),
        Provider(episodes=duplicate_coordinate),
        input_fn=_input("", "1", "1", "1", "1"),
        output=output,
    )

    assert not changed
    assert "No unique numbered provider episode" in output.getvalue()


def test_show_selection_defers_for_empty_or_out_of_range_results() -> None:
    for provider, responses, expected in (
        (Provider(shows=()), ("",), "no shows"),
        (Provider(), ("", "99"), "outside the displayed"),
        (Provider(search_reason="fabricated unavailable"), ("",), "search unavailable"),
    ):
        output = StringIO()
        changed = _review_specific_episode(
            {"schema_version": 4},
            _record(),
            provider,
            input_fn=_input(*responses),
            output=output,
        )
        assert not changed
        assert expected in output.getvalue().casefold()


def test_special_review_refuses_catalog_without_numeric_specials() -> None:
    regular_only = tuple(episode for episode in EPISODES if episode.season > 0)
    output = StringIO()

    changed = _review_special(
        {"schema_version": 4},
        _record(),
        Provider(episodes=regular_only),
        input_fn=_input("", "1"),
        output=output,
    )

    assert not changed
    assert "no numerically addressable special" in output.getvalue()


def test_special_review_cancel_and_out_of_range_are_nonmutating() -> None:
    for selection in ("cancel", "99"):
        raw: dict[str, object] = {"schema_version": 4}
        changed = _review_special(
            raw,
            _record(),
            Provider(),
            input_fn=_input("", "1", selection),
            output=StringIO(),
        )
        assert not changed
        assert "reviewed_episode_decisions" not in raw


def test_prompt_int_retries_invalid_and_negative_values() -> None:
    output = StringIO()
    values = iter(("nope", "-2", "3"))

    assert _prompt_int(
        "Episode",
        input_fn=lambda _prompt: next(values),
        output=output,
    ) == 3
    assert "Enter an integer" in output.getvalue()
    assert "cannot be negative" in output.getvalue()


def test_prompt_int_honors_default_and_cancel() -> None:
    assert _prompt_int(
        "Episode",
        input_fn=lambda _prompt: "",
        output=StringIO(),
        default=7,
    ) == 7
    assert _prompt_int(
        "Episode",
        input_fn=lambda _prompt: "cancel",
        output=StringIO(),
    ) is None


def test_prompt_choice_retries_until_valid() -> None:
    output = StringIO()
    values = iter(("bad", "2"))

    selected = _prompt_choice(
        "Choose",
        {"1": "One", "2": "Two"},
        input_fn=lambda _prompt: next(values),
        output=output,
    )

    assert selected == "2"
    assert "Invalid choice" in output.getvalue()
