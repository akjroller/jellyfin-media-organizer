from __future__ import annotations

import pytest

from jellyfin_show_organizer.destination import DestinationPolicy
from jellyfin_show_organizer.models import (
    CanonicalShow,
    DuplicateCollisionClass,
    DuplicateDecision,
    ExtraDecision,
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
)
from jellyfin_show_organizer.review_contract import ReviewContractCatalog
from jellyfin_show_organizer.review_execution import (
    PlanningConfigurationError,
    _bindings_for_group,
    _clear_duplicate_decisions,
    _confirmed_provider_episode,
    _explicit_extra_record,
    _reviewed_episode_record,
    _source_binding,
)
from jellyfin_show_organizer.review_overrides import (
    ExplicitExtraOverride,
    ReviewedEpisodeOverride,
)

pytestmark = pytest.mark.local

SHOW_ID = 4242
EPISODE_ID = 9001
SOURCE = "Fabricated Series/Mystery.mkv"
OTHER = "Fabricated Series/Other.mkv"
DESTINATION = "Fabricated Series/Season 01/Fabricated Series S01E01.mkv"
PARSE = ParseResult(
    series_hint="Fabricated Series",
    season=1,
    episodes=(1,),
    title_hint="Pilot",
)
SHOW = CanonicalShow(
    source_key="Fabricated Series",
    tvmaze_id=SHOW_ID,
    title="Fabricated Series",
    numbering_mode=NumberingMode.AIRED,
)
OTHER_SHOW = CanonicalShow(
    source_key="Fabricated Series",
    tvmaze_id=9999,
    title="Fabricated Series",
    numbering_mode=NumberingMode.AIRED,
)
EVIDENCE = MatchEvidence(method="fabricated", confidence=1.0)
EPISODE = ProviderEpisode(
    tvmaze_episode_id=EPISODE_ID,
    season=1,
    number=1,
    title="Pilot",
    airdate="2024-01-01",
)


class Provider:
    def __init__(
        self,
        *,
        episodes: tuple[ProviderEpisode, ...] = (EPISODE,),
        unresolved_reason: str | None = None,
        errors: tuple[str, ...] = (),
    ) -> None:
        self.episodes = episodes
        self.unresolved_reason = unresolved_reason
        self.errors = errors

    @property
    def provider_name(self) -> str:
        return "tvmaze"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="tvmaze",
            request_key=f"search:{title.casefold()}",
            cache_snapshot_id="1" * 64,
            shows=(),
            unresolved_reason="unused in fabricated record-guard tests",
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        return ProviderEpisodeCatalog(
            provider="tvmaze",
            request_key=f"show:{show_identity.value}",
            cache_snapshot_id="2" * 64,
            show_identity=show_identity,
            episodes=() if self.unresolved_reason is not None else self.episodes,
            unresolved_reason=self.unresolved_reason,
            errors=self.errors,
        )


def _source(path: str = SOURCE, *, sha: str = "a" * 64) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=100, mtime_ns=200, sha256=sha),
    )


def _record(
    *,
    show: CanonicalShow | None = SHOW,
    source: str = SOURCE,
) -> PlanRecord:
    return PlanRecord(
        source=_source(source),
        status=TerminalStatus.SUSPICIOUS,
        parse=PARSE,
        show=show,
        evidence=EVIDENCE,
        reason="fabricated review candidate",
    )


def _plan(record: PlanRecord) -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=3,
        overrides_version=5,
        records=(record,),
    )


def _catalog(
    *,
    reviewed: tuple[ReviewedEpisodeOverride, ...] = (),
    extras: tuple[ExplicitExtraOverride, ...] = (),
) -> ReviewContractCatalog:
    return ReviewContractCatalog(
        schema_version=5,
        shows=(),
        reviewed_episode_decisions=reviewed,
        extra_decisions=extras,
        review_session_sha256="1" * 64,
        review_base_plan_sha256="2" * 64,
        review_base_override_snapshot="3" * 64,
    )


