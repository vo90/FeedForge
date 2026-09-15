from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from feedback_converter.cli import (
    _conversion_result_payload,
    _print_conversion_results,
    build_parser,
)


def test_cli_validation_policy_is_strict_by_default_and_accepts_safe() -> None:
    parser = build_parser()

    assert parser.parse_args(["song.psarc"]).validation_policy == "strict"
    assert parser.parse_args(["song.psarc", "--validation-policy", "safe"]).validation_policy == "safe"


def test_conversion_result_protocol_distinguishes_publishable_chart_warnings() -> None:
    result = SimpleNamespace(
        output_path=Path("song.feedpak"),
        validation=SimpleNamespace(
            ok=False,
            converted_with_chart_warnings=True,
            chart_warning_count=2,
            chart_warnings=["arrangement compatibility warning: fret 127 was preserved"],
        ),
        conversion_details=[
            SimpleNamespace(
                message="chart data was preserved",
                category="chart-validation",
            )
        ],
        warnings=[],
    )

    payload = _conversion_result_payload(result)

    assert payload == {
        "outputPath": "song.feedpak",
        "validationOk": False,
        "publishable": True,
        "convertedWithChartWarnings": True,
        "chartWarningCount": 2,
        "chartWarnings": ["arrangement compatibility warning: fret 127 was preserved"],
        "conversionDetails": [
            {
                "message": "chart data was preserved",
                "category": "chart-validation",
            }
        ],
        "warnings": [],
    }


def test_conversion_details_use_a_non_warning_log_prefix(capsys) -> None:
    result = SimpleNamespace(
        output_path=Path("song.feedpak"),
        validation=None,
        conversion_details=[
            SimpleNamespace(
                message="bend timing normalized to the note duration",
                category="source-normalization",
            )
        ],
        warnings=[],
    )

    _print_conversion_results([result])

    captured = capsys.readouterr()
    assert (
        "detail [source-normalization]: bend timing normalized to the note duration"
        in captured.err
    )
    assert "warning:" not in captured.err.lower()
