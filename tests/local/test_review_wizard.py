from __future__ import annotations

import io
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import JellyfinProvider
from jellyfin_show_organizer.models import (
    CanonicalShow,
    DuplicateDecision,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanEpisode,
    PlanRecord,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.review_overrides import (
    ReviewOverrideCatalog,
    load_planning_overrides,
)
from jellyfin_show_organizer.review_wizard import run_review_wizard
from jellyfin_show_organizer.schema import plan_to_manifest

pytestmark = pytest.mark.local


class FakeProvider:
    provider_name = "tvmaze"

    def __init__(
        self,
        *,
        show: ProviderShow | None = None,
        episodes: tuple[ProviderEpisode, ...] = (),
    ) -> None:
        self.show = show or ProviderShow(
            identity=ProviderIdentity.tvmaze(4242),
            title="Fabricated Series",
            year=2024,
        )
        self.episodes = episodes or (
            ProviderEpisode(
                identity=ProviderIdentity.tvmaze(9001),
                season=1,
                number=1,
                title="Fabricated Pilot",
                airdate="2024-01-01",
            ),
            ProviderEpisode(
                identity=ProviderIdentity.tvmaze(9002),
                season=0,
                number=1,
                title="Fabricated Special",
                airdate="2024-01-08",
                episode_type="special",
            ),
        )

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="tvmaze",
            request_key=title,
            cache_snapshot_id="search-snapshot",
            shows=(self.show,),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == self.show.identity
        return ProviderEpisodeCatalog(
            provider="tvmaze",
            request_key=show_identity.value,
            cache_snapshot_id="episode-snapshot",
            show_identity=show_identity,
            episodes=self.episodes,
        )


def _source(path: str) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=100, mtime_ns=200),
    )


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Fabricated Series",
        provider_identity=ProviderIdentity.tvmaze(4242),
        title="Fabricated Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _evidence() -> MatchEvidence:
    return MatchEvidence(
        method="fabricated-review-evidence",
        confidence=1.0,
        reasons=("fabricated evidence",),
    )


def _held_plan() -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=2,
        overrides_version=4,
        records=(
            PlanRecord(
                source=_source("Fabricated Series/Unknown Special.mkv"),
                status=TerminalStatus.HELD,
                parse=ParseResult(
                    series_hint="Fabricated Series",
                    season=1,
                    episodes=(1,),
                    title_hint="Unknown Special",
                ),
                evidence=MatchEvidence(
                    method="source-hold-override",
                    confidence=1.0,
                    reasons=("review me",),
                ),
                operation_group_id="op-held",
                reason="review me",
            ),
        ),
    )


def _duplicate_plan() -> OrganizerPlan:
    first = "Fabricated Series/Fabricated.Series.S01E01.WEB.mkv"
    second = "Fabricated Series/Fabricated.Series.S01E01.BD.mkv"
    decision = DuplicateDecision(
        destination_key="fabricated series/season 01/fabricated s01e01.mkv",
        candidates=(first, second),
        winner=second,
        losers=(first,),
        confidence=1.0,
        evidence=("fabricated duplicate quality comparison",),
    )
    episode = PlanEpisode(
        provider_identity=ProviderIdentity.tvmaze(9001),
        season=1,
        number=1,
        title="Fabricated Pilot",
        airdate="2024-01-01",
    )
    parse = ParseResult(series_hint="Fabricated Series", season=1, episodes=(1,))
    destination = (
        "Fabricated Series (2024)/Season 01/"
        "Fabricated Series (2024) S01E01 - Fabricated Pilot.mkv"
    )
    return OrganizerPlan(
        schema_version=2,
        overrides_version=4,
        records=(
            PlanRecord(
                source=_source(first),
                status=TerminalStatus.DUPLICATE,
                parse=parse,
                show=_show(),
                evidence=_evidence(),
                destination=destination,
                duplicate=decision,
                operation_group_id="op-web",
                provider_episodes=(episode,),
                reason="non-destructive duplicate loser",
            ),
            PlanRecord(
                source=_source(second),
                status=TerminalStatus.MATCHED,
                parse=parse,
                show=_show(),
                evidence=_evidence(),
                destination=destination,
                duplicate=decision,
                operation_group_id="op-bd",
                provider_episodes=(episode,),
            ),
        ),
    )


