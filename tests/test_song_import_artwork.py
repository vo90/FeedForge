import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image
import pytest

from feedback_converter.song_import.artwork import HttpResponse, resolve_album_art, IMAGE_LIMIT, JSON_LIMIT


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def credit(name="Example Artist"):
    return [{"name": name, "artist": {"id": uid(1), "name": name}}]


def recording(number=2, title="Example Song", disambiguation="", artist="Example Artist"):
    return {"id": uid(number), "title": title, "artist-credit": credit(artist), "disambiguation": disambiguation}


def release(number=3, group=4, title="Original Album", date="2001-04-03", secondary=None, status="Official", artist="Example Artist"):
    return {"id": uid(number), "title": title, "date": date, "status": status, "artist-credit": credit(artist),
            "release-group": {"id": uid(group), "title": title, "primary-type": "Album",
                              "secondary-types": secondary or [], "first-release-date": date}}


def image_bytes(size=(1600, 800), format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, (92, 10, 32)).save(output, format=format)
    return output.getvalue()


def response(document=None, status=200, headers=None, raw=None):
    return HttpResponse(status, headers or {}, raw if raw is not None else json.dumps(document).encode())


class FakeClock:
    def __init__(self):
        self.value = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, amount):
        self.sleeps.append(amount)
        self.value += amount


class Network:
    def __init__(self):
        self.clock = FakeClock()
        self.calls = []
        self.records = [recording()]
        self.releases = {uid(2): [release()]}
        self.cover = {"release": f"https://musicbrainz.org/release/{uid(3)}", "images": [
            {"id": "99", "front": True, "approved": True,
             "image": "https://archive.org/download/example/original.png",
             "thumbnails": {"1200": "https://archive.org/download/example/front.png"}}]}
        self.image = image_bytes()
        self.override = None

    def __call__(self, url, headers, timeout, maximum):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, "maximum": maximum, "time": self.clock()})
        if self.override:
            replaced = self.override(url)
            if replaced is not None:
                return replaced
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        if parts.hostname == "musicbrainz.org":
            if parts.path.startswith("/ws/2/recording/") and parts.path != "/ws/2/recording/":
                candidate = next((record for record in self.records if record["id"] == parts.path.rsplit("/", 1)[1]), None)
                return response(candidate, status=200 if candidate else 404)
            if parts.path == "/ws/2/recording/":
                offset = int(params["offset"][0])
                return response({"count": len(self.records), "recordings": self.records[offset:offset + 100]})
            if parts.path == "/ws/2/release/":
                records = self.releases.get(params["recording"][0], [])
                offset = int(params["offset"][0])
                return response({"release-count": len(records), "releases": records[offset:offset + 100]})
        if parts.hostname == "coverartarchive.org":
            return response(self.cover)
        if parts.hostname == "archive.org":
            return response(raw=self.image, headers={"Content-Type": "image/png"})
        pytest.fail(f"Unexpected network address: {url}")

    def resolve(self, directory, *, cache=None, **metadata):
        return resolve_album_art({"artist": "Example Artist", "title": "Example Song", **metadata}, directory,
                                 cache, transport=self, clock=self.clock, sleep=self.clock.sleep, now=self.clock)


def test_official_album_cover_preserves_aspect_and_records_identity(tmp_path):
    network = Network()
    supplied = {"artist": "Example Artist", "title": "Example Song", "year": 1999}
    result = resolve_album_art(supplied, tmp_path, transport=network, clock=network.clock,
                               sleep=network.clock.sleep, now=network.clock)
    assert result["status"] == "matched"
    assert result["album"] == "Original Album"
    assert result["year"] == 2001
    assert result["provenance"]["recordingId"] == uid(2)
    assert result["provenance"]["releaseGroupId"] == uid(4)
    assert result["provenance"]["releaseId"] == uid(3)
    assert supplied == {"artist": "Example Artist", "title": "Example Song", "year": 1999}
    with Image.open(result["path"]) as image:
        assert image.size == (1200, 600)
        assert image.mode == "RGB"
    assert all("FeedForge/" in call["headers"]["User-Agent"] for call in network.calls)
    assert all(call["timeout"] <= 10 for call in network.calls)


