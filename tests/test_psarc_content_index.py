import struct

from feedback_converter.psarc_content_index import (
    PsarcContentIndex,
    parse_wwise_bank_media_ids,
)


def _chunk(tag: bytes, payload: bytes = b"") -> bytes:
    return struct.pack("<4sI", tag, len(payload)) + payload


def _hirc_object(object_type: int, payload: bytes) -> bytes:
    return struct.pack("<BI", object_type, len(payload)) + payload


def _sound_object(media_id: int, *, object_id: int = 1) -> bytes:
    payload = bytearray(24)
    struct.pack_into("<I", payload, 0, object_id)
    struct.pack_into("<I", payload, 12, media_id)
    struct.pack_into("<I", payload, 16, media_id)
    return _hirc_object(2, bytes(payload))


def _hirc(*objects: bytes) -> bytes:
    return _chunk(b"HIRC", struct.pack("<I", len(objects)) + b"".join(objects))


def _bank_with_didx(media_id: int, data: bytes) -> bytes:
    didx = struct.pack("<III", media_id, 0, len(data))
    return b"".join(
        (
            _chunk(b"BKHD"),
            _chunk(b"DIDX", didx),
            _chunk(b"DATA", data),
            _hirc(_sound_object(media_id)),
        )
    )


def test_didx_reference_does_not_scan_false_wem_id_from_data() -> None:
    referenced_id = 360_148_530
    unrelated_full_track_id = 659_021_540
    data = b"compressed" + struct.pack("<I", unrelated_full_track_id) + b"audio"
    bank = _bank_with_didx(referenced_id, data)

    assert parse_wwise_bank_media_ids(
        bank,
        known_media_ids={referenced_id, unrelated_full_track_id},
    ) == {referenced_id}


def test_structured_hirc_source_is_used_when_bank_has_no_didx() -> None:
    referenced_id = 523_798_861
    unrelated_full_track_id = 768_367_085
    bank = b"".join(
        (
            _chunk(b"BKHD"),
            _chunk(b"DATA", struct.pack("<I", unrelated_full_track_id)),
            _hirc(_sound_object(referenced_id)),
        )
    )

    assert parse_wwise_bank_media_ids(
        bank,
        known_media_ids={referenced_id, unrelated_full_track_id},
    ) == {referenced_id}


def test_malformed_chunk_framing_never_triggers_raw_byte_scan() -> None:
    coincidental_wem_id = 123_456
    malformed = _chunk(b"BKHD") + struct.pack("<4sI", b"DATA", 100) + struct.pack(
        "<I", coincidental_wem_id
    )

    assert parse_wwise_bank_media_ids(
        malformed,
        known_media_ids={coincidental_wem_id},
    ) == set()


def test_fallback_is_bounded_to_source_bearing_hirc_objects() -> None:
    coincidental_wem_id = 987_654
    unrelated_hirc_payload = struct.pack("<II", 1, coincidental_wem_id)
    bank = _chunk(b"BKHD") + _hirc(_hirc_object(7, unrelated_hirc_payload))

    assert parse_wwise_bank_media_ids(
        bank,
        known_media_ids={coincidental_wem_id},
    ) == set()


def test_hirc_music_track_fallback_accepts_only_one_candidate() -> None:
    first_id = 123_456
    second_id = 654_321
    unique_track = _hirc_object(11, struct.pack("<III", 1, first_id, 0))
    unique_bank = _chunk(b"BKHD") + _hirc(unique_track)
    assert parse_wwise_bank_media_ids(
        unique_bank,
        known_media_ids={first_id, second_id},
    ) == {first_id}

    ambiguous_track = _hirc_object(11, struct.pack("<III", 1, first_id, second_id))
    ambiguous_bank = _chunk(b"BKHD") + _hirc(ambiguous_track)
    assert parse_wwise_bank_media_ids(
        ambiguous_bank,
        known_media_ids={first_id, second_id},
    ) == set()


def test_index_precomputes_normalized_song_bank_and_wem_lookups() -> None:
    preview_id = 360_148_530
    full_id = 111_222_333
    unrelated_id = 659_021_540
    preview_data = b"x" + struct.pack("<I", unrelated_id) + b"y"
    content = {
        "Audio\\Windows\\song_MySong_preview.BNK": _bank_with_didx(
            preview_id, preview_data
        ),
        "audio/windows/song_mysong.bnk": _bank_with_didx(full_id, b"full"),
        "Audio/Windows/360148530.WEM": b"preview",
        "audio/windows/111222333.wem": b"full",
        "audio/windows/659021540.wem": b"unrelated full track",
    }

    index = PsarcContentIndex(content)

    preview_banks = index.preview_bank_paths_for_song("MySong")
    full_banks = index.full_bank_paths_for_song("mysong")
    assert preview_banks == ["Audio\\Windows\\song_MySong_preview.BNK"]
    assert full_banks == ["audio/windows/song_mysong.bnk"]
    assert index.normalized_paths[preview_banks[0]] == (
        "audio/windows/song_mysong_preview.bnk"
    )
    assert index.wem_path_by_id[preview_id] == "Audio/Windows/360148530.WEM"
    assert index.wem_paths_for_banks(preview_banks) == {
        "Audio/Windows/360148530.WEM"
    }
    assert index.wem_paths_for_banks(full_banks) == {
        "audio/windows/111222333.wem"
    }
    # Lookups accept either archive slash style and do not reparse bank bytes.
    assert index.referenced_wem_paths(
        "audio/windows/song_mysong_preview.bnk"
    ) == ("Audio/Windows/360148530.WEM",)
