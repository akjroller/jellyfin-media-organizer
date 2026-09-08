from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import cast

import pytest

from jellyfin_show_organizer.models import CompanionStatus, ProviderIdentity, TerminalStatus
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    PlanningConfigurationError,
    execute_plan as execute_base_plan,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.review_contract import (
    ReviewContractCatalog,
    load_review_contract,
    load_review_contract_payload,
    verify_review_contract_session,
)
from jellyfin_show_organizer.review_execution import execute_plan as execute_reviewed_plan
from jellyfin_show_organizer.review_session import (
    ReviewItemState,
    build_review_session,
    load_review_session,
)
from jellyfin_show_organizer.review_system import (
    ReviewAnswer,
    ReviewAnswerBundle,
    ReviewConfigurationError,
    run_review_system,
)
from jellyfin_show_organizer.tvmaze_cache import JsonGetter

pytestmark = pytest.mark.local

SHOW_ID = 4242
EPISODE_ID = 9001
SPECIAL_ID = 9000
SHOW_NAME = "Fabricated Series"
EPISODE_TITLE = "Pilot"
EPISODE_AIRDATE = "2024-01-01"
SOURCE_EPISODE = f"{SHOW_NAME}/{SHOW_NAME} S01E01.mkv"
SOURCE_SPECIAL = f"{SHOW_NAME}/{SHOW_NAME} OVA01.mkv"
SOURCE_EXTRA = f"{SHOW_NAME}/Extras/Mystery Feature.mkv"


def _episode_payload(
    *,
    episode_id: int = EPISODE_ID,
    title: str = EPISODE_TITLE,
    airdate: str = EPISODE_AIRDATE,
) -> list[dict[str, object]]:
    return [
        {
            "id": SPECIAL_ID,
            "season": 0,
            "number": 1,
            "name": "Launch Special",
            "airdate": "2023-12-15",
            "type": "special",
        },
        {
            "id": episode_id,
            "season": 1,
            "number": 1,
            "name": title,
            "airdate": airdate,
            "type": "regular",
        },
    ]


class HttpFixture:
    def __init__(
        self,
        *,
        episode_id: int = EPISODE_ID,
        title: str = EPISODE_TITLE,
        airdate: str = EPISODE_AIRDATE,
    ) -> None:
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []
        self.episode_id = episode_id
        self.title = title
        self.airdate = airdate

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        if "search/shows" in url:
            return [
                {
                    "show": {
                        "id": SHOW_ID,
                        "name": SHOW_NAME,
                        "premiered": "2024-01-01",
                    }
                }
            ]
        if f"/shows/{SHOW_ID}/episodes" in url:
            return _episode_payload(
                episode_id=self.episode_id,
                title=self.title,
                airdate=self.airdate,
            )
        raise AssertionError(f"unexpected fabricated provider request: {url}")


class ReviewProvider:
    def __init__(
        self,
        *,
        search_resolved: bool = True,
        catalog_resolved: bool = True,
        catalog_errors: tuple[str, ...] = (),
    ) -> None:
        self.search_resolved = search_resolved
        self.catalog_resolved = catalog_resolved
        self.catalog_errors = catalog_errors
        self.show = ProviderShow(
            identity=ProviderIdentity.tvmaze(SHOW_ID),
            title=SHOW_NAME,
            year=2024,
        )
        self.episodes = (
            ProviderEpisode(
                tvmaze_episode_id=SPECIAL_ID,
                season=0,
                number=1,
                title="Launch Special",
                airdate="2023-12-15",
                episode_type="special",
            ),
            ProviderEpisode(
                tvmaze_episode_id=EPISODE_ID,
                season=1,
                number=1,
                title=EPISODE_TITLE,
                airdate=EPISODE_AIRDATE,
                episode_type="regular",
            ),
        )

    @property
    def provider_name(self) -> str:
        return "tvmaze"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        if not self.search_resolved:
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key=f"search:{title.casefold()}",
                cache_snapshot_id="1" * 64,
                shows=(),
                unresolved_reason="fabricated provider search unavailable",
            )
        return ProviderSearchSnapshot(
            provider="tvmaze",
            request_key=f"search:{title.casefold()}",
            cache_snapshot_id="1" * 64,
            shows=(self.show,),
        )

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        if not self.catalog_resolved:
            return ProviderEpisodeCatalog(
                provider="tvmaze",
                request_key=f"show:{show_identity.value}",
                cache_snapshot_id="2" * 64,
                show_identity=show_identity,
                episodes=(),
                unresolved_reason="fabricated provider catalog unavailable",
            )
        return ProviderEpisodeCatalog(
            provider="tvmaze",
            request_key=f"show:{show_identity.value}",
            cache_snapshot_id="2" * 64,
            show_identity=show_identity,
            episodes=self.episodes,
            errors=self.catalog_errors,
        )