def test_standalone_music_video_does_not_conflict_with_audio_album(tmp_path):
    network = Network()
    network.records.append({**recording(number=9), "video": True})
    result = network.resolve(tmp_path)
    assert result["status"] == "matched" and result["album"] == "Original Album"
    assert result["provenance"]["audioRecordingVerified"] is False
    assert result["provenance"]["recordingIds"] == [uid(2)]


def test_recording_identifier_takes_priority_but_cannot_override_conflicting_title(tmp_path):
    network = Network()
    result = network.resolve(tmp_path / "matching", musicbrainzRecordingId=uid(2))
    assert result["status"] == "matched"
    assert "recording-id" in result["provenance"]["matchBasis"]
    assert "query=" not in network.calls[0]["url"]
    assert network.resolve(tmp_path / "wrong", musicbrainzRecordingId=uid(2), title="Other Song")["status"] == "unavailable"
    assert next(call for call in network.calls if urlsplit(call["url"]).hostname == "archive.org")["maximum"] == IMAGE_LIMIT
    assert network.calls[0]["maximum"] == JSON_LIMIT


def test_original_album_beats_earlier_compilation_live_bootleg_and_reissue(tmp_path):
    network = Network()
    network.releases[uid(2)] = [
        release(10, 11, "Greatest Hits", "1990", ["Compilation"]),
        release(12, 13, "Live Record", "1991", ["Live"]),
        release(14, 15, "Unauthorized", "1992", status="Bootleg"),
        release(16, 4, "Original Album", "2015-05-02"), release(),
        release(18, 19, "Later Album", "2008-06-07")]
    network.releases[uid(2)][3]["release-group"]["first-release-date"] = "2001-04-03"
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    assert result["provenance"]["releaseId"] == uid(3)


def test_supplied_album_wins_over_earlier_original_and_is_not_overwritten(tmp_path):
    network = Network()
    network.releases[uid(2)].append(release(9, 10, "Requested Album", "2009"))
    result = network.resolve(tmp_path, album="Requested Album")
    assert result["status"] == "matched"
    assert result["album"] == "Requested Album"
    assert result["provenance"]["releaseGroupId"] == uid(10)
    assert result["provenance"]["albumSelection"] == "supplied-album"


def test_missing_supplied_album_never_silently_uses_another_album(tmp_path):
    network = Network()
    result = network.resolve(tmp_path, album="An unrelated album")
    assert result["status"] == "unavailable"
    assert result["reason"] == "album_not_found"
    assert len(network.calls) == 2


@pytest.mark.parametrize("candidate", [
    recording(2, artist="A cover artist"), recording(2, title="Example Song Part Two"),
    recording(2, disambiguation="live"), recording(2, disambiguation="acoustic"),
    recording(2, disambiguation="remix"), recording(2, disambiguation="2018 remaster"),
])
def test_wrong_artist_title_or_recording_version_does_not_match(tmp_path, candidate):
    network = Network()
    network.records = [candidate]
    result = network.resolve(tmp_path)
    assert result["status"] == "unavailable"
    assert len(network.calls) == 1


def test_explicit_live_recording_uses_compatible_live_album(tmp_path):
    network = Network()
    network.records = [recording(disambiguation="live")]
    network.releases[uid(2)] = [release(9, 10, "Concert Album", "2009", ["Live"]), release()]
    result = network.resolve(tmp_path, title="Example Song (Live)")
    assert result["status"] == "matched"
    assert result["album"] == "Concert Album"
    query = parse_qs(urlsplit(network.calls[0]["url"]).query)["query"][0]
    assert 'recording:"Example Song"' in query