def _reviewed_decision(
    plan: OrganizerPlan,
    *,
    binding: str | None = None,
    show_identity: ProviderIdentity | None = None,
) -> ReviewedEpisodeOverride:
    return ReviewedEpisodeOverride(
        source=SOURCE,
        source_binding_sha256=binding or _source_binding(plan, SOURCE),
        show_provider_identity=show_identity or ProviderIdentity.tvmaze(SHOW_ID),
        episode_provider_identity=ProviderIdentity.tvmaze(EPISODE_ID),
        season=1,
        number=1,
        title="Pilot",
        airdate="2024-01-01",
        lookup_mode="coordinate",
    )


def _extra_decision(
    plan: OrganizerPlan,
    *,
    binding: str | None = None,
    show_identity: ProviderIdentity | None = None,
) -> ExplicitExtraOverride:
    return ExplicitExtraOverride(
        source=SOURCE,
        source_binding_sha256=binding or _source_binding(plan, SOURCE),
        show_provider_identity=show_identity or ProviderIdentity.tvmaze(SHOW_ID),
        kind="trailer",
        display_title="Reviewed Trailer",
    )


def test_reviewed_binding_guards_reject_missing_members() -> None:
    plan = _plan(_record())

    with pytest.raises(
        PlanningConfigurationError,
        match="reviewed duplicate candidate is missing",
    ):
        _bindings_for_group(plan, (SOURCE, OTHER))

    with pytest.raises(
        PlanningConfigurationError,
        match="reviewed source is missing",
    ):
        _source_binding(plan, OTHER)


def test_clear_duplicate_decisions_restores_matched_extra_and_plain_records() -> None:
    duplicate = DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(SOURCE, OTHER),
        winner=SOURCE,
        losers=(OTHER,),
        confidence=1.0,
        evidence=("fabricated duplicate",),
        collision_class=DuplicateCollisionClass.SAME_LOGICAL_IDENTITY,
    )
    planned_episode = PlanEpisode(
        tvmaze_episode_id=EPISODE_ID,
        season=1,
        number=1,
        title="Pilot",
        airdate="2024-01-01",
    )
    winner = PlanRecord(
        source=_source(SOURCE),
        status=TerminalStatus.MATCHED,
        parse=PARSE,
        show=SHOW,
        evidence=EVIDENCE,
        destination=DESTINATION,
        duplicate=duplicate,
        provider_episodes=(planned_episode,),
    )
    loser = PlanRecord(
        source=_source(OTHER),
        status=TerminalStatus.DUPLICATE,
        parse=PARSE,
        show=SHOW,
        evidence=EVIDENCE,
        destination=DESTINATION,
        duplicate=duplicate,
        provider_episodes=(planned_episode,),
        reason="non-destructive duplicate loser",
    )
    extra = PlanRecord(
        source=_source("Fabricated Series/Trailer.mkv"),
        status=TerminalStatus.DUPLICATE,
        destination="Fabricated Series/Extras/Trailer.mkv",
        extra=ExtraDecision(kind="trailer", rule="fabricated"),
        duplicate=duplicate,
        reason="duplicate extra",
    )
    untouched = _record(source="Fabricated Series/Untouched.mkv")

    restored = _clear_duplicate_decisions((winner, loser, extra, untouched))

    assert [item.status for item in restored] == [
        TerminalStatus.MATCHED,
        TerminalStatus.MATCHED,
        TerminalStatus.EXTRA,
        TerminalStatus.SUSPICIOUS,
    ]
    assert all(item.duplicate is None for item in restored[:3])
    assert restored[3] is untouched


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        (Provider(unresolved_reason="fabricated unavailable"), "unavailable or unsafe"),
        (Provider(errors=("fabricated malformed",)), "unavailable or unsafe"),
        (Provider(episodes=()), "missing or ambiguous"),
        (
            Provider(
                episodes=(
                    ProviderEpisode(
                        tvmaze_episode_id=EPISODE_ID,
                        season=1,
                        number=1,
                        title="Retitled Pilot",
                        airdate="2024-01-01",
                    ),
                )
            ),
            "metadata changed",
        ),
    ],
)
def test_confirmed_provider_episode_fails_closed(
    provider: Provider,
    message: str,
) -> None:
    plan = _plan(_record())
    decision = _reviewed_decision(plan)

    with pytest.raises(PlanningConfigurationError, match=message):
        _confirmed_provider_episode(provider, decision)


