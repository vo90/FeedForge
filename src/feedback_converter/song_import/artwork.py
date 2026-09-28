"""Best-effort, recording-aware album covers from MusicBrainz and CAA.

No account, video thumbnail, or first-search-result fallback is used. Transport
is injectable for offline tests: ``transport(url, headers, timeout, max_bytes)``
returns HttpResponse and MUST NOT follow redirects; this module checks each hop.
The returned album/year are enrichment suggestions, never edits to the input.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid
import warnings

from PIL import Image, ImageOps

POLICY_VERSION = 3
COVER_ENCODING = "jpeg-512-q90-v1"
COVER_MAX_SIZE = 512
USER_AGENT = "FeedForge/2.0.1 (album artwork; https://github.com/balki97/FeedForge)"
JSON_LIMIT = 2 * 1024 * 1024
IMAGE_LIMIT = 12 * 1024 * 1024
MAX_REQUESTS = 24
MAX_SECONDS = 45.0
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_RATE_LOCK = threading.Lock()
_LAST_MB = 0.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict
    body: bytes


class _LookupFailure(Exception):
    def __init__(self, reason, status="unavailable"):
        self.reason, self.status = reason, status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(url, headers, timeout, max_bytes):
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(Request(url, headers=headers), timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        raw_length = response.headers.get("Content-Length")
        if raw_length and int(raw_length) > max_bytes:
            raise _LookupFailure("response_too_large")
        deadline, chunks, size = time.monotonic() + timeout, [], 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Artwork response timed out.")
            chunk = response.read1(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise _LookupFailure("response_too_large")
            chunks.append(chunk)
        return HttpResponse(response.status, dict(response.headers), b"".join(chunks))


def _url_allowed(url, kind, *, resolve=False):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        if parsed.fragment or "\\" in url or any(ord(c) < 32 for c in url):
            raise ValueError()
        allowed = host == "musicbrainz.org" if kind == "mb" else (
            host == "coverartarchive.org" or host == "archive.org" or host.endswith(".archive.org"))
        if not allowed:
            raise ValueError()
        if resolve:
            # OS resolver calls do not consistently obey socket timeouts. A
            # daemon resolver bounds this optional operation without holding
            # converter shutdown open if the platform DNS service stalls.
            answers = queue.Queue(maxsize=1)
            def lookup():
                try:
                    answers.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
                except OSError as exc:
                    answers.put(exc)
            threading.Thread(target=lookup, daemon=True).start()
            try:
                addresses = answers.get(timeout=4)
            except queue.Empty:
                raise _LookupFailure("dns_unavailable") from None
            if isinstance(addresses, OSError):
                raise addresses
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError()
    except (ValueError, OSError):
        raise _LookupFailure("unsafe_artwork_address") from None


def _atomic_json(path, document):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _try_process_lock(handle):
    os.lseek(handle, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


class _Client:
    def __init__(self, transport, cache, clock, sleep, now):
        self.transport = transport or _http
        self.resolve = transport is None
        self.cache, self.clock, self.sleep, self.now = cache, clock, sleep, now
        self.started, self.requests = clock(), 0
        self.last_mb = None

    def _remaining(self):
        remaining = MAX_SECONDS - (self.clock() - self.started)
        if remaining <= 0 or self.requests >= MAX_REQUESTS:
            raise _LookupFailure("lookup_budget_exceeded", "ambiguous")
        return remaining

    def _pause(self, seconds):
        if seconds >= self._remaining():
            raise _LookupFailure("lookup_budget_exceeded", "ambiguous")
        if seconds > 0:
            self.sleep(seconds)

    def _throttle(self):
        # A shared cache also coordinates independent worker processes. Failure
        # to acquire its short-lived lock is nonblocking for song conversion.
        global _LAST_MB
        with _RATE_LOCK:
            lock = self.cache / "musicbrainz.lock" if self.cache else None
            handle = None
            try:
                if lock:
                    # Closing the descriptor (including process termination)
                    # releases this OS lock. An old lock file is harmless.
                    handle = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
                    if os.fstat(handle).st_size == 0:
                        os.write(handle, b"\0")
                    acquired = False
                    for _ in range(60):
                        try:
                            _try_process_lock(handle)
                            acquired = True
                            break
                        except (OSError, BlockingIOError):
                            self._pause(0.05)
                    if not acquired:
                        raise _LookupFailure("artwork_lookup_busy")
                    previous = 0.0
                    state = self.cache / "musicbrainz-rate.json"
                    if state.is_file() and state.stat().st_size < 256:
                        try:
                            previous = float(json.loads(state.read_text(encoding="utf-8"))["lastRequest"])
                        except (ValueError, KeyError, TypeError):
                            previous = 0.0
                    if math.isfinite(previous):
                        self._pause(max(0.0, 1.05 - (self.now() - previous)))
                    _atomic_json(state, {"lastRequest": self.now()})
                elif self.resolve:
                    # Default transport calls share the process-wide limit.
                    self._pause(max(0.0, 1.05 - (time.monotonic() - _LAST_MB)))
                    _LAST_MB = time.monotonic()
                elif self.last_mb is not None:
                    self._pause(max(0.0, 1.05 - (self.clock() - self.last_mb)))
                self.last_mb = self.clock()
            finally:
                if handle is not None:
                    os.close(handle)

    def get(self, url, kind, maximum=JSON_LIMIT):
        retries = 0
        redirects = 0
        while True:
            _url_allowed(url, kind, resolve=self.resolve)
            self._remaining()
            if kind == "mb":
                self._throttle()
            timeout = min(10.0, self._remaining())
            self.requests += 1
            try:
                response = self.transport(url, {"User-Agent": USER_AGENT,
                                               "Accept": "application/json" if maximum == JSON_LIMIT else "image/*"},
                                          timeout, maximum)
            except (TimeoutError, URLError, ConnectionError):
                if retries >= 1:
                    raise _LookupFailure("service_unavailable") from None
                retries += 1
                self._pause(1.0)
                continue
            if not isinstance(response, HttpResponse) or not isinstance(response.body, bytes) or len(response.body) > maximum:
                raise _LookupFailure("invalid_response")
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            if response.status in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > 3 or not headers.get("location"):
                    raise _LookupFailure("invalid_redirect")
                url = urljoin(url, headers["location"])
                continue
            if response.status in (429, 500, 502, 503, 504) and retries < 1:
                retries += 1
                try:
                    delay = float(headers.get("retry-after", 1))
                except ValueError:
                    delay = 1.0
                # Honor a server-requested delay; if it exceeds this optional
                # lookup's budget, return unavailable instead of retrying early.
                delay = max(1.0, delay) if math.isfinite(delay) else 1.0
                if delay >= self._remaining():
                    raise _LookupFailure("service_unavailable")
                self._pause(delay)
                continue
            if response.status == 404:
                return None
            if response.status != 200:
                raise _LookupFailure("service_unavailable")
            return response.body

    def json(self, url, kind, *, fresh=False):
        # Share stable entity lookups between songs; searches still have their
        # own complete-result checks and song-result cache. Never cache errors.
        cache_file = None
        self.last_json_cached = False
        if kind == 'mb' and self.cache is not None and 'query=' not in url:
            folder = self.cache / 'lookups'
            try:
                folder.mkdir(exist_ok=True)
                cache_file = folder / (hashlib.sha256(url.encode()).hexdigest() + '.json')
                if not fresh and cache_file.is_file() and cache_file.stat().st_size <= JSON_LIMIT:
                    saved = json.loads(cache_file.read_text(encoding='utf8'))
                    if saved.get('version') == POLICY_VERSION and 0 <= self.now() - saved['createdAt'] < 86400 and isinstance(saved.get('data'), dict):
                        self.last_json_cached = True
                        return saved['data']
            except (OSError, ValueError, KeyError, TypeError):
                pass
        raw = self.get(url, kind)
        if raw is None:
            return None
        try:
            result = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise _LookupFailure("invalid_metadata") from None
        if not isinstance(result, dict):
            raise _LookupFailure("invalid_metadata")
        if cache_file is not None:
            try:
                _atomic_json(cache_file, {'version': POLICY_VERSION, 'createdAt': self.now(), 'data': result})
            except OSError:
                pass
        return result


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(c for c in value if c.isalnum())


_VERSIONS = {
    "live": r"\blive\b", "acoustic": r"\bacoustic\b", "instrumental": r"\binstrumental\b",
    "remix": r"\bremix(?:ed)?\b", "demo": r"\bdemo\b", "rerecorded": r"\bre[- ]?record(?:ed|ing)?\b",
    "remastered": r"\bremaster(?:ed)?\b", "edit": r"\b(?:radio|single|video)\s+edit\b",
    "extended": r"\bextended\b", "mono": r"\bmono\b", "stereo": r"\bstereo\b",
}


def _title_identity(title, disambiguation=""):
    tags = set()
    details = []

    def classify(fragment):
        found = {key for key, pattern in _VERSIONS.items() if re.search(pattern, fragment, re.I)}
        if found:
            tags.update(found)
            details.append(_norm(fragment))
        return bool(found)

    def replace(match):
        return "" if classify(match.group(1)) else match.group(0)

    base = re.sub(r"[\[(]([^\])]+)[\])]", replace, str(title))
    parts = re.split(r"\s+[-–—]\s+", base)
    if len(parts) > 1 and classify(parts[-1]):
        base = " - ".join(parts[:-1])
    classify(disambiguation)
    return _norm(base), tags, set(details)


def _search_title(title):
    def replace(match):
        fragment = match.group(1)
        return "" if any(re.search(pattern, fragment, re.I) for pattern in _VERSIONS.values()) else match.group(0)
    base = re.sub(r"[\[(]([^\])]+)[\])]", replace, str(title))
    parts = re.split(r"\s+[-–—]\s+", base)
    if len(parts) > 1 and any(re.search(pattern, parts[-1], re.I) for pattern in _VERSIONS.values()):
        base = " - ".join(parts[:-1])
    return base.strip()


def _artist_names(record):
    credits = record.get("artist-credit")
    if not isinstance(credits, list) or not credits:
        return set()
    credited, canonical = [], []
    for credit in credits:
        if not isinstance(credit, dict) or not isinstance(credit.get("artist"), dict):
            return set()
        join = str(credit.get("joinphrase") or "")
        credited.append(str(credit.get("name") or credit["artist"].get("name") or "") + join)
        canonical.append(str(credit["artist"].get("name") or "") + join)
    names = {_norm("".join(credited)), _norm("".join(canonical))}
    if len(credits) == 1:
        names.update(_norm(alias.get("name")) for alias in credits[0]["artist"].get("aliases", []) if isinstance(alias, dict))
    return names - {""}


def _query_url(entity, values):
    return "https://musicbrainz.org/ws/2/" + entity + "/?" + urlencode({**values, "fmt": "json"})


def _pages(client, entity, values, list_key, count_key, cap):
    found, ids, expected = [], set(), None
    offset = 0
    while True:
        page = client.json(_query_url(entity, {**values, "limit": 100, "offset": offset}), "mb")
        if page is None:
            return []
        count, items = page.get(count_key), page.get(list_key)
        if type(count) is not int or count < 0 or not isinstance(items, list):
            raise _LookupFailure("incomplete_catalogue", "ambiguous")
        if count > cap or (expected is not None and count != expected):
            raise _LookupFailure("incomplete_catalogue", "ambiguous")
        expected = count
        if not items and offset < count:
            raise _LookupFailure("incomplete_catalogue", "ambiguous")
        for item in items:
            if not isinstance(item, dict) or not _UUID.fullmatch(str(item.get("id") or "")) or item["id"] in ids:
                raise _LookupFailure("incomplete_catalogue", "ambiguous")
            ids.add(item["id"])
            found.append(item)
        offset += len(items)
        if offset == count:
            return found
        if offset > count or offset >= cap:
            raise _LookupFailure("incomplete_catalogue", "ambiguous")


_EXCLUDE = {"compilation", "live", "remix", "dj-mix", "mixtape/street", "demo", "interview", "audiobook", "spokenword"}


def _cover_indexes(client, group):
    """Prefer the matched edition, then album default, then verified editions.

    Other editions only supply artwork. They never replace the original track
    evidence, album/year, recording choice or audio synchronization.
    """
    yield "release", group["release"]["id"]
    yield "release-group", group["id"]
    from .album_match import _ids
    editions = sorted(group.get("releases", {}).values(), key=lambda r: (str(r.get("date") or "9999"), r["id"]))
    editions = [r for r in editions if r["id"] != group["release"]["id"]]
    for edition in editions[:3]:
        identifier = edition["id"]
        if not _UUID.fullmatch(str(identifier)):
            continue
        full = client.json(f"https://musicbrainz.org/ws/2/release/{identifier}?fmt=json&inc=artist-credits+release-groups", "mb")
        if (not full or full.get("id") != identifier or full.get("status") != "Official"
                or (full.get("release-group") or {}).get("id") != group["id"]
                or _ids(full) != group["artistIds"]):
            continue
        # A successful catalogue response explicitly reporting no front art
        # does not need a second request to the image service.
        if (full.get("cover-art-archive") or {}).get("front") is False:
            continue
        yield "release", identifier


def _cover(client, group):
    unavailable = None
    attempted = set()
    for kind, identifier in _cover_indexes(client, group):
        index_url = f"https://coverartarchive.org/{kind}/{identifier}"
        try:
            index = client.json(index_url, "caa")
        except _LookupFailure as exc:
            if exc.reason != 'service_unavailable':
                raise
            unavailable = exc
            continue
        if not index:
            continue
        images = index.get("images")
        if not isinstance(images, list) or len(images) > 100:
            raise _LookupFailure("invalid_artwork_metadata")
        eligible = [item for item in images if isinstance(item, dict) and item.get("front") is True and item.get("approved") is True]
        if not eligible:
            continue
        release_url = str(index.get("release") or "")
        cover_release = release_url.rstrip("/").split("/")[-1]
        if not _UUID.fullmatch(cover_release) or (kind == "release" and cover_release != identifier):
            raise _LookupFailure("invalid_artwork_metadata")
        for image in eligible[:3]:
            thumbnails = image.get("thumbnails") if isinstance(image.get("thumbnails"), dict) else {}
            variants = 0
            for image_url in (thumbnails.get("500"), thumbnails.get("1200"), image.get("image")):
                if not isinstance(image_url, str) or not image_url:
                    continue
                # Historical CAA indexes include HTTP links to their own image
                # hosts. Upgrade, then validate every address and redirect.
                parsed = urlsplit(image_url)
                if parsed.scheme == "http" and parsed.port in (None, 80) and not parsed.username and not parsed.password:
                    image_url = urlunsplit(("https", parsed.hostname or "", parsed.path, parsed.query, parsed.fragment))
                if image_url in attempted:
                    continue
                # Leave room for another edition instead of spending the whole
                # request budget on different sizes served by one failing host.
                if variants >= 2:
                    break
                variants += 1
                attempted.add(image_url)
                try:
                    raw = client.get(image_url, "caa", IMAGE_LIMIT)
                    if raw is None:
                        continue
                    # Decode here so a broken image can fall through to another
                    # candidate. Only validated compact bytes enter the cache.
                    raw = _normalize_image(raw)
                except _LookupFailure as exc:
                    if exc.reason not in {"service_unavailable", "invalid_cover_image", "response_too_large"}:
                        raise
                    unavailable = exc
                    continue
                return raw, {"source": "cover-art-archive", "sourceUrl": index_url, "imageUrl": image_url,
                             "artworkReleaseId": cover_release}
    raise unavailable or _LookupFailure("cover_not_available")


def _normalize_image(raw, *, reuse_encoded=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as opened:
                if (opened.format not in {"JPEG", "PNG", "WEBP"} or opened.width * opened.height > 20_000_000
                        or min(opened.size) < (1 if reuse_encoded else 32)):
                    raise ValueError()
                # Our validated compact cache must not acquire another lossy
                # generation every time the same album is reused.
                if (reuse_encoded and opened.format == "JPEG" and opened.mode == "RGB"
                        and max(opened.size) <= COVER_MAX_SIZE and not opened.getexif()):
                    opened.load()
                    return raw
                image = ImageOps.exif_transpose(opened)
                image.thumbnail((COVER_MAX_SIZE, COVER_MAX_SIZE), Image.Resampling.LANCZOS)
                if image.mode == "RGBA" or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=90, optimize=True)
                return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _LookupFailure("invalid_cover_image") from None


def _result(status, reason, **values):
    messages = {"matched": "Album cover found.", "ambiguous": "Album cover could not be selected confidently. The song can still be converted.",
                "unavailable": "Album cover is unavailable. The song can still be converted."}
    return {"status": status, "reason": reason, "message": messages[status], "provenance": {"policyVersion": POLICY_VERSION}, **values}


def _cache_read(cache, key, now, *, max_age=None):
    if cache is None:
        return None
    try:
        record = cache / f"{key}.json"
        if not record.is_file() or record.stat().st_size > 64 * 1024:
            return None
        saved = json.loads(record.read_text(encoding="utf-8"))
        ttl = min(saved["ttl"], max_age) if max_age is not None else saved["ttl"]
        if saved.get("version") != POLICY_VERSION or not 0 <= now() - saved["createdAt"] <= ttl:
            return None
        result = saved["result"]
        if result.get("status") not in {"matched", "ambiguous", "unavailable"}:
            return None
        raw = None
        if result["status"] == "matched":
            compact = result["provenance"].get("imageEncoding") == COVER_ENCODING
            image = cache / f"{key}.{'jpg' if compact else 'png'}"
            if not image.is_file() or image.stat().st_size > IMAGE_LIMIT:
                return None
            raw = image.read_bytes()
            if hashlib.sha256(raw).hexdigest() != result["provenance"]["imageHash"]:
                return None
            # Old matching records remain useful: compact their PNG locally,
            # without another lookup or changing the saved cache's expiry.
            raw = _normalize_image(raw, reuse_encoded=compact)
            result["provenance"].update({"imageHash": hashlib.sha256(raw).hexdigest(),
                                         "imageEncoding": COVER_ENCODING})
        return result, raw
    except (OSError, ValueError, KeyError, TypeError, _LookupFailure):
        return None


def _cache_write(cache, key, result, raw, now):
    if cache is None:
        return
    try:
        if raw:
            temporary = cache / f"{key}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_bytes(raw)
                os.replace(temporary, cache / f"{key}.jpg")
            finally:
                temporary.unlink(missing_ok=True)
        _atomic_json(cache / f"{key}.json", {"version": POLICY_VERSION, "createdAt": now(),
                                           "ttl": 30 * 86400 if raw else (300 if result.get('reason') in {'service_unavailable', 'lookup_budget_exceeded'} else 86400), "result": result})
    except OSError:
        pass  # A cache is optional; never lose a fetched cover because it is unwritable.


def _album_cover(client, group, cache, now):
    """Share validated cover bytes across songs, never their recording identity.

    The edition and album group must both match. A different edition may have
    different artwork, even when its display album name is identical.
    """
    identity = {"releaseGroupId": group["id"], "releaseId": group["release"]["id"]}
    key = hashlib.sha256(json.dumps([POLICY_VERSION, identity], sort_keys=True).encode()).hexdigest()
    shared = cache / "albums" if cache is not None else None
    if shared is not None:
        try:
            shared.mkdir(parents=True, exist_ok=True)
        except OSError:
            shared = None
    cached = _cache_read(shared, key, now, max_age=30 * 86400)
    if cached:
        saved, raw = cached
        provenance = saved.get("provenance", {})
        if (saved.get("status") == "matched" and raw is not None
                and provenance.get("source") == "cover-art-archive"
                and all(provenance.get(field) == value for field, value in identity.items())):
            # Current song matching below owns recording IDs, album name/year
            # and match scope. Never restore those from another song's result.
            return raw, {field: provenance[field] for field in (
                "source", "sourceUrl", "imageUrl", "artworkReleaseId",
                "releaseGroupId", "releaseId", "imageHash", "imageEncoding") if field in provenance}
    raw, provenance = _cover(client, group)
    provenance.update({**identity, "imageHash": hashlib.sha256(raw).hexdigest(), "imageEncoding": COVER_ENCODING})
    _cache_write(shared, key, _result("matched", "album_cover", provenance=provenance), raw, now)
    return raw, provenance


def resolve_album_art(metadata: dict, directory: Path, cache_dir: Path | None = None, *, transport=None,
                      clock=time.monotonic, sleep=time.sleep, now=time.time) -> dict:
    """Return matched/unavailable/ambiguous; artwork never blocks song conversion.

    ``metadata`` accepts title, artist, optional album and recording ID, and an
    audioTitle corroborated by the selected source timing map. Album identity
    and image availability are independent outcomes (albumStatus and status).
    Ambiguous recordings may identify the same album; in that case provenance
    records all recordingIds and matchingScope='album', without inventing one.
    The returned file is a compact JPEG, at most 512 pixels on its longest edge,
    in the caller-owned directory. Existing PNG caches are compacted locally.
    Different songs reuse a cover only after independently matching the same
    release-group and release IDs; shared covers expire after 30 days.
    """
    cache, key, enrichment = None, None, {}
    try:
        if not isinstance(metadata, dict) or any(not isinstance(metadata.get(k), str) or not metadata[k].strip() or len(metadata[k]) > 300 for k in ("artist", "title")):
            return _result("unavailable", "metadata_missing")
        values = {key: str(metadata.get(key) or "").strip() for key in ("artist", "title", "album", "audioTitle")}
        if metadata.get("musicbrainzRecordingId"):
            identifier = str(metadata["musicbrainzRecordingId"]).lower()
            if not _UUID.fullmatch(identifier):
                return _result("unavailable", "metadata_invalid")
            values["musicbrainzRecordingId"] = identifier
        if len(values["album"]) > 300 or len(values["audioTitle"]) > 1000:
            return _result("unavailable", "metadata_invalid")
        key = hashlib.sha256(json.dumps([POLICY_VERSION, values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        cache = None
        if cache_dir:
            try:
                cache = Path(cache_dir)
                cache.mkdir(parents=True, exist_ok=True)
            except OSError:
                cache = None
        cached = _cache_read(cache, key, now)
        raw = None
        if cached:
            result, raw = cached
            enrichment = {k: result[k] for k in ('albumStatus', 'album', 'year', 'provenance') if k in result}
        else:
            client = _Client(transport, cache, clock, sleep, now)
            from .album_match import select_album
            group = select_album(client, values)
            records = list(group["records"].values())
            provenance = {"policyVersion": POLICY_VERSION, "recordingIds": [record["id"] for record in records],
                          "recordingId": records[0]["id"] if len(records) == 1 else None,
                          "matchingScope": "album", "audioRecordingVerified": False,
                          "matchBasis": "recording-id-artist-title-version-album-track" if values.get("musicbrainzRecordingId") else "artist-title-version-album-track",
                          "releaseGroupId": group["id"], "releaseId": group["release"]["id"],
                          "albumSelection": "supplied-album" if values["album"] else "original-official-release",
                          "releaseType": group["releaseType"], "lookupTitle": group["lookupTitle"],
                          "versions": group["versions"], "trackEvidence": group["trackEvidence"]}
            enrichment = {"albumStatus": "matched", "album": group["album"], "year": group["interval"][0].year,
                          "provenance": provenance}
            try:
                raw, image_provenance = _album_cover(client, group, cache, now)
                provenance.update(image_provenance)
                result = _result("matched", "album_cover", **enrichment)
            except _LookupFailure as exc:
                result = _result(exc.status, exc.reason, **enrichment)
                result["message"] = "Album identified. Its cover is currently unavailable; the song can still be converted."
            _cache_write(cache, key, result, raw, now)
        if raw is not None:
            destination = Path(directory)
            destination.mkdir(parents=True, exist_ok=True)
            image_path = destination / f"album-cover-{uuid.uuid4().hex}.jpg"
            with image_path.open("xb") as stream:
                stream.write(raw)
            return {**result, "path": str(image_path)}
        return result
    except _LookupFailure as exc:
        result = _result(exc.status, exc.reason)
        if cache is not None and key is not None:
            _cache_write(cache, key, result, None, now)
        return result
    except Exception:
        # Transport, malformed third-party records and local image writes are
        # optional enrichment failures, never a failed musical conversion.
        return _result("unavailable", "lookup_failed", **enrichment)