def test_artist_punctuation_accents_and_explicit_aliases(tmp_path):
    network = Network()
    network.records[0]["artist-credit"][0]["artist"]["aliases"] = [{"name": "Exámple Artist"}]
    result = network.resolve(tmp_path, artist="Exámple-Artist")
    assert result["status"] == "matched"


def test_video_duration_does_not_force_another_recording(tmp_path):
    network = Network()
    network.records[0]["length"] = 200000
    result = network.resolve(tmp_path, duration=260.0, audioKind="youtube")
    assert result["status"] == "matched"


def test_recording_ambiguity_is_safe_but_common_album_is_usable(tmp_path):
    network = Network()
    network.records.append(recording(20))
    network.releases[uid(20)] = [release()]
    result = network.resolve(tmp_path / "same")
    assert result["status"] == "matched"
    assert result["provenance"]["recordingId"] is None
    assert result["provenance"]["recordingIds"] == [uid(2), uid(20)]
    assert result["provenance"]["matchingScope"] == "album"
    network.releases[uid(20)] = [release(22, 23, "Different Album")]
    result = network.resolve(tmp_path / "different")
    assert result["status"] == "ambiguous"
    assert "path" not in result


def test_release_pagination_considers_original_album_on_later_page(tmp_path):
    network = Network()
    network.releases[uid(2)] = [release(100 + i, 20, "Later Album", "2010-05-06") for i in range(100)] + [release()]
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    assert result["album"] == "Original Album"
    assert any("offset=100" in call["url"] for call in network.calls)


@pytest.mark.parametrize("broken", [
    {"release-count": 301, "releases": [release()]},
    {"release-count": 1, "releases": []},
    {"release-count": 2, "releases": [release(), release()]},
    {"releases": [release()]},
])
def test_truncated_or_inconsistent_release_catalogue_cannot_auto_select(tmp_path, broken):
    network = Network()
    network.override = lambda url: response(broken) if "/ws/2/release/" in url else None
    result = network.resolve(tmp_path)
    assert result["status"] == "ambiguous"
    assert result["reason"] == "incomplete_catalogue"


def test_same_year_partially_dated_albums_remain_ambiguous(tmp_path):
    network = Network()
    network.releases[uid(2)] = [release(date="2001"), release(10, 11, "Other Album", "2001-02-03")]
    result = network.resolve(tmp_path)
    assert result["status"] == "ambiguous"


def test_missing_release_art_tries_only_same_release_group(tmp_path):
    network = Network()
    network.override = lambda url: response(status=404) if "coverartarchive.org/release/" in url else None
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    assert result["provenance"]["sourceUrl"] == f"https://coverartarchive.org/release-group/{uid(4)}"


@pytest.mark.parametrize("cover", [{"images": []}, {"images": [{"front": False, "approved": True}]},
                                   {"images": [{"front": True, "approved": False}]}])
