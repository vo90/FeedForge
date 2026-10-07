import copy
from urllib.parse import urlsplit

import pytest

from feedback_converter.song_import.artwork import MAX_REQUESTS, MAX_SECONDS, resolve_album_art
from test_song_import_artwork import Network, recording, release, response, uid


@pytest.mark.parametrize("version", ["Acoustic", "Live", "Remix", "Demo", "Instrumental", "2018 Remastered"])
def test_version_fallback_is_explicit_artwork_only_and_cached(tmp_path, version):
    network = Network()
    metadata = {"artist": "Example Artist", "title": f"Example Song ({version})",
                "audioTitle": "Different performer - Example Song", "year": 2025}
    before = copy.deepcopy(metadata)
    options = dict(transport=network, clock=network.clock, sleep=network.clock.sleep, now=network.clock)
    result = resolve_album_art(metadata, tmp_path / "first", tmp_path / "cache", **options)
    assert metadata == before
    assert result["status"] == "matched" and result["reason"] == "original_song_album_cover"
    assert not {"album", "year", "albumStatus"} & result.keys()
    proof = result["provenance"]
    assert proof["matchingScope"] == "original-song-artwork"
    assert proof["audioRecordingVerified"] is False
    assert proof["specificArtwork"]["reason"] == "recording_not_found"
    assert proof["originalSongArtwork"]["album"] == "Original Album"
    assert proof["originalSongArtwork"]["year"] == 2001
    assert proof["originalSongArtwork"]["trackEvidence"]
    calls = len(network.calls)
    cached = resolve_album_art(metadata, tmp_path / "second", tmp_path / "cache", **options)
    assert len(network.calls) == calls
    assert cached["provenance"] == proof
    assert cached["message"] == result["message"]
    assert cached["path"] != result["path"]


def version_network():
    network = Network()
    network.records.append(recording(9, title="Example Song (Acoustic)"))
    network.releases[uid(9)] = [release(10, 11, "Acoustic Album", "2020")]
    return network


def test_specific_version_cover_always_wins(tmp_path):
    network = version_network()
    result = network.resolve(tmp_path, title="Example Song (Acoustic)")
    assert result["reason"] == "album_cover"
    assert result["album"] == "Acoustic Album"
    assert "originalSongArtwork" not in result["provenance"]
    assert not any('/release-group/' + uid(4) in call["url"] for call in network.calls)


def test_missing_specific_image_uses_original_cover_preserving_specific_enrichment(tmp_path):
    network = version_network()
    network.override = lambda url: response(status=404) if "coverartarchive.org" in url and (
        uid(10) in url or uid(11) in url) else None
    result = network.resolve(tmp_path, title="Example Song (Acoustic)")
    assert result["reason"] == "original_song_album_cover"
    assert result["album"] == "Acoustic Album" and result["year"] == 2020
    assert result["provenance"]["originalSongArtwork"]["album"] == "Original Album"
    assert len(network.calls) <= MAX_REQUESTS
    assert network.clock.value - 1000 < MAX_SECONDS


def test_missing_both_covers_keeps_specific_album_and_reason(tmp_path):
    network = version_network()
    network.cover["images"] = []
    result = network.resolve(tmp_path, title="Example Song (Acoustic)")
    assert result["status"] == "unavailable" and result["reason"] == "cover_not_available"
    assert result["album"] == "Acoustic Album" and result["year"] == 2020
    assert result["provenance"]["originalSongArtworkAttempt"]["reason"] == "cover_not_available"


@pytest.mark.parametrize("title", ["Example Song (Part Two)", "Example Song Live", "Example Song (Unknown Version)"])
def test_unknown_title_parts_are_not_removed(tmp_path, title):
    network = Network()
    result = network.resolve(tmp_path, title=title)
    assert result["status"] == "unavailable" and "path" not in result
    assert not any('coverartarchive.org' in call['url'] for call in network.calls)


def test_audio_context_can_trigger_artwork_only_fallback(tmp_path):
    result = Network().resolve(tmp_path, audioTitle="Example Artist - Example Song (Live)")
    assert result["reason"] == "original_song_album_cover" and "album" not in result


@pytest.mark.parametrize("problem", ["artist", "title", "ambiguous", "track", "pinned"])
def test_original_cover_still_requires_full_identity_proof(tmp_path, problem):
    network = Network()
    metadata = {"title": "Example Song (Acoustic)"}
    if problem == "artist":
        network.records = [recording(artist="Cover Performer")]
    elif problem == "title":
        network.records = [recording(title="Different Song")]
    elif problem == "ambiguous":
        network.releases[uid(2)].append(release(10, 11, "Another Album", "2001-04-03"))
    elif problem == "track":
        network.override = lambda url: response({**release(), "media": []}) if urlsplit(url).path == '/ws/2/release/' + uid(3) else None
    else:
        metadata["musicbrainzRecordingId"] = uid(2)
    result = network.resolve(tmp_path, **metadata)
    assert result["status"] != "matched" and "path" not in result


def test_shared_budget_is_not_reset_for_fallback(tmp_path, monkeypatch):
    import feedback_converter.song_import.artwork as artwork
    monkeypatch.setattr(artwork, "MAX_REQUESTS", 2)
    network = Network()
    result = network.resolve(tmp_path, title="Example Song (Acoustic)")
    assert result["status"] != "matched"
    assert result["provenance"]["originalSongArtworkAttempt"]["reason"] == "lookup_budget_exceeded"
    assert len(network.calls) == 2


def test_transient_catalogue_failure_does_not_retry_other_identity(tmp_path):
    network = Network()
    network.override = lambda url: response(status=503)
    result = network.resolve(tmp_path, title="Example Song (Acoustic)")
    assert result["reason"] == "service_unavailable"
    assert "originalSongArtworkAttempt" not in result["provenance"]


@pytest.mark.parametrize("source_album", [None, "Source version album"])
def test_fallback_cover_enters_feedpak_without_original_album_metadata(tmp_path, source_album):
    import zipfile
    import yaml
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    performance, audio, alignment, job = inputs(tmp_path)
    performance.update(artist="Example Artist", title="Example Song (Acoustic)")
    if source_album:
        performance.update(album=source_album, year=2025)
    before = copy.deepcopy(performance)
    art = Network().resolve(tmp_path / "art", title=performance["title"], album=source_album or "")
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out", artwork=art)
    assert performance == before
    with zipfile.ZipFile(result["stagingPath"]) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        assert manifest["artist"] == performance["artist"] and manifest["title"] == performance["title"]
        assert manifest.get("album") == source_album
        assert manifest.get("year") == (2025 if source_album else None)
        assert len(archive.read(manifest["cover"])) > 0


def test_old_negative_cache_does_not_hide_original_cover(tmp_path):
    import hashlib
    import json
    network = Network()
    values = {"artist": "Example Artist", "title": "Example Song (Acoustic)", "album": "", "audioTitle": ""}
    key = hashlib.sha256(json.dumps([4, values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / (key + ".json")).write_text(json.dumps({"version": 4, "createdAt": network.clock(),
        "result": {"status": "unavailable", "reason": "recording_not_found"}}))
    assert network.resolve(tmp_path / "result", cache=cache, title=values["title"])["reason"] == "original_song_album_cover"
