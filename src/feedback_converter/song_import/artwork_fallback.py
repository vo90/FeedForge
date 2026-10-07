"""Original-song cover fallback; never substitutes recording metadata or audio."""
from .artwork import _LookupFailure, _search_title, _result, _matched_album_cover
from .album_match import _audio_context, _context_versions, select_album


def original_song_cover(client, values, specific, cache, now):
    # Retry semantic misses only. Do not hide identity ambiguity, malformed
    # responses, pinned recording conflicts, or exhausted shared lookup budgets.
    eligible = {"recording_not_found", "album_not_found", "cover_not_available",
                "invalid_cover_image", "response_too_large"}
    if specific.get("albumStatus") == "matched":
        eligible.add("service_unavailable")  # Image failure after verified album identity.
    if specific["reason"] not in eligible or values.get("musicbrainzRecordingId"):
        return specific, None
    names = {values["artist"]}
    audio = _audio_context(values, names, _search_title(values["title"]))
    versions, _ = _context_versions(values["title"], audio, names)
    if not versions and not values["album"]:
        return specific, None
    title = _search_title(values["title"])
    if not title:
        return specific, None
    original = {"artist": values["artist"], "title": title, "album": "", "audioTitle": ""}
    # Same artist, conservative version-suffix removal, and the full ordinary
    # album/track proof. No guessed cover performer or fuzzy title search.
    try:
        group = select_album(client, original)
        cover, raw = _matched_album_cover(client, group, original, cache, now)
    except _LookupFailure as exc:
        cover, raw = _result(exc.status, exc.reason), None
    if raw is None:
        return {**specific, "provenance": {**specific["provenance"],
                "originalSongArtworkAttempt": {"status": cover["status"], "reason": cover["reason"]}}}, None
    # Keep any independently established specific album enrichment. The fallback
    # album/year live only in provenance and cannot populate the feedpak metadata.
    enrichment = {k: specific[k] for k in ("albumStatus", "album", "year") if k in specific}
    proof = {**cover["provenance"], "album": cover["album"], "year": cover["year"],
             "artist": original["artist"]}
    provenance = {**cover["provenance"], "matchingScope": "original-song-artwork",
                  "audioRecordingVerified": False, "originalSongArtwork": proof,
                  "specificArtwork": {"status": specific["status"], "reason": specific["reason"],
                                      "provenance": specific["provenance"]}}
    result = _result("matched", "original_song_album_cover", provenance=provenance, **enrichment)
    result["message"] = "Using original-song album artwork; a cover for this version was not found."
    return result, raw