def _write_library(tmp_path: Path, source: str) -> tuple[Path, Path, Path | None]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir()
    video = shows / Path(source)
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fabricated-video")
    subtitle: Path | None = None
    if source == SOURCE_EPISODE:
        subtitle = video.with_suffix(".en.srt")
        subtitle.write_bytes(b"fabricated-subtitle")
    return shows, destination, subtitle


def _base_override(tmp_path: Path, source: str) -> tuple[Path, bytes]:
    path = tmp_path / "base-overrides.toml"
    payload = (
        "schema_version = 4\n\n"
        "[[source_holds]]\n"
        f"source = {json.dumps(source)}\n"
        'reasons = ["fabricated source requires manual review"]\n'
    ).encode()
    path.write_bytes(payload)
    return path, payload


def _prepare_review(
    tmp_path: Path,
    source: str,
) -> tuple[
    Path,
    Path,
    Path,
    bytes,
    dict[str, object],
    str,
]:
    shows, destination, _ = _write_library(tmp_path, source)
    base_path, base_payload = _base_override(tmp_path, source)

    def reject_network(
        _url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        raise AssertionError("source-held base planning attempted provider access")

    base = execute_base_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "base-audit",
            cache_dir=tmp_path / "base-cache",
            overrides_path=base_path,
        ),
        cast(JsonGetter, reject_network),
    )
    assert [record.status for record in base.plan.records] == [TerminalStatus.HELD]
    if source == SOURCE_EPISODE:
        assert [item.status for item in base.plan.companions] == [CompanionStatus.IGNORED]
    manifest = cast(dict[str, object], json.loads(base.bundle.plan_json))
    base_catalog = load_review_contract(base_path)
    return shows, destination, base_path, base_payload, manifest, base_catalog.snapshot_id


def _answer_bundle(
    manifest: dict[str, object],
    base_payload: bytes,
    base_snapshot: str,
    *,
    action: str,
    responses: tuple[str, ...],
) -> ReviewAnswerBundle:
    starting = build_review_session(
        manifest,
        base_override_snapshot=base_snapshot,
        base_override_payload=base_payload,
    )
    assert len(starting.items) == 1
    item = starting.items[0]
    return ReviewAnswerBundle(
        plan_sha256=starting.plan_sha256,
        base_override_snapshot=starting.base_override_snapshot,
        session_sha256=starting.sha256,
        answers=(
            ReviewAnswer(
                review_ref=item.review_ref,
                action=action,
                expected_identity_sha256=item.identity_sha256,
                responses=responses,
            ),
        ),
    )


def _run_answered_review(
    tmp_path: Path,
    source: str,
    *,
    action: str,
    responses: tuple[str, ...],
    provider: ReviewProvider | None = None,
) -> tuple[Path, Path, Path, ReviewContractCatalog, object, StringIO]:
    shows, destination, _, base_payload, manifest, base_snapshot = _prepare_review(
        tmp_path, source
    )
    answers = _answer_bundle(
        manifest,
        base_payload,
        base_snapshot,
        action=action,
        responses=responses,
    )
    session_path = tmp_path / "review-session.json"
    active_path = tmp_path / "active-overrides.toml"
    output = StringIO()
    session, active = run_review_system(
        manifest,
        base_payload,
        base_override_snapshot=base_snapshot,
        provider=provider or ReviewProvider(),
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=lambda _prompt: pytest.fail("answer-bound review requested live input"),
        output=output,
        answers=answers,
    )
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    assert active_path.read_bytes() == active
    return shows, destination, session_path, catalog, session, output