def test_confirmed_provider_episode_returns_exact_identity() -> None:
    plan = _plan(_record())
    decision = _reviewed_decision(plan)

    assert _confirmed_provider_episode(Provider(), decision) == EPISODE


def test_reviewed_episode_record_returns_unchanged_without_decision() -> None:
    record = _record()
    plan = _plan(record)

    assert (
        _reviewed_episode_record(
            record,
            plan,
            _catalog(),
            Provider(),
            DestinationPolicy(),
        )
        is record
    )


@pytest.mark.parametrize(
    ("record", "decision_factory", "message"),
    [
        (
            _record(),
            lambda plan: _reviewed_decision(plan, binding="f" * 64),
            "source fingerprint or companion set changed",
        ),
        (
            _record(show=None),
            lambda plan: _reviewed_decision(plan),
            "could not resolve a verified show identity",
        ),
        (
            _record(show=OTHER_SHOW),
            lambda plan: _reviewed_decision(plan),
            "conflicts with resolved show identity",
        ),
    ],
)
def test_reviewed_episode_record_rejects_stale_or_conflicting_state(
    record: PlanRecord,
    decision_factory,
    message: str,
) -> None:
    plan = _plan(record)
    decision = decision_factory(plan)

    with pytest.raises(PlanningConfigurationError, match=message):
        _reviewed_episode_record(
            record,
            plan,
            _catalog(reviewed=(decision,)),
            Provider(),
            DestinationPolicy(),
        )


def test_reviewed_episode_record_builds_verified_match() -> None:
    record = _record()
    plan = _plan(record)
    decision = _reviewed_decision(plan)

    updated = _reviewed_episode_record(
        record,
        plan,
        _catalog(reviewed=(decision,)),
        Provider(),
        DestinationPolicy(),
    )

    assert updated.status is TerminalStatus.MATCHED
    assert updated.destination is not None
    assert updated.provider_episodes[0].provider_identity == ProviderIdentity.tvmaze(
        EPISODE_ID
    )
    assert updated.evidence is not None
    assert updated.evidence.method == "reviewed-provider-episode"


def test_explicit_extra_record_returns_unchanged_without_decision() -> None:
    record = _record()
    plan = _plan(record)

    assert (
        _explicit_extra_record(
            record,
            plan,
            _catalog(),
            DestinationPolicy(),
        )
        is record
    )


@pytest.mark.parametrize(
    ("record", "decision_factory", "message"),
    [
        (
            _record(),
            lambda plan: _extra_decision(plan, binding="f" * 64),
            "source fingerprint or companion set changed",
        ),
        (
            _record(show=None),
            lambda plan: _extra_decision(plan),
            "could not resolve a verified show identity",
        ),
        (
            _record(show=OTHER_SHOW),
            lambda plan: _extra_decision(plan),
            "conflicts with resolved show identity",
        ),
    ],
)
def test_explicit_extra_record_rejects_stale_or_conflicting_state(
    record: PlanRecord,
    decision_factory,
    message: str,
) -> None:
    plan = _plan(record)
    decision = decision_factory(plan)

    with pytest.raises(PlanningConfigurationError, match=message):
        _explicit_extra_record(
            record,
            plan,
            _catalog(extras=(decision,)),
            DestinationPolicy(),
        )


def test_explicit_extra_record_builds_verified_extra() -> None:
    record = _record()
    plan = _plan(record)
    decision = _extra_decision(plan)

    updated = _explicit_extra_record(
        record,
        plan,
        _catalog(extras=(decision,)),
        DestinationPolicy(),
    )

    assert updated.status is TerminalStatus.EXTRA
    assert updated.destination is not None
    assert updated.extra is not None
    assert updated.extra.kind == "trailer"
    assert updated.evidence is not None
    assert updated.evidence.method == "explicit-extra-review+extra-naming"
