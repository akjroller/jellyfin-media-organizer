from pathlib import Path

import pytest

from jellyfin_show_organizer.summary_io import read_summary, summary_int


def test_read_summary_preserves_values_after_first_equals(tmp_path: Path) -> None:
    path = tmp_path / "summary.txt"
    path.write_text(
        "records=12\nprovider=local=offline\nignored line\n", encoding="utf-8"
    )

    values = read_summary(path)

    assert values == {"records": "12", "provider": "local=offline"}


def test_summary_int_uses_default_and_reports_invalid_values() -> None:
    assert summary_int({}, "missing") == 0
    assert summary_int({}, "missing", default=3) == 3

    with pytest.raises(ValueError, match="audit summary has invalid records"):
        summary_int({"records": "not-a-number"}, "records")