def test_held_to_provider_episode_carries_jellyfin_ids_and_replays_offline(
    tmp_path: Path,
) -> None:
    shows, destination, session_path, catalog, session, output = _run_answered_review(
        tmp_path,
        SOURCE_EPISODE,
        action="episode",
        responses=("", "1", "1", "", "", "y", "111", "222", "tt333", "y"),
    )
    active_path = tmp_path / "active-overrides.toml"

    assert session.complete
    assert session.items[0].state is ReviewItemState.ANSWERED
    assert session.items[0].action == "episode"
    reviewed = catalog.reviewed_episode_for(SOURCE_EPISODE)
    assert reviewed is not None
    assert reviewed.episode_provider_identity == ProviderIdentity.tvmaze(EPISODE_ID)
    assert reviewed.lookup_mode == "coordinate"
    assert {item.value for item in catalog.jellyfin_identifiers_for(SHOW_NAME)} == {
        "111",
        "222",
        "tt333",
    }
    assert not catalog.source_holds
    verify_review_contract_session(catalog, cast(object, session))
    assert "Destination preview:" in output.getvalue()

    online_getter = HttpFixture()
    online = execute_reviewed_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "reviewed-online",
            cache_dir=tmp_path / "reviewed-cache",
            overrides_path=active_path,
        ),
        online_getter,
        review_session_path=session_path,
    )

    assert online.preflight.ready
    assert not online.provider_failure
    assert [record.status for record in online.plan.records] == [TerminalStatus.MATCHED]
    record = online.plan.records[0]
    assert record.provider_episodes[0].provider_identity == ProviderIdentity.tvmaze(
        EPISODE_ID
    )
    assert record.destination is not None
    for tag in ("[tvdbid-111]", "[tmdbid-222]", "[imdbid-tt333]"):
        assert tag in record.destination
    assert [item.status for item in online.plan.companions] == [
        CompanionStatus.ASSOCIATED
    ]
    provenance = json.loads(
        (tmp_path / "reviewed-online" / "run-provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert provenance["review"]["session_sha256"] == session.sha256
    assert provenance["review"]["scope_state"] == "complete"
    assert provenance["review"]["movement_authorized"] is False

    def reject_network(
        _url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        raise AssertionError("reviewed offline replay attempted provider access")

    offline = execute_reviewed_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "reviewed-offline",
            cache_dir=tmp_path / "reviewed-cache",
            overrides_path=active_path,
            offline=True,
        ),
        cast(JsonGetter, reject_network),
        review_session_path=session_path,
    )

    assert offline.preflight.ready
    assert offline.preflight.plan_hash == online.preflight.plan_hash
    assert offline.bundle.plan_json == online.bundle.plan_json
    assert offline.bundle.decision_sha256 == online.bundle.decision_sha256


def test_held_to_provider_confirmed_special_compiles_exact_special(
    tmp_path: Path,
) -> None:
    _, _, _, catalog, session, output = _run_answered_review(
        tmp_path,
        SOURCE_SPECIAL,
        action="special",
        responses=("", "1", "1", "n", "y"),
    )

    decision = catalog.reviewed_episode_for(SOURCE_SPECIAL)
    assert decision is not None
    assert decision.lookup_mode == "special"
    assert decision.episode_provider_identity == ProviderIdentity.tvmaze(SPECIAL_ID)
    assert (decision.season, decision.number, decision.title) == (0, 1, "Launch Special")
    assert session.items[0].action == "special"
    assert "Provider-confirmed specials:" in output.getvalue()


def test_held_to_explicit_extra_compiles_extra_and_provider_ids(tmp_path: Path) -> None:
    _, _, _, catalog, session, output = _run_answered_review(
        tmp_path,
        SOURCE_EXTRA,
        action="extra",
        responses=("", "1", "1", "Reviewed Trailer", "y", "111", "222", "tt333", "y"),
    )

    decision = catalog.extra_decision_for(SOURCE_EXTRA)
    assert decision is not None
    assert decision.kind == "trailer"
    assert decision.display_title == "Reviewed Trailer"
    assert session.items[0].action == "extra"
    assert {item.value for item in catalog.jellyfin_identifiers_for(SHOW_NAME)} == {
        "111",
        "222",
        "tt333",
    }
    assert "Destination preview:" in output.getvalue()


@pytest.mark.parametrize(
    ("source", "action", "responses"),
    [
        (SOURCE_EPISODE, "episode", ("", "1", "4")),
        (SOURCE_SPECIAL, "special", ("", "1", "cancel")),
        (SOURCE_EXTRA, "extra", ("", "1", "1", "", "n", "n")),
    ],
)
def test_cancelled_held_actions_are_saved_as_deferred(
    tmp_path: Path,
    source: str,
    action: str,
    responses: tuple[str, ...],
) -> None:
    _, _, session_path, catalog, session, _ = _run_answered_review(
        tmp_path,
        source,
        action=action,
        responses=responses,
    )

    assert not session.complete
    assert session.items[0].state is ReviewItemState.DEFERRED
    assert session.items[0].action == "defer"
    assert not session.usable_for_planning
    assert load_review_session(session_path.read_bytes()) == session
    with pytest.raises(ValueError, match="unresolved work"):
        verify_review_contract_session(catalog, cast(object, session))


def test_answer_prompt_exhaustion_fails_closed(tmp_path: Path) -> None:
    _, _, _, base_payload, manifest, base_snapshot = _prepare_review(
        tmp_path, SOURCE_EPISODE
    )
    answers = _answer_bundle(
        manifest,
        base_payload,
        base_snapshot,
        action="episode",
        responses=("",),
    )

    with pytest.raises(ReviewConfigurationError, match="ran out"):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=ReviewProvider(),
            session_path=tmp_path / "session.json",
            output_override_path=tmp_path / "active.toml",
            resume=False,
            input_fn=lambda _prompt: pytest.fail("unexpected live prompt"),
            output=StringIO(),
            answers=answers,
        )

    assert not (tmp_path / "active.toml").exists()


