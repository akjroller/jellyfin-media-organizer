from jellyfin_show_organizer.destination import (
    DestinationStatus,
    build_episode_destination,
)
from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    ProviderEpisode,
    SourceEpisodeAssignment,
)
from jellyfin_show_organizer.models import (
    CandidateEvidence,
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
from jellyfin_show_organizer.schema import load_plan_schema, plan_to_manifest


def _generic_show() -> CanonicalShow:
    return CanonicalShow(
        source_key="fixture-harbor",
        provider_identity=ProviderIdentity("fixture", "show-17"),
        title="Fixture Harbor",
        year=2026,
        numbering_mode=NumberingMode.AIRED,
    )


def _generic_assignment() -> SourceEpisodeAssignment:
    return SourceEpisodeAssignment(
        source_key="Fixture Harbor S01E01.mkv",
        status=AssignmentStatus.MATCHED,
        episodes=(
            ProviderEpisode(
                provider_identity=ProviderIdentity("fixture", "episode-a"),
                season=1,
                number=1,
                title="Arrival",
            ),
        ),
        evidence=MatchEvidence(method="fixture-catalog", confidence=1.0),
    )


def test_destination_uses_canonical_namespaced_identity_without_tvmaze_assumption() -> (
    None
):
    decision = build_episode_destination(
        _generic_show(),
        _generic_assignment(),
        ".mkv",
    )

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path == (
        "Fixture Harbor (2026)/Season 01/Fixture Harbor (2026) S01E01 - Arrival.mkv"
    )
    assert "canonical-provider-identity:fixture:show-17" in decision.reasons
    assert not any(
        reason.startswith("canonical-tvmaze-id:") for reason in decision.reasons
    )


def test_plan_manifest_serializes_provider_plus_id_instead_of_bare_tvmaze_id() -> None:
    identity = ProviderIdentity("fixture", "show-17")
    plan = OrganizerPlan(
        schema_version=1,
        overrides_version=2,
        records=(
            PlanRecord(
                source=SourceFile(
                    relative_path="Fixture Harbor/Fixture Harbor S01E01.mkv",
                    extension=".mkv",
                    fingerprint=SourceFingerprint(size=123, mtime_ns=456),
                ),
                status=TerminalStatus.MATCHED,
                parse=ParseResult(
                    series_hint="Fixture Harbor",
                    season=1,
                    episodes=(1,),
                    embedded_provider_identity=identity,
                ),
                show=_generic_show(),
                evidence=MatchEvidence(
                    method="fixture-search",
                    confidence=1.0,
                    candidates=(
                        CandidateEvidence(
                            provider_identity=identity,
                            title="Fixture Harbor",
                            score=1.0,
                        ),
                    ),
                ),
                destination=(
                    "Fixture Harbor (2026)/Season 01/"
                    "Fixture Harbor (2026) S01E01 - Arrival.mkv"
                ),
            ),
        ),
    )

    manifest = plan_to_manifest(plan)
    record = manifest["records"][0]

    assert record["show"]["provider_identity"] == {
        "provider": "fixture",
        "value": "show-17",
    }
    assert "tvmaze_id" not in record["show"]
    assert record["evidence"]["candidates"][0]["provider_identity"] == {
        "provider": "fixture",
        "value": "show-17",
    }
    assert record["parse"]["embedded_provider_identity"] == {
        "provider": "fixture",
        "value": "show-17",
    }


def test_checked_in_schema_uses_jmo_namespaced_provider_identity() -> None:
    schema = load_plan_schema()

    assert schema["$id"].startswith("urn:jmo:")
    assert "providerIdentity" in schema["$defs"]
    assert "provider_identity" in schema["$defs"]["show"]["required"]
    assert "tvmaze_id" not in schema["$defs"]["show"]["properties"]
