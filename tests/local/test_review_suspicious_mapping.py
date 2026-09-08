from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import DestinationPolicy
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
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
from jellyfin_show_organizer.review_contract import (
    ReviewContractCatalog,
    load_review_contract_payload,
)
from jellyfin_show_organizer.review_execution import (
    PlanningConfigurationError,
    _reviewed_episode_record,
)
from jellyfin_show_organizer.review_session import ReviewItemKind
from jellyfin_show_organizer.review_system import run_review_system
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local

SHOW = "Synthetic Compound Series"
SOURCE = f"{SHOW}/release.mkv"
SHOW_ID = ProviderIdentity.tvmaze(4242)
EMPTY_BASE = b"schema_version = 4\n"


def _episodes(*, changed_second_title: bool = False) -> tuple[ProviderEpisode, ...]:
    return (
        ProviderEpisode(
            tvmaze_episode_id=9001,
            season=1,
            number=1,
            title="Part One",
        ),
        ProviderEpisode(
            tvmaze_episode_id=9002,
            season=1,
            number=2,
            title="Changed Part Two" if changed_second_title else "Part Two",
        ),
    )


class FixtureProvider:
    provider_name = "tvmaze"

    def __init__(self, *, changed_second_title: bool = False) -> None:
        self.changed_second_title = changed_second_title

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="tvmaze",
            request_key=f"search:{title.casefold()}",
            cache_snapshot_id="search-snapshot",
            shows=(ProviderShow(identity=SHOW_ID, title=SHOW, year=2024),),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        return ProviderEpisodeCatalog(
            provider="tvmaze",
            request_key="episodes:4242",
            cache_snapshot_id="episode-snapshot",
            show_identity=SHOW_ID,
            episodes=_episodes(changed_second_title=self.changed_second_title),
        )


def _record(*, fingerprint_sha: str = "a" * 64) -> PlanRecord:
    return PlanRecord(
        source=SourceFile(
            relative_path=SOURCE,
            extension=".mkv",
            fingerprint=SourceFingerprint(
                size=1_000,
                mtime_ns=2_000,
                sha256=fingerprint_sha,
            ),
        ),
        status=TerminalStatus.SUSPICIOUS,
        parse=ParseResult(
            series_hint=SHOW,
            season=1,
            episodes=(1, 2),
            title_hint="Part One - Part Two",
        ),
        show=CanonicalShow(
            source_key=SHOW,
            tvmaze_id=4242,
            title=SHOW,
            year=2024,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(
            method="synthetic-review-required",
            confidence=0.0,
            reasons=("synthetic compound episode ambiguity",),
        ),
        reason="synthetic compound episode ambiguity",
    )


def _plan(record: PlanRecord | None = None) -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=(record or _record(),),
    )


def _input(*responses: str):
    iterator = iter(responses)
    return lambda _prompt: next(iterator)


def _base_snapshot() -> str:
    return load_review_contract_payload(EMPTY_BASE).snapshot_id


def _review_compound(tmp_path: Path) -> tuple[object, bytes, OrganizerPlan]:
    plan = _plan()
    manifest = plan_to_manifest(plan)
    session_path = tmp_path / "session.json"
    active_path = tmp_path / "active.toml"
    session, active = run_review_system(
        manifest,
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=FixtureProvider(),
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input(
            "m",  # choose compound episode review
            "",  # default show search
            "1",  # choose the single show candidate
            "",  # default to parsed episode count (2)
            "",  # episode 1 season
            "",  # episode 1 number
            "",  # episode 2 season
            "",  # episode 2 number
            "n",  # keep Jellyfin IDs unchanged
            "y",  # confirm the compound decision
        ),
        output=StringIO(),
    )
    return session, active, plan


def test_suspicious_source_enters_existing_source_review_ledger(tmp_path: Path) -> None:
    plan = _plan()
    session_path = tmp_path / "session.json"
    active_path = tmp_path / "active.toml"

    session, _ = run_review_system(
        plan_to_manifest(plan),
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=FixtureProvider(),
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("h"),
        output=StringIO(),
    )

    assert session.complete
    assert len(session.items) == 1
    item = session.items[0]
    assert item.kind is ReviewItemKind.HELD
    assert item.source == SOURCE
    assert item.action == "keep_held"


def test_keep_held_compiles_a_real_source_hold_for_suspicious_input(
    tmp_path: Path,
) -> None:
    plan = _plan()
    session_path = tmp_path / "session.json"
    active_path = tmp_path / "active.toml"

    _, active = run_review_system(
        plan_to_manifest(plan),
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=FixtureProvider(),
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("h"),
        output=StringIO(),
    )

    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    assert [hold.source for hold in catalog.source_holds] == [SOURCE]
    assert not catalog.reviewed_episode_decisions


def test_compound_review_persists_exact_provider_episode_set(tmp_path: Path) -> None:
    session, active, plan = _review_compound(tmp_path)

    assert session.complete
    assert session.items[0].action == "multi_episode"
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    reviewed = catalog.reviewed_episodes_for(SOURCE)
    assert [(item.season, item.number) for item in reviewed] == [(1, 1), (1, 2)]
    assert [item.episode_provider_identity.value for item in reviewed] == ["9001", "9002"]
    assert len({item.source_binding_sha256 for item in reviewed}) == 1

    updated = _reviewed_episode_record(
        plan.records[0],
        plan,
        catalog,
        FixtureProvider(),
        DestinationPolicy(max_path_length=240, max_component_length=180),
    )
    assert updated.status is TerminalStatus.MATCHED
    assert updated.destination is not None
    assert updated.evidence is not None
    assert updated.evidence.method == "reviewed-provider-episode-set"
    assert [episode.provider_identity.value for episode in updated.provider_episodes] == [
        "9001",
        "9002",
    ]


def test_compound_review_fails_closed_when_provider_metadata_changes(
    tmp_path: Path,
) -> None:
    _, active, plan = _review_compound(tmp_path)
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)

    with pytest.raises(
        PlanningConfigurationError,
        match="provider episode metadata changed",
    ):
        _reviewed_episode_record(
            plan.records[0],
            plan,
            catalog,
            FixtureProvider(changed_second_title=True),
            DestinationPolicy(max_path_length=240, max_component_length=180),
        )


def test_compound_review_fails_closed_when_source_fingerprint_changes(
    tmp_path: Path,
) -> None:
    _, active, plan = _review_compound(tmp_path)
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    changed = _record(fingerprint_sha="b" * 64)
    changed_plan = _plan(changed)

    with pytest.raises(
        PlanningConfigurationError,
        match="source fingerprint or companion set changed",
    ):
        _reviewed_episode_record(
            changed,
            changed_plan,
            catalog,
            FixtureProvider(),
            DestinationPolicy(max_path_length=240, max_component_length=180),
        )