def test_unavailable_provider_search_defers_without_inventing_identity(tmp_path: Path) -> None:
    _, _, _, catalog, session, output = _run_answered_review(
        tmp_path,
        SOURCE_EPISODE,
        action="episode",
        responses=("",),
        provider=ReviewProvider(search_resolved=False),
    )

    assert session.items[0].state is ReviewItemState.DEFERRED
    assert catalog.reviewed_episode_for(SOURCE_EPISODE) is None
    assert "Provider search unavailable:" in output.getvalue()


@pytest.mark.parametrize(
    ("episode_id", "title", "airdate", "message"),
    [
        (9002, EPISODE_TITLE, EPISODE_AIRDATE, "identity is missing or ambiguous"),
        (EPISODE_ID, "Retitled Pilot", EPISODE_AIRDATE, "metadata changed"),
        (EPISODE_ID, EPISODE_TITLE, "2024-01-02", "metadata changed"),
    ],
)
def test_reviewed_episode_replanning_rejects_provider_drift(
    tmp_path: Path,
    episode_id: int,
    title: str,
    airdate: str,
    message: str,
) -> None:
    shows, destination, session_path, _, _, _ = _run_answered_review(
        tmp_path,
        SOURCE_EPISODE,
        action="episode",
        responses=("", "1", "1", "", "", "n", "y"),
    )

    with pytest.raises(PlanningConfigurationError, match=message):
        execute_reviewed_plan(
            PlanningConfig(
                shows_root=shows,
                destination_root=destination,
                output_dir=tmp_path / "drift-audit",
                cache_dir=tmp_path / "drift-cache",
                overrides_path=tmp_path / "active-overrides.toml",
            ),
            HttpFixture(episode_id=episode_id, title=title, airdate=airdate),
            review_session_path=session_path,
        )

    assert not (tmp_path / "drift-audit").exists()


@pytest.mark.parametrize("member", ["video", "companion"])
def test_reviewed_episode_replanning_rejects_source_or_companion_drift(
    tmp_path: Path,
    member: str,
) -> None:
    shows, destination, session_path, _, _, _ = _run_answered_review(
        tmp_path,
        SOURCE_EPISODE,
        action="episode",
        responses=("", "1", "1", "", "", "n", "y"),
    )
    video = shows / Path(SOURCE_EPISODE)
    subtitle = video.with_suffix(".en.srt")
    target = video if member == "video" else subtitle
    target.write_bytes(target.read_bytes() + b"-changed")

    with pytest.raises(
        PlanningConfigurationError,
        match="source fingerprint or companion set changed",
    ):
        execute_reviewed_plan(
            PlanningConfig(
                shows_root=shows,
                destination_root=destination,
                output_dir=tmp_path / f"{member}-drift-audit",
                cache_dir=tmp_path / f"{member}-drift-cache",
                overrides_path=tmp_path / "active-overrides.toml",
            ),
            HttpFixture(),
            review_session_path=session_path,
        )


def test_schema5_contract_rejects_wrong_review_session(tmp_path: Path) -> None:
    shows, destination, session_path, _, session, _ = _run_answered_review(
        tmp_path,
        SOURCE_EPISODE,
        action="episode",
        responses=("", "1", "1", "", "", "n", "y"),
    )
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["plan_sha256"] = "f" * 64
    stale_session = tmp_path / "stale-session.json"
    stale_session.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PlanningConfigurationError, match="session base plan"):
        execute_reviewed_plan(
            PlanningConfig(
                shows_root=shows,
                destination_root=destination,
                output_dir=tmp_path / "stale-session-audit",
                cache_dir=tmp_path / "stale-session-cache",
                overrides_path=tmp_path / "active-overrides.toml",
            ),
            HttpFixture(),
            review_session_path=stale_session,
        )

    assert session.complete
