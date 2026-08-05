from __future__ import annotations

import io
import json
import random
import threading
import time
from pathlib import Path

import pytest

from feedback_converter import batch, cli
from feedback_converter.converter import PsarcPlanningData
from feedback_converter.psarc_format.psarc import PSARC


class _CountingBytesIO(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        value = super().read(size)
        self.bytes_read += len(value)
        return value


def _planning_data(path: Path, artist: str = "ABBA", title: str = "Angel Eyes") -> PsarcPlanningData:
    stat = path.stat()
    return PsarcPlanningData(
        input_path=path,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        songs=[
            (
                "song",
                {
                    "artist": artist,
                    "title": title,
                    "album": "Voulez-Vous",
                    "year": 1979,
                    "arrangement_names": {"song_lead": "Lead"},
                },
            )
        ],
    )


def test_metadata_reader_skips_large_audio_and_chart_payloads() -> None:
    random_source = random.Random(42)
    audio = random_source.randbytes(2 * 1024 * 1024)
    chart = random_source.randbytes(512 * 1024)
    metadata = json.dumps({"ArtistName": "ABBA", "SongName": "Angel Eyes"}).encode()
    archive = PSARC(crypto=False).build(
        {
            "audio/windows/song.wem": audio,
            "manifests/songs_dlc_song/song.json": metadata,
            "songs/bin/generic/song_lead.sng": chart,
        }
    )
    stream = _CountingBytesIO(archive)

    selected = PSARC(crypto=False).parse_metadata_stream(stream)

    assert selected["manifests/songs_dlc_song/song.json"] == metadata
    assert selected["songs/bin/generic/song_lead.sng"] == b""
    assert "audio/windows/song.wem" not in selected
    assert stream.bytes_read < len(archive) // 20

    preview_stream = _CountingBytesIO(archive)
    preview = PSARC(crypto=False).parse_preview_stream(preview_stream)
    assert preview["songs/bin/generic/song_lead.sng"] == chart
    assert "audio/windows/song.wem" not in preview
    assert preview_stream.bytes_read < len(archive) // 3


def test_parallel_metadata_reads_keep_collision_names_in_queue_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = [tmp_path / f"song-{index}.psarc" for index in range(4)]
    for path in inputs:
        path.write_bytes(path.name.encode())

    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def fake_load(path: Path, **_kwargs: object) -> PsarcPlanningData:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.04 * (len(inputs) - inputs.index(Path(path))))
            return _planning_data(Path(path), artist="Same Artist", title="Same Song")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(batch, "load_psarc_planning_data", fake_load)
    result = batch.plan_conversion_request(
        {
            "items": [{"inputPath": str(path)} for path in inputs],
            "outputDir": str(tmp_path / "out"),
            "nameTemplate": "{artist} - {title}",
            "overwrite": True,
            "workers": 4,
        }
    )

    assert maximum_active > 1
    assert [Path(item["outputs"][0]["path"]).name for item in result["items"]] == [
        "Same Artist - Same Song.feedpak",
        "Same Artist - Same Song (2).feedpak",
        "Same Artist - Same Song (3).feedpak",
        "Same Artist - Same Song (4).feedpak",
    ]


def test_planning_cache_reuses_unchanged_metadata_and_reports_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"first")
    calls = 0

    def fake_load(path: Path, **_kwargs: object) -> PsarcPlanningData:
        nonlocal calls
        calls += 1
        return _planning_data(Path(path))

    monkeypatch.setattr(batch, "load_psarc_planning_data", fake_load)
    request = {
        "items": [{"inputPath": str(input_path)}],
        "outputDir": str(tmp_path / "out"),
        "nameTemplate": "{artist} - {title}",
        "cachePath": str(tmp_path / "cache" / "planning.sqlite3"),
        "workers": 2,
    }
    progress: list[dict[str, object]] = []

    first = batch.plan_conversion_request(request, progress_callback=progress.append)
    second = batch.plan_conversion_request(request)
    input_path.write_bytes(b"changed and larger")
    third = batch.plan_conversion_request(request)

    assert first["cached"] == 0
    assert second["cached"] == 1
    assert third["cached"] == 0
    assert calls == 2
    assert progress[0]["stage"] == "metadata"
    assert progress[-1]["stage"] == "complete"
    assert progress[-1]["completed"] == 1


def test_planning_cli_streams_machine_readable_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({"items": [{"inputPath": "song.psarc"}]}), encoding="utf-8")

    def fake_plan(
        _request: dict[str, object],
        *,
        progress_callback: object,
    ) -> dict[str, object]:
        assert callable(progress_callback)
        progress_callback({"stage": "metadata", "completed": 1, "total": 1})
        return {"ok": True, "total": 1, "planned": 1, "failed": 0, "items": []}

    monkeypatch.setattr(cli, "plan_conversion_request", fake_plan)
    assert cli.main(["--plan-conversion-file", str(request_path)]) == 0
    captured = capsys.readouterr()

    assert captured.err.startswith(cli.PLAN_PROGRESS_PREFIX)
    progress = json.loads(captured.err.removeprefix(cli.PLAN_PROGRESS_PREFIX))
    assert progress == {"stage": "metadata", "completed": 1, "total": 1}
    assert json.loads(captured.out)["ok"] is True
