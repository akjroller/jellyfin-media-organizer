from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import TerminalStatus
from jellyfin_show_organizer.review_contract import (
    compile_active_overrides,
    load_review_contract_payload,
)
from jellyfin_show_organizer.review_execution import (
    PlanningConfig,
    PlanningConfigurationError,
    execute_plan,
)
from jellyfin_show_organizer.review_session import (
    REVIEW_SESSION_SCHEMA_VERSION,
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    ReviewSessionItem,
    render_review_session,
)

pytestmark = pytest.mark.local

BASE = (
    b"schema_version = 4\n\n"
    b"[[source_holds]]\n"
    b'source = "Fabricated Series/Held.mkv"\n'
    b'reasons = ["fabricated reviewed hold"]\n'
)


def _reject_network(
    _url: str,
    _params: Mapping[str, str] | None = None,
) -> object:
    raise AssertionError("fabricated reviewed-plan test attempted network access")


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    video = shows / "Fabricated Series" / "Held.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fabricated-video")
    destination.mkdir()
    return shows, destination


def _session(
    *,
    plan_sha256: str = "a" * 64,
    base_payload: bytes = BASE,
    base_snapshot: str | None = None,
    pending: bool = False,
) -> ReviewSession:
    if base_snapshot is None:
        base_snapshot = load_review_contract_payload(BASE).snapshot_id
    items = ()
    if pending:
        items = (
            ReviewSessionItem(
                review_ref="held-0123456789abcdef",
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.PENDING,
                show_key="Fabricated Series",
                source="Fabricated Series/Held.mkv",
                source_binding_sha256="b" * 64,
            ),
        )
    return ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256=plan_sha256,
        base_override_snapshot=base_snapshot,
        base_override_toml=base_payload.decode("utf-8"),
        items=items,
    )


def _write_review_state(
    tmp_path: Path,
    session: ReviewSession,
) -> tuple[Path, Path]:
    session_path = tmp_path / "review-session.json"
    override_path = tmp_path / "reviewed-overrides.toml"
    session_path.write_bytes(render_review_session(session))
    override_path.write_bytes(compile_active_overrides(session))
    return session_path, override_path


def _config(
    tmp_path: Path,
    shows: Path,
    destination: Path,
    overrides: Path | None,
    *,
    output_name: str,
) -> PlanningConfig:
    return PlanningConfig(
        shows_root=shows,
        destination_root=destination,
        output_dir=tmp_path / output_name,
        cache_dir=tmp_path / f"cache-{output_name}",
        overrides_path=overrides,
    )


def test_schema5_reviewed_held_plan_executes_without_network(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    session = _session()
    session_path, override_path = _write_review_state(tmp_path, session)

    outcome = execute_plan(
        _config(
            tmp_path,
            shows,
            destination,
            override_path,
            output_name="reviewed-audit",
        ),
        _reject_network,
        review_session_path=session_path,
    )

    assert [record.status for record in outcome.plan.records] == [TerminalStatus.HELD]
    assert not outcome.plan.companions
    assert outcome.preflight.ready
    assert not outcome.provider_failure
    provenance = json.loads(
        (tmp_path / "reviewed-audit" / "run-provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert provenance["review"]["session_sha256"] == session.sha256
    assert provenance["review"]["scope_state"] == "complete"
    assert provenance["review"]["movement_authorized"] is False


def test_schema5_review_requires_session_path(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    session = _session()
    _, override_path = _write_review_state(tmp_path, session)

    with pytest.raises(
        PlanningConfigurationError,
        match="schema-5 reviewed overrides require --review-session",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                override_path,
                output_name="missing-session-arg",
            ),
            _reject_network,
        )


def test_schema5_review_rejects_missing_session_file(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    session = _session()
    _, override_path = _write_review_state(tmp_path, session)

    with pytest.raises(
        PlanningConfigurationError,
        match="review session file does not exist",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                override_path,
                output_name="missing-session-file",
            ),
            _reject_network,
            review_session_path=tmp_path / "does-not-exist.json",
        )


def test_legacy_overrides_reject_review_session_argument(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    legacy = tmp_path / "legacy.toml"
    legacy.write_bytes(BASE)
    session_path = tmp_path / "unused-session.json"
    session_path.write_bytes(render_review_session(_session()))

    with pytest.raises(
        PlanningConfigurationError,
        match="--review-session is only valid with schema-5 reviewed overrides",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                legacy,
                output_name="legacy-session",
            ),
            _reject_network,
            review_session_path=session_path,
        )


def test_schema5_review_rejects_different_session_hash(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    original = _session()
    _, override_path = _write_review_state(tmp_path, original)
    different = _session(plan_sha256="c" * 64)
    different_path = tmp_path / "different-session.json"
    different_path.write_bytes(render_review_session(different))

    with pytest.raises(
        PlanningConfigurationError,
        match="does not match the supplied session hash",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                override_path,
                output_name="wrong-session-hash",
            ),
            _reject_network,
            review_session_path=different_path,
        )


def test_schema5_review_rejects_unresolved_session(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    pending = _session(pending=True)
    session_path, override_path = _write_review_state(tmp_path, pending)

    with pytest.raises(
        PlanningConfigurationError,
        match="review session still has unresolved work",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                override_path,
                output_name="pending-session",
            ),
            _reject_network,
            review_session_path=session_path,
        )


def test_schema5_review_rejects_stale_base_override_payload(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    original_snapshot = load_review_contract_payload(BASE).snapshot_id
    stale = _session(
        base_payload=(
            b"schema_version = 4\n\n"
            b"[[source_holds]]\n"
            b'source = "Other/Held.mkv"\n'
            b'reasons = ["fabricated"]\n'
        ),
        base_snapshot=original_snapshot,
    )
    session_path, override_path = _write_review_state(tmp_path, stale)

    with pytest.raises(
        PlanningConfigurationError,
        match="base override payload no longer matches its snapshot",
    ):
        execute_plan(
            _config(
                tmp_path,
                shows,
                destination,
                override_path,
                output_name="stale-base-payload",
            ),
            _reject_network,
            review_session_path=session_path,
        )


def test_reviewed_plan_rejects_existing_or_uncreatable_output(tmp_path: Path) -> None:
    shows, destination = _roots(tmp_path)
    existing = tmp_path / "existing-output"
    existing.mkdir()

    with pytest.raises(PlanningConfigurationError, match="output directory already exists"):
        execute_plan(
            PlanningConfig(
                shows_root=shows,
                destination_root=destination,
                output_dir=existing,
                cache_dir=tmp_path / "existing-output-cache",
            ),
            _reject_network,
        )

    with pytest.raises(
        PlanningConfigurationError,
        match="output directory parent does not exist",
    ):
        execute_plan(
            PlanningConfig(
                shows_root=shows,
                destination_root=destination,
                output_dir=tmp_path / "missing-parent" / "audit",
                cache_dir=tmp_path / "missing-parent-cache",
            ),
            _reject_network,
        )
