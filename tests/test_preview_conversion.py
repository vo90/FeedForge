from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest
import soundfile as sf

from feedback_converter import converter


def _bank_with_wem_id(wem_id: int) -> bytes:
    return b"BKHD" + struct.pack("<I", wem_id) + b"HIRC"


def test_song_group_keeps_main_and_preview_bank_audio_separate() -> None:
    main_wem_id = 672419915
    preview_wem_id = 325345864
    manifest_path = "manifests/songs/focuhocu_lead.json"
    chart_path = "songs/bin/generic/focuhocu_lead.sng"
    main_wem_path = f"audio/windows/{main_wem_id}.wem"
    preview_wem_path = f"audio/windows/{preview_wem_id}.wem"
    content = {
        manifest_path: json.dumps({"SongKey": "focuhocu", "SongName": "Hocus Pocus"}).encode(),
        chart_path: b"chart",
        main_wem_path: b"main audio",
        preview_wem_path: b"preview audio",
        "audio/windows/song_focuhocu.bnk": _bank_with_wem_id(main_wem_id),
        "audio/windows/song_focuhocu_preview.bnk": _bank_with_wem_id(preview_wem_id),
    }

    selected = converter._content_for_song_group(
        content,
        "focuhocu",
        {manifest_path, chart_path},
    )

    marked_preview = f"{converter.INTERNAL_PREVIEW_AUDIO_PREFIX}{preview_wem_path}"
    assert selected[main_wem_path] == b"main audio"
    assert selected[marked_preview] == b"preview audio"
    assert preview_wem_path not in selected
    assert not converter._is_preview_audio_path(main_wem_path)
    assert converter._is_preview_audio_path(marked_preview)
    assert converter._select_primary_audio(
        [(marked_preview, b"preview" * 100), (main_wem_path, b"main")]
    )[0] == main_wem_path


def test_browser_preview_prefers_and_decodes_dedicated_wem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_bytes = b"dedicated preview wem"
    content = {
        f"{converter.INTERNAL_PREVIEW_AUDIO_PREFIX}audio/windows/123.wem": preview_bytes,
    }
    warnings: list[converter.ConversionWarning] = []

    def fake_convert(data: bytes, output_path: Path, **_kwargs: object) -> bool:
        assert data == preview_bytes
        output_path.write_bytes(b"OggS" + b"preview" * 200)
        return True

    monkeypatch.setattr(converter, "_convert_wem_bytes_to_ogg", fake_convert)
    preview_path = converter._copy_browser_preview(
        content,
        tmp_path,
        [{"id": "full", "file": "stems/full.ogg", "default": True}],
        warnings,
    )

    assert preview_path == "preview.ogg"
    assert (tmp_path / preview_path).read_bytes().startswith(b"OggS")
    assert warnings == []


def test_browser_preview_falls_back_to_thirty_seconds_of_full_mix(tmp_path: Path) -> None:
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    full_mix = stems_dir / "full.wav"
    sample_rate = 8000
    duration_seconds = 40
    with wave.open(str(full_mix), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * sample_rate * duration_seconds)

    warnings: list[converter.ConversionWarning] = []
    preview_path = converter._copy_browser_preview(
        {},
        tmp_path,
        [{"id": "full", "file": "stems/full.wav", "default": True}],
        warnings,
    )

    assert preview_path == "preview.ogg"
    with sf.SoundFile(tmp_path / preview_path) as preview:
        assert preview.format == "OGG"
        assert preview.subtype == "VORBIS"
        assert preview.frames / preview.samplerate == 30.0
    assert warnings == []


def test_preview_decode_error_falls_back_without_failing_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = {
        f"{converter.INTERNAL_PREVIEW_AUDIO_PREFIX}audio/windows/123.wem": b"preview",
    }
    warnings: list[converter.ConversionWarning] = []

    def fail_decode(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError("decoder unavailable")

    def fake_fallback(_source: Path, target: Path) -> bool:
        target.write_bytes(b"OggS" + b"fallback" * 200)
        return True

    monkeypatch.setattr(converter, "_convert_wem_bytes_to_ogg", fail_decode)
    monkeypatch.setattr(converter, "_write_preview_from_full_mix", fake_fallback)

    preview_path = converter._copy_browser_preview(
        content,
        tmp_path,
        [{"id": "full", "file": "stems/full.ogg", "default": True}],
        warnings,
    )

    assert preview_path == "preview.ogg"
    assert warnings == []


def test_missing_preview_is_a_warning_not_a_conversion_failure(tmp_path: Path) -> None:
    warnings: list[converter.ConversionWarning] = []

    preview_path = converter._copy_browser_preview(
        {},
        tmp_path,
        [{"id": "full", "file": "stems/missing.ogg", "default": True}],
        warnings,
    )

    assert preview_path is None
    assert len(warnings) == 1
    assert "song remains playable" in warnings[0].message


def test_preview_fallback_keeps_working_after_stem_separation(tmp_path: Path) -> None:
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    full_mix = stems_dir / "full.wav"
    sample_rate = 8000
    with wave.open(str(full_mix), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * sample_rate * 2)
    warnings: list[converter.ConversionWarning] = []

    preview_path = converter._copy_browser_preview(
        {},
        tmp_path,
        [
            {"id": "full", "file": "stems/full.wav", "codec": "wav", "default": False},
            {"id": "guitar", "file": "stems/guitar.ogg", "codec": "vorbis", "default": False},
        ],
        warnings,
    )

    assert preview_path == "preview.ogg"
    assert (tmp_path / preview_path).is_file()
    assert warnings == []