def test_missing_front_art_is_nonblocking_and_never_a_thumbnail_fallback(tmp_path, cover):
    network = Network()
    network.cover = cover
    result = network.resolve(tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "cover_not_available"
    assert not list(tmp_path.glob("*.png"))
    assert not any("youtube" in call["url"] for call in network.calls)


@pytest.mark.parametrize("raw", [b"not an image", b"<html>login</html>", image_bytes((8, 8)), image_bytes(format="GIF")],
                         ids=["invalid", "html", "tiny", "gif"])
def test_corrupt_tiny_or_unsupported_art_is_nonblocking(tmp_path, raw):
    network = Network()
    network.image = raw
    result = network.resolve(tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_cover_image"


@pytest.mark.parametrize("url", ["https://127.0.0.1/cover.png", "https://evil.example/cover.png",
                                 "https://archive.org.evil.example/cover.png", "http://127.0.0.1/cover.png",
                                 "https://user:password@archive.org/cover.png", "https://archive.org:8443/cover.png"])
def test_untrusted_image_hosts_and_ports_are_rejected(tmp_path, url):
    network = Network()
    network.cover["images"][0]["thumbnails"]["1200"] = url
    result = network.resolve(tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "unsafe_artwork_address"
    assert not any(call["url"] == url for call in network.calls)


def test_redirect_target_is_validated_before_contact(tmp_path):
    network = Network()
    network.override = lambda url: response(status=302, headers={"Location": "http://localhost/private"}) if "archive.org/download/" in url else None
    result = network.resolve(tmp_path)
    assert result["reason"] == "unsafe_artwork_address"
    assert not any("localhost" in call["url"] for call in network.calls)


def test_historical_http_caa_image_url_is_upgraded_before_request(tmp_path):
    network = Network()
    network.cover["images"][0]["thumbnails"]["1200"] = "http://archive.org/download/example/front.png"
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    assert result["provenance"]["imageUrl"].startswith("https://archive.org/")
    assert all(call["url"].startswith("https://") for call in network.calls)


def test_oversized_response_is_rejected_even_if_transport_ignores_limit(tmp_path):
    network = Network()
    network.override = lambda url: response(raw=b" " * (JSON_LIMIT + 1))
    result = network.resolve(tmp_path)
    assert result["reason"] == "invalid_response"


def test_musicbrainz_rate_limit_and_single_bounded_retry(tmp_path):
    network = Network()
    attempted = False

    def retry(url):
        nonlocal attempted
        if "musicbrainz.org" in url and not attempted:
            attempted = True
            return response(status=503, headers={"Retry-After": "2"})

    network.override = retry
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    calls = [call for call in network.calls if "musicbrainz.org" in call["url"]]
    assert len(calls) == 3
    assert all(b["time"] - a["time"] >= 1 for a, b in zip(calls, calls[1:]))
    assert 2 in network.clock.sleeps


def test_persistent_cache_avoids_network_and_checks_image_hash(tmp_path):
    network = Network()
    cache = tmp_path / "cache"
    first = network.resolve(tmp_path / "one", cache=cache)
    count = len(network.calls)
    second = network.resolve(tmp_path / "two", cache=cache)
    assert second["status"] == "matched"
    assert len(network.calls) == count
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes()
    next(cache.glob("*.png")).write_bytes(b"corrupt")
    third = network.resolve(tmp_path / "three", cache=cache)
    assert third["status"] == "matched"
    assert len(network.calls) > count


def test_lock_file_left_by_cancelled_worker_does_not_block_lookup(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "musicbrainz.lock").write_bytes(b"")
    network = Network()
    assert network.resolve(tmp_path / "art", cache=cache)["status"] == "matched"


def _another_song(network, title="Another Song", album=None):
    network.records.append(recording(number=20, title=title))
    network.releases[uid(20)] = [album or release()]


def _artwork_calls(network):
    return [call for call in network.calls if urlsplit(call["url"]).hostname in {"coverartarchive.org", "archive.org"}]


def test_same_album_cover_is_reused_after_independent_song_match(tmp_path):
    network = Network()
    cache = tmp_path / "cache"
    first = network.resolve(tmp_path / "first", cache=cache)
    artwork_count = len(_artwork_calls(network))
    count = len(network.calls)
    _another_song(network)
    second = network.resolve(tmp_path / "second", cache=cache, title="Another Song")
    assert second["status"] == "matched"
    assert len(network.calls) > count, "second song still resolves its own recording and album"
    assert len(_artwork_calls(network)) == artwork_count
    assert second["provenance"]["recordingId"] == uid(20)
    assert second["provenance"]["recordingIds"] == [uid(20)]
    assert first["provenance"]["recordingId"] == uid(2)
    assert second["provenance"]["releaseId"] == first["provenance"]["releaseId"]
    assert Path(second["path"]).read_bytes() == Path(first["path"]).read_bytes()


def test_corrupt_shared_cover_is_refetched_for_next_song(tmp_path):
    network = Network()
    cache = tmp_path / "cache"
    assert network.resolve(tmp_path / "first", cache=cache)["status"] == "matched"
    count = len(_artwork_calls(network))
    next((cache / "albums").glob("*.png")).write_bytes(b"corrupt")
    _another_song(network)
    assert network.resolve(tmp_path / "second", cache=cache, title="Another Song")["status"] == "matched"
    assert len(_artwork_calls(network)) > count
    with Image.open(next((cache / "albums").glob("*.png"))) as image:
        image.verify()


def test_shared_cover_age_is_capped_at_30_days_even_if_saved_ttl_is_larger(tmp_path):
    network = Network()
    cache = tmp_path / "cache"
    network.resolve(tmp_path / "first", cache=cache)
    record = next((cache / "albums").glob("*.json"))
    saved = json.loads(record.read_text())
    saved["ttl"] = 365 * 86400
    record.write_text(json.dumps(saved))
    network.clock.value += 30 * 86400 + 1
    count = len(_artwork_calls(network))
    _another_song(network)
    assert network.resolve(tmp_path / "second", cache=cache, title="Another Song")["status"] == "matched"
    assert len(_artwork_calls(network)) > count


@pytest.mark.parametrize("other_release", [release(30, 4), release(3, 40)])
def test_shared_cover_does_not_cross_release_or_album_identity(tmp_path, other_release):
    network = Network()
    cache = tmp_path / "cache"
    network.resolve(tmp_path / "first", cache=cache)
    count = len(_artwork_calls(network))
    _another_song(network, album=other_release)
    assert network.resolve(tmp_path / "second", cache=cache, title="Another Song")["status"] == "matched"
    assert len(_artwork_calls(network)) > count


def test_failed_image_decode_does_not_create_a_shared_cover_entry(tmp_path):
    network = Network()
    cache = tmp_path / "cache"
    network.image = b"not an image"
    assert network.resolve(tmp_path / "first", cache=cache)["reason"] == "invalid_cover_image"
    assert not list((cache / "albums").glob("*.png"))
    assert not list((cache / "albums").glob("*.json"))


def test_negative_match_cache_expires_and_does_not_publish_a_placeholder(tmp_path):
    network = Network()
    network.records = []
    cache = tmp_path / "cache"
    first = network.resolve(tmp_path / "one", cache=cache)
    count = len(network.calls)
    assert network.resolve(tmp_path / "two", cache=cache) == first
    assert len(network.calls) == count
    network.clock.value += 86401
    network.resolve(tmp_path / "three", cache=cache)
    assert len(network.calls) == count + 1
    assert not list(cache.glob("*.png"))


def test_unavailable_service_is_nonblocking_and_retry_is_bounded(tmp_path):
    network = Network()
    network.override = lambda url: response(status=429, headers={"Retry-After": "9999"})
    result = network.resolve(tmp_path)
    assert result["status"] == "unavailable"
    assert len(network.calls) == 1, "must not retry before the server's Retry-After"
    assert not network.clock.sleeps


def test_transient_transport_timeout_retries_once_within_budget(tmp_path):
    network = Network()
    attempted = False
    def transient(url):
        nonlocal attempted
        if not attempted:
            attempted = True
            raise TimeoutError("temporary")
    network.override = transient
    result = network.resolve(tmp_path)
    assert result["status"] == "matched"
    assert len([call for call in network.calls if "musicbrainz.org" in call["url"]]) == 3


def test_transport_exception_and_invalid_input_are_nonblocking(tmp_path):
    def offline(*args):
        raise TimeoutError("offline")
    assert resolve_album_art({"title": "Example", "artist": "Artist"}, tmp_path, transport=offline)["status"] == "unavailable"
    assert resolve_album_art({"title": "Example"}, tmp_path, transport=offline)["reason"] == "metadata_missing"


def test_no_album_from_various_artist_compilation_with_missing_secondary_tag(tmp_path):
    network = Network()
    network.releases[uid(2)] = [release(10, 11, "Collection", "1999", artist="Various Artists")]
    assert network.resolve(tmp_path)["reason"] == "album_not_found"