def _schema4_overrides(*, with_duplicate_preference: bool = False) -> bytes:
    preference = ""
    if with_duplicate_preference:
        preference = """

[[duplicate_preferences]]
source = "Fabricated Series/Fabricated.Series.S01E01.BD.mkv"
rank = 50
reasons = ["existing reviewed preference"]
"""
    return (
        """schema_version = 4

[[shows]]
key = "Fabricated Series"
provider = "tvmaze"
provider_id = "4242"
aliases = []
year = 2024
numbering_mode = "aired"
title_preference = "provider"

[[source_holds]]
source = "Fabricated Series/Unknown Special.mkv"
reasons = ["review me"]
"""
        + preference
    ).encode("utf-8")


def _inputs(values: list[str]) -> callable:
    iterator: Iterator[str] = iter(values)

    def read(_prompt: str) -> str:
        return next(iterator)

    return read


def test_duplicate_accept_recommended_persists_explicit_winner() -> None:
    rendered, summary = run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["1"]),
        output=io.StringIO(),
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    preferences = raw["duplicate_preferences"]
    assert raw["schema_version"] == 5
    assert len(preferences) == 1
    assert preferences[0]["source"].endswith("S01E01.BD.mkv")
    assert preferences[0]["rank"] == 100
    assert summary.duplicate_groups_changed == 1


def test_duplicate_select_different_winner_and_keep_all_are_explicit() -> None:
    rendered, _ = run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["2", "1"]),
        output=io.StringIO(),
    )
    raw = tomllib.loads(rendered.decode("utf-8"))
    assert raw["duplicate_preferences"][0]["source"].endswith("S01E01.WEB.mkv")

    held_rendered, _ = run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["3"]),
        output=io.StringIO(),
    )
    held_raw = tomllib.loads(held_rendered.decode("utf-8"))
    held_sources = {entry["source"] for entry in held_raw["source_holds"]}
    assert {
        "Fabricated Series/Fabricated.Series.S01E01.WEB.mkv",
        "Fabricated Series/Fabricated.Series.S01E01.BD.mkv",
    } <= held_sources
    assert "duplicate_preferences" not in held_raw or not held_raw["duplicate_preferences"]


def test_duplicate_quarantine_is_only_a_non_moving_marker() -> None:
    rendered, _ = run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["4"]),
        output=io.StringIO(),
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    markers = [
        entry
        for entry in raw["source_holds"]
        if "review-quarantine-candidate" in entry["reasons"]
    ]
    assert len(markers) == 1
    assert markers[0]["source"].endswith("S01E01.WEB.mkv")
    assert all("delete" not in reason.casefold() for reason in markers[0]["reasons"])


def test_duplicate_defer_preserves_existing_preference() -> None:
    rendered, summary = run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(with_duplicate_preference=True),
        FakeProvider(),
        input_fn=_inputs(["5"]),
        output=io.StringIO(),
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    assert raw["duplicate_preferences"][0]["rank"] == 50
    assert raw["duplicate_preferences"][0]["reasons"] == [
        "existing reviewed preference"
    ]
    assert summary.deferred == 1


