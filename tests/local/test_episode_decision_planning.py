from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import DestinationPolicy
from jellyfin_show_organizer.extra_classifier import (
    ExtraClassification,
    ExtraDisposition,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import EpisodeDecisionOverride, OverrideCatalog
from jellyfin_show_organizer.planner import (
    PlanningConfigurationError,
    TrackingTvmazeCatalogCache,
    _plan_resolved_group,
    _validate_episode_decision_consumption,
    _validate_episode_decision_sources,
)
from jellyfin_show_organizer.providers import TvmazeProviderAdapter
from jellyfin_show_organizer.show_resolver import ResolutionStatus, ShowResolution

pytestmark = pytest.mark.local

CATALOG = [
    {"id": 1001, "season": 1, "number": 1, "name": "Part Alpha"},
    {"id": 1002, "season": 1, "number": 2, "name": "Part Beta"},
]
SOURCE = "Example Series/Example Series - ambiguous.mkv"


class CountingGetter:
    def __init__(self, response: object = CATALOG) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.response


def _source() -> SourceFile:
    return SourceFile(
        relative_path=SOURCE,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=100, mtime_ns=10),
    )


def _classification(
    disposition: ExtraDisposition = ExtraDisposition.EPISODE_CANDIDATE,
) -> ExtraClassification:
    return ExtraClassification(
        disposition=disposition,
        parse=ParseResult(series_hint="Example Series", year=2024),
        reasons=("synthetic classification",),
    )


def _resolution(
    mode: NumberingMode = NumberingMode.AIRED,
) -> ShowResolution:
    return ShowResolution(
        status=ResolutionStatus.MATCHED,
        show=CanonicalShow(
            source_key="Example Series",
            provider_identity=ProviderIdentity.tvmaze(45001),
            title="Example Series",
            year=2024,
            numbering_mode=mode,
        ),
        evidence=MatchEvidence(
            method="synthetic-show-resolution",
            confidence=1.0,
            reasons=("synthetic resolved show",),
        ),
    )


def _decision(
    *,
    provider_id: int = 45001,
    mode: NumberingMode = NumberingMode.AIRED,
    parse: ParseResult | None = None,
) -> EpisodeDecisionOverride:
    if parse is None:
        parse = ParseResult(season=1, episodes=(2,))
    return EpisodeDecisionOverride(
        source=SOURCE,
        show_provider_identity=ProviderIdentity.tvmaze(provider_id),
        numbering_mode=mode,
        parse=parse,
        reasons=("reviewed local episode numbering",),
    )


def _overrides(decision: EpisodeDecisionOverride) -> OverrideCatalog:
    return OverrideCatalog(
        schema_version=3,
        shows=(),
        episode_decisions=(decision,),
    )


def _plan(
    tmp_path: Path,
    decision: EpisodeDecisionOverride,
    *,
    resolution: ShowResolution | None = None,
    classification: ExtraClassification | None = None,
    getter: CountingGetter | None = None,
):
    resolved = resolution or _resolution()
    classified = classification or _classification()
    provider = getter or CountingGetter()
    cache = TrackingTvmazeCatalogCache(
        tmp_path / "cache",
        offline=False,
        refresh=False,
    )
    records = _plan_resolved_group(
        (_source(),),
        {SOURCE: classified},
        resolved,
        TvmazeProviderAdapter(cache, provider),
        DestinationPolicy(),
        _overrides(decision),
    )
    return records, provider


def test_episode_decision_resolves_reviewed_numbering_through_catalog(
    tmp_path: Path,
) -> None:
    records, getter = _plan(tmp_path, _decision())

    assert len(records) == 1
    record = records[0]
    assert record.status is TerminalStatus.MATCHED
    assert record.parse.season == 1
    assert record.parse.episodes == (2,)
    assert [episode.tvmaze_episode_id for episode in record.provider_episodes] == [1002]
    assert "episode-decision-override" in record.evidence.method
    assert "reviewed local episode numbering" in record.evidence.reasons
    assert len(getter.calls) == 1


def test_episode_decision_does_not_bypass_missing_catalog_entry(
    tmp_path: Path,
) -> None:
    getter = CountingGetter(
        [{"id": 1001, "season": 1, "number": 1, "name": "Part Alpha"}]
    )
    records, _ = _plan(tmp_path, _decision(), getter=getter)

    record = records[0]
    assert record.status is TerminalStatus.UNRESOLVED
    assert record.provider_episodes == ()
    assert "episode-decision-override" in record.evidence.method
    assert "missing-aired-catalog-entry:S01E02" in record.evidence.reasons


def test_episode_decision_provider_identity_conflict_fails_before_catalog_access(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()

    with pytest.raises(
        PlanningConfigurationError,
        match="provider identity conflicts with resolved show",
    ):
        _plan(tmp_path, _decision(provider_id=45002), getter=getter)

    assert getter.calls == []


def test_episode_decision_numbering_mode_conflict_fails_before_catalog_access(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    decision = _decision(
        mode=NumberingMode.ABSOLUTE,
        parse=ParseResult(absolute_episode=2),
    )

    with pytest.raises(
        PlanningConfigurationError,
        match="numbering mode conflicts with resolved show",
    ):
        _plan(tmp_path, decision, getter=getter)

    assert getter.calls == []


def test_episode_decision_cannot_reclassify_non_episode_source(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()

    with pytest.raises(
        PlanningConfigurationError,
        match="source is not an episode candidate",
    ):
        _plan(
            tmp_path,
            _decision(),
            classification=_classification(ExtraDisposition.UNRESOLVED),
            getter=getter,
        )

    assert getter.calls == []


def test_episode_decision_rejects_unknown_source() -> None:
    decision = _decision()
    with pytest.raises(
        PlanningConfigurationError,
        match="references an unknown source",
    ):
        _validate_episode_decision_sources((), _overrides(decision))


def test_episode_decision_rejects_unconsumed_existing_source() -> None:
    decision = _decision()
    source = _source()
    _validate_episode_decision_sources((source,), _overrides(decision))

    with pytest.raises(
        PlanningConfigurationError,
        match="could not be consumed safely",
    ):
        _validate_episode_decision_consumption([], _overrides(decision))
