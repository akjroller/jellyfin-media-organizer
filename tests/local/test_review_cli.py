from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from jellyfin_show_organizer.cli import REVIEW_INCOMPLETE_EXIT, main
from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.review_contract import (
    ReviewContractCatalog,
    load_review_contract,
)
from jellyfin_show_organizer.review_session import (
    ReviewItemKind,
    ReviewItemState,
    build_review_session,
    load_review_session,
)
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local


class _TTY:
    def isatty(self) -> bool:
        return True


def _duplicate_records(show_name: str, show_id: int) -> tuple[PlanRecord, PlanRecord]:
    destination = f"{show_name}/Season 01/{show_name} S01E01.mkv"
    candidates = []
    for name in ("A", "B"):
        source = f"{show_name}/{name}.mkv"
        candidates.append(
            DuplicateCandidate(
                operation_key=source,
                members=(source,),
                destination=destination,
                logical_identity=f"tvmaze:{show_id}:episode:1",
                fingerprint=SourceFingerprint(
                    size=100,
                    mtime_ns=200,
                    sha256="a" * 64,
                ),
            )
        )
    result = classify_duplicate_candidates(candidates)[0]
    decision = result.decision
    parse = ParseResult(series_hint=show_name, season=1, episodes=(1,))
    show = CanonicalShow(
        source_key=show_name,
        tvmaze_id=show_id,
        title=show_name,
        numbering_mode=NumberingMode.AIRED,
    )
    evidence = MatchEvidence(method="fabricated-review-cli", confidence=1.0)
    records = []
    for candidate in candidates:
        winner = candidate.operation_key == decision.winner
        records.append(
            PlanRecord(
                source=SourceFile(
                    relative_path=candidate.operation_key,
                    extension=".mkv",
                    fingerprint=candidate.fingerprint,
                ),
                status=TerminalStatus.MATCHED if winner else TerminalStatus.DUPLICATE,
                parse=parse if winner else None,
                show=show if winner else None,
                evidence=evidence if winner else None,
                destination=destination if winner else None,
                duplicate=decision,
            )
        )
    return records[0], records[1]


def _review_fixture(
    tmp_path: Path,
    *,
    shows: tuple[tuple[str, int], ...] = (("Fabricated Series", 4242),),
) -> tuple[Path, Path, Path, bytes, dict[str, object]]:
    base = tmp_path / "base.toml"
    base_payload = b"schema_version = 4\n"
    base.write_bytes(base_payload)
    base_catalog = load_review_contract(base)
    records = tuple(
        record
        for show_name, show_id in shows
        for record in _duplicate_records(show_name, show_id)
    )
    plan = OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=records,
        provenance=PlanProvenance(
            tool_version="0.1.0",
            config_snapshot_id="1" * 64,
            overrides_snapshot_id=base_catalog.snapshot_id,
        ),
    )
    manifest = plan_to_manifest(plan)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(manifest), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    return plan_path, base, cache, base_payload, manifest


def _review_args(
    plan: Path,
    base: Path,
    cache: Path,
    output: Path,
    session: Path,
    *extra: str,
) -> list[str]:
    return [
        "review",
        str(plan),
        "--overrides",
        str(base),
        "--output",
        str(output),
        "--session",
        str(session),
        "--cache-dir",
        str(cache),
        "--offline",
        *extra,
    ]


def _interactive(
    monkeypatch: pytest.MonkeyPatch,
    responses: tuple[str, ...],
) -> None:
    iterator = iter(responses)
    monkeypatch.setattr("jellyfin_show_organizer.cli.sys.stdin", _TTY())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(iterator))


def test_review_cli_complete_interactive_duplicate_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, base, cache, _, _ = _review_fixture(tmp_path)
    output = tmp_path / "active.toml"
    session_path = tmp_path / "session.json"
    _interactive(monkeypatch, ("a",))

    exit_code = main(_review_args(plan, base, cache, output, session_path))

    assert exit_code == 0
    session = load_review_session(session_path.read_bytes())
    assert session.complete
    assert session.items[0].state is ReviewItemState.ANSWERED
    assert session.items[0].action == "select_winner"
    active = load_review_contract(output)
    assert isinstance(active, ReviewContractCatalog)
    assert active.review_session_sha256 == session.sha256
    rendered = capsys.readouterr().out
    assert "Review complete:" in rendered
    assert str(tmp_path) not in rendered


