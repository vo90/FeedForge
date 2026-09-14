"""Reusable path and audio-reference index for extracted PSARC content.

Rocksmith song banks are Wwise BNK files.  Media references live in structured
BNK chunks; treating every four bytes in a bank as a possible WEM id also
interprets compressed audio in the DATA chunk as references.  This module
parses the chunk and object framing once and builds the lookups needed by the
converter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
import re
import struct
from types import MappingProxyType
from typing import AbstractSet


_CHUNK_HEADER = struct.Struct("<4sI")
_DIDX_ENTRY = struct.Struct("<III")
_HIRC_COUNT = struct.Struct("<I")
_HIRC_OBJECT_HEADER = struct.Struct("<BI")

_MAX_BANK_CHUNKS = 4_096
_MAX_HIRC_OBJECTS = 100_000
_MAX_FALLBACK_OBJECT_BYTES = 64 * 1024
_SOURCE_BEARING_HIRC_TYPES = frozenset({2, 11})  # Sound, Music Track


def normalize_psarc_path(path: str) -> str:
    """Return the archive-path form used for case-insensitive lookup."""

    return str(path).replace("\\", "/").lower()


def normalize_song_key(value: str) -> str:
    """Normalize a manifest/bank song key in the same way as the converter."""

    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip().lower())
    return re.sub(r"-+", "-", value).strip("-_")


def _is_chunk_tag(tag: bytes) -> bool:
    # Wwise top-level chunk tags are four uppercase ASCII letters/digits.
    # Checking this makes a coincidental length word in arbitrary data
    # insufficient to turn that data into a parsed bank.
    return len(tag) == 4 and all(
        ord("A") <= value <= ord("Z") or ord("0") <= value <= ord("9")
        for value in tag
    )


def _parse_chunks(data: bytes) -> tuple[tuple[bytes, memoryview], ...] | None:
    view = memoryview(data)
    position = 0
    chunks: list[tuple[bytes, memoryview]] = []

    while position < len(view):
        if len(chunks) >= _MAX_BANK_CHUNKS or len(view) - position < _CHUNK_HEADER.size:
            return None
        tag, size = _CHUNK_HEADER.unpack_from(view, position)
        if not _is_chunk_tag(tag):
            return None
        payload_start = position + _CHUNK_HEADER.size
        payload_end = payload_start + size
        if payload_end > len(view):
            return None
        chunks.append((tag, view[payload_start:payload_end]))
        position = payload_end

    if not chunks or chunks[0][0] != b"BKHD":
        return None
    return tuple(chunks)


def _parse_didx_ids(
    payload: memoryview,
    *,
    data_size: int,
) -> set[int]:
    if not payload or len(payload) % _DIDX_ENTRY.size:
        return set()

    result: set[int] = set()
    for position in range(0, len(payload), _DIDX_ENTRY.size):
        media_id, data_offset, media_size = _DIDX_ENTRY.unpack_from(payload, position)
        if not media_id or not media_size:
            continue
        # DIDX offsets address the bank's DATA payload.  Reject entries whose
        # bounds cannot describe a real embedded-media object.
        if not data_size or data_offset > data_size or media_size > data_size - data_offset:
            continue
        result.add(media_id)
    return result


def _parse_hirc_objects(
    payload: memoryview,
) -> tuple[tuple[int, memoryview], ...] | None:
    if len(payload) < _HIRC_COUNT.size:
        return None
    object_count = _HIRC_COUNT.unpack_from(payload, 0)[0]
    if object_count > _MAX_HIRC_OBJECTS:
        return None

    objects: list[tuple[int, memoryview]] = []
    position = _HIRC_COUNT.size
    for _ in range(object_count):
        if len(payload) - position < _HIRC_OBJECT_HEADER.size:
            return None
        object_type, object_size = _HIRC_OBJECT_HEADER.unpack_from(payload, position)
        object_start = position + _HIRC_OBJECT_HEADER.size
        object_end = object_start + object_size
        if object_size < 4 or object_end > len(payload):
            return None
        objects.append((object_type, payload[object_start:object_end]))
        position = object_end

    if position != len(payload):
        return None
    return tuple(objects)


def _structured_hirc_media_ids(
    objects: Iterable[tuple[int, memoryview]],
    known_media_ids: AbstractSet[int] | None,
) -> set[int]:
    """Read source/file ids from Rocksmith-era Wwise Sound objects.

    In these type-2 objects the source id and streamed-file id are the uint32
    fields at payload offsets 12 and 16.  Without an external WEM-id set, only
    the duplicated form is accepted; with one, each field must name a WEM.
    """

    result: set[int] = set()
    for object_type, payload in objects:
        if object_type != 2 or len(payload) < 20:
            continue
        source_id = struct.unpack_from("<I", payload, 12)[0]
        file_id = struct.unpack_from("<I", payload, 16)[0]
        if known_media_ids is None:
            if source_id and source_id == file_id:
                result.add(source_id)
            continue
        if source_id in known_media_ids:
            result.add(source_id)
        if file_id in known_media_ids:
            result.add(file_id)
    return result


def _bounded_hirc_fallback(
    objects: Iterable[tuple[int, memoryview]],
    known_media_ids: AbstractSet[int] | None,
) -> set[int]:
    """Conservatively recover one source id from a framed HIRC object.

    This is only used when neither DIDX nor the known Sound-object fields found
    a reference.  It never reads DATA, only checks aligned words in bounded
    Sound/Music-Track objects, and accepts the result only when it is unique.
    """

    if not known_media_ids:
        return set()
    candidates: set[int] = set()
    for object_type, payload in objects:
        if object_type not in _SOURCE_BEARING_HIRC_TYPES:
            continue
        limit = min(len(payload), _MAX_FALLBACK_OBJECT_BYTES)
        for position in range(4, max(4, limit - 3), 4):
            value = struct.unpack_from("<I", payload, position)[0]
            if value in known_media_ids:
                candidates.add(value)
                if len(candidates) > 1:
                    return set()
    return candidates


def parse_wwise_bank_media_ids(
    data: bytes,
    *,
    known_media_ids: AbstractSet[int] | None = None,
) -> frozenset[int]:
    """Return external-WEM ids referenced by a structurally valid Wwise bank.

    DIDX is authoritative when it contains ids present in ``known_media_ids``.
    HIRC Sound fields are used for banks without a usable DIDX entry.  Malformed
    chunk/object framing produces no references instead of triggering a raw
    byte scan.
    """

    chunks = _parse_chunks(data)
    if chunks is None:
        return frozenset()

    data_size = sum(len(payload) for tag, payload in chunks if tag == b"DATA")
    didx_ids: set[int] = set()
    hirc_objects: list[tuple[int, memoryview]] = []
    for tag, payload in chunks:
        if tag == b"DIDX":
            didx_ids.update(_parse_didx_ids(payload, data_size=data_size))
        elif tag == b"HIRC":
            parsed = _parse_hirc_objects(payload)
            if parsed is not None:
                hirc_objects.extend(parsed)

    if known_media_ids is None:
        if didx_ids:
            return frozenset(didx_ids)
    else:
        mapped_didx_ids = didx_ids.intersection(known_media_ids)
        if mapped_didx_ids:
            return frozenset(mapped_didx_ids)

    structured_ids = _structured_hirc_media_ids(hirc_objects, known_media_ids)
    if structured_ids:
        return frozenset(structured_ids)
    return frozenset(_bounded_hirc_fallback(hirc_objects, known_media_ids))


def _bank_song_key(path: str) -> tuple[str, bool] | None:
    stem = PurePosixPath(normalize_psarc_path(path)).stem
    preview = stem.endswith("_preview")
    if preview:
        stem = stem.removesuffix("_preview")
    if stem.startswith("song_"):
        stem = stem.removeprefix("song_")
    key = normalize_song_key(stem)
    return (key, preview) if key else None


class PsarcContentIndex:
    """Precomputed path, song-bank, and bank-to-WEM lookups for one PSARC.

    Construct one instance per extracted content mapping and reuse it for every
    song in the archive.  The mapping values are not retained after indexing.
    """

    def __init__(self, content: Mapping[str, bytes]) -> None:
        ordered_paths = sorted(content, key=lambda item: (normalize_psarc_path(item), item))
        normalized_paths = {path: normalize_psarc_path(path) for path in ordered_paths}
        path_by_normalized: dict[str, str] = {}
        for path in ordered_paths:
            path_by_normalized.setdefault(normalized_paths[path], path)

        wem_path_by_id: dict[int, str] = {}
        for path in ordered_paths:
            normalized = normalized_paths[path]
            if not normalized.endswith(".wem"):
                continue
            stem = PurePosixPath(normalized).stem
            if stem.isdigit():
                wem_path_by_id.setdefault(int(stem), path)

        full_banks: dict[str, list[str]] = {}
        preview_banks: dict[str, list[str]] = {}
        bank_paths: list[str] = []
        for path in ordered_paths:
            if not normalized_paths[path].endswith(".bnk"):
                continue
            bank_paths.append(path)
            key_and_kind = _bank_song_key(path)
            if key_and_kind is None:
                continue
            key, preview = key_and_kind
            target = preview_banks if preview else full_banks
            target.setdefault(key, []).append(path)

        known_media_ids = frozenset(wem_path_by_id)
        referenced_wems_by_bank: dict[str, tuple[str, ...]] = {}
        for bank_path in bank_paths:
            media_ids = parse_wwise_bank_media_ids(
                content[bank_path],
                known_media_ids=known_media_ids,
            )
            referenced_wems_by_bank[bank_path] = tuple(
                wem_path_by_id[media_id] for media_id in sorted(media_ids)
            )

        self.normalized_paths = MappingProxyType(normalized_paths)
        self.path_by_normalized = MappingProxyType(path_by_normalized)
        self.wem_path_by_id = MappingProxyType(wem_path_by_id)
        self.full_banks_by_song_key = MappingProxyType(
            {key: tuple(paths) for key, paths in full_banks.items()}
        )
        self.preview_banks_by_song_key = MappingProxyType(
            {key: tuple(paths) for key, paths in preview_banks.items()}
        )
        self.referenced_wems_by_bank = MappingProxyType(referenced_wems_by_bank)

    def full_bank_paths_for_song(self, song_key: str) -> list[str]:
        """Return the full-song banks for ``song_key``."""

        return list(self.full_banks_by_song_key.get(normalize_song_key(song_key), ()))

    def preview_bank_paths_for_song(self, song_key: str) -> list[str]:
        """Return the preview banks for ``song_key``."""

        return list(self.preview_banks_by_song_key.get(normalize_song_key(song_key), ()))

    def referenced_wem_paths(self, bank_path: str) -> tuple[str, ...]:
        """Return precomputed WEM paths for one bank path."""

        original_path = self.path_by_normalized.get(normalize_psarc_path(bank_path))
        if original_path is None:
            return ()
        return self.referenced_wems_by_bank.get(original_path, ())

    def wem_paths_for_banks(self, bank_paths: Iterable[str]) -> set[str]:
        """Return the union of precomputed WEM paths for ``bank_paths``."""

        result: set[str] = set()
        for bank_path in bank_paths:
            result.update(self.referenced_wem_paths(bank_path))
        return result


__all__ = [
    "PsarcContentIndex",
    "normalize_psarc_path",
    "normalize_song_key",
    "parse_wwise_bank_media_ids",
]