def test_held_episode_requires_provider_confirmation_and_previews_jellyfin_ids() -> None:
    output = io.StringIO()
    rendered, summary = run_review_wizard(
        plan_to_manifest(_held_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(
            [
                "2",  # held action: episode
                "",  # default show search
                "1",  # provider show
                "1",  # coordinate lookup
                "",  # default season 1
                "",  # default episode 1
                "y",  # edit Jellyfin IDs
                "79168",  # TVDB
                "12345",  # TMDB
                "tt1234567",  # IMDb
                "y",  # write decision
            ]
        ),
        output=output,
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    assert raw["schema_version"] == 5
    assert "source_holds" not in raw or not raw["source_holds"]
    reviewed = raw["reviewed_episode_decisions"][0]
    assert reviewed["episode_provider_id"] == "9001"
    assert reviewed["season"] == 1
    assert reviewed["number"] == 1
    assert reviewed["lookup_mode"] == "coordinate"
    show = raw["shows"][0]
    assert show["tvdb_id"] == "79168"
    assert show["tmdb_id"] == "12345"
    assert show["imdb_id"] == "tt1234567"
    assert "[tvdbid-79168]" in output.getvalue()
    assert "[tmdbid-12345]" in output.getvalue()
    assert "[imdbid-tt1234567]" in output.getvalue()
    assert summary.held_records_changed == 1


def test_held_special_is_provider_catalog_confirmed() -> None:
    rendered, _ = run_review_wizard(
        plan_to_manifest(_held_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(
            [
                "3",  # held action: special
                "",  # search
                "1",  # show
                "1",  # first listed special
                "n",  # do not edit external IDs
                "y",  # confirm
            ]
        ),
        output=io.StringIO(),
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    reviewed = raw["reviewed_episode_decisions"][0]
    assert reviewed["episode_provider_id"] == "9002"
    assert reviewed["lookup_mode"] == "special"
    assert reviewed["season"] == 0


def test_unnumbered_special_stays_held() -> None:
    provider = FakeProvider(
        episodes=(
            ProviderEpisode(
                identity=ProviderIdentity.tvmaze(9003),
                season=0,
                number=None,
                title="Unnumbered Fabricated Special",
                episode_type="special",
            ),
        )
    )
    rendered, summary = run_review_wizard(
        plan_to_manifest(_held_plan()),
        _schema4_overrides(),
        provider,
        input_fn=_inputs(["3", "", "1"]),
        output=io.StringIO(),
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    assert raw["source_holds"][0]["source"].endswith("Unknown Special.mkv")
    assert not raw.get("reviewed_episode_decisions")
    assert summary.deferred == 1


def test_held_extra_is_first_class_and_has_destination_preview() -> None:
    output = io.StringIO()
    rendered, summary = run_review_wizard(
        plan_to_manifest(_held_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(
            [
                "4",  # held action: extra
                "",  # show search
                "1",  # provider show
                "1",  # trailer
                "Fabricated Trailer",
                "n",  # no external IDs
                "y",  # write
            ]
        ),
        output=output,
    )

    raw = tomllib.loads(rendered.decode("utf-8"))
    extra = raw["extra_decisions"][0]
    assert extra["kind"] == "trailer"
    assert extra["display_title"] == "Fabricated Trailer"
    assert "source_holds" not in raw or not raw["source_holds"]
    assert "/trailers/" in output.getvalue()
    assert summary.held_records_changed == 1


def test_schema5_roundtrip_has_deterministic_snapshot(tmp_path: Path) -> None:
    rendered, _ = run_review_wizard(
        plan_to_manifest(_held_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["1"]),
        output=io.StringIO(),
    )
    path = tmp_path / "reviewed.toml"
    path.write_bytes(rendered)

    first = load_planning_overrides(path)
    second = load_planning_overrides(path)
    assert isinstance(first, ReviewOverrideCatalog)
    assert first.schema_version == 5
    assert first.snapshot_id == second.snapshot_id


def test_review_contains_no_delete_action_text() -> None:
    output = io.StringIO()
    run_review_wizard(
        plan_to_manifest(_duplicate_plan()),
        _schema4_overrides(),
        FakeProvider(),
        input_fn=_inputs(["5"]),
        output=output,
    )

    assert "No delete operation exists" in output.getvalue()
    assert ") Delete" not in output.getvalue()