def test_review_cli_incomplete_returns_distinct_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, base, cache, _, _ = _review_fixture(tmp_path)
    output = tmp_path / "partial.toml"
    session_path = tmp_path / "partial-session.json"
    _interactive(monkeypatch, ("d",))

    exit_code = main(_review_args(plan, base, cache, output, session_path))

    assert exit_code == REVIEW_INCOMPLETE_EXIT
    session = load_review_session(session_path.read_bytes())
    assert session.items[0].state is ReviewItemState.DEFERRED
    assert not session.usable_for_planning


def test_review_cli_interruption_leaves_resumable_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, base, cache, _, _ = _review_fixture(tmp_path)
    output = tmp_path / "active.toml"
    session_path = tmp_path / "session.json"
    monkeypatch.setattr("jellyfin_show_organizer.cli.sys.stdin", _TTY())

    def interrupt(_prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)
    first_exit = main(_review_args(plan, base, cache, output, session_path))

    assert first_exit == 130
    assert session_path.is_file()
    assert not output.exists()
    assert load_review_session(session_path.read_bytes()).items[0].state is (
        ReviewItemState.PENDING
    )

    _interactive(monkeypatch, ("a",))
    resumed_exit = main(
        _review_args(plan, base, cache, output, session_path, "--resume")
    )

    assert resumed_exit == 0
    assert load_review_session(session_path.read_bytes()).complete
    assert output.is_file()


def test_review_cli_answer_file_is_bound_to_plan_session_and_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, base, cache, base_payload, manifest = _review_fixture(tmp_path)
    base_catalog = load_review_contract(base)
    starting_session = build_review_session(
        manifest,
        base_override_snapshot=base_catalog.snapshot_id,
        base_override_payload=base_payload,
    )
    item = starting_session.items[0]
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "plan_sha256": starting_session.plan_sha256,
                "base_override_snapshot": starting_session.base_override_snapshot,
                "session_sha256": starting_session.sha256,
                "answers": [
                    {
                        "review_ref": item.review_ref,
                        "action": "accept_recommended",
                        "expected_identity_sha256": item.identity_sha256,
                        "winner": None,
                        "responses": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "active.toml"
    session_path = tmp_path / "session.json"
    monkeypatch.setattr("jellyfin_show_organizer.cli.sys.stdin", object())

    exit_code = main(
        _review_args(
            plan,
            base,
            cache,
            output,
            session_path,
            "--answers",
            str(answers),
        )
    )

    assert exit_code == 0
    assert load_review_session(session_path.read_bytes()).complete


def test_review_cli_refuses_output_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, base, cache, _, _ = _review_fixture(tmp_path)
    output = tmp_path / "active.toml"
    session_path = tmp_path / "session.json"
    output.write_text("do-not-replace\n", encoding="utf-8")
    _interactive(monkeypatch, ("a",))

    exit_code = main(_review_args(plan, base, cache, output, session_path))

    assert exit_code == 2
    assert output.read_text(encoding="utf-8") == "do-not-replace\n"
    assert "failed safely" in capsys.readouterr().err.casefold()


def test_review_cli_approved_partial_requires_and_records_selected_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, base, cache, _, _ = _review_fixture(
        tmp_path,
        shows=(("Fabricated One", 4242), ("Fabricated Two", 4343)),
    )
    output = tmp_path / "partial.toml"
    session_path = tmp_path / "partial-session.json"
    _interactive(monkeypatch, ("a",))

    exit_code = main(
        _review_args(
            plan,
            base,
            cache,
            output,
            session_path,
            "--show",
            "Fabricated One",
            "--approve-partial",
        )
    )

    assert exit_code == 0
    session = load_review_session(session_path.read_bytes())
    assert session.approved_partial
    assert len(session.approved_scope_refs) == 1
    approved = session.item(session.approved_scope_refs[0])
    assert approved.kind is ReviewItemKind.DUPLICATE
    assert approved.show_key == "Fabricated One"
    assert approved.state is ReviewItemState.ANSWERED
    assert any(item.state is ReviewItemState.PENDING for item in session.items)
