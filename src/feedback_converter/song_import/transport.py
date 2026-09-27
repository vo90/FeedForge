"""Bounded, redacted retrieval failure facts shared with the desktop scheduler."""
from __future__ import annotations

import errno
import http.client
import re
import socket
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RETRY_HTTP = {408, 429, 500, 502, 503, 504}


def retry_after(value):
    try:
        if not isinstance(value, str) or len(value) > 128 or not value.strip():
            return None
        now = datetime.now(timezone.utc).timestamp()
        at = now + int(value) if value.strip().isdigit() else parsedate_to_datetime(value).timestamp()
        return int(at * 1000) if at > now else None
    except (ValueError, TypeError, OverflowError):
        return None


def classify(error, *, service="audio_host", downloader=False):
    """Never persist exception text, signed URLs, paths, cookies or headers."""
    fact = {"version": 1, "phase": "audio", "operation": "audio_download", "service": service}
    # Local IO errors must not be confused with interrupted network transfers.
    if isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EACCES, errno.EPERM, errno.ENOENT}:
        return None
    if isinstance(error, urllib.error.HTTPError):
        if error.code not in RETRY_HTTP:
            return None
        fact.update(reason="http", status=error.code)
        at = retry_after(error.headers.get("Retry-After") if error.headers else None)
        if at:
            fact["retryAfterAt"] = at
        return fact
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        return classify(error.reason, service=service)
    if isinstance(error, socket.gaierror):
        reason = "temporary_dns" if error.errno == socket.EAI_AGAIN else None
    elif isinstance(error, (TimeoutError, socket.timeout)):
        reason = "timeout"
    elif isinstance(error, ConnectionResetError):
        reason = "connection_reset"
    elif isinstance(error, http.client.IncompleteRead):
        reason = "interrupted_transfer"
    else:
        reason = None
    if reason:
        return {**fact, "reason": reason}
    if not downloader:
        return None
    # yt-dlp sometimes only supplies a DownloadError/string. Recognize its
    # media-download message, not an arbitrary access-denied response.
    message = str(error)[-8000:].lower()
    if re.search(r"video (?:is )?(?:unavailable|private)|private video|sign in|sign-in|age.restrict|confirm.you.re|captcha|no space|permission denied|postprocess", message):
        return None
    if re.search(r"unable to download video data:.*http error 403\b", message):
        return {**fact, "reason": "media_url_expired", "status": 403}
    status = re.search(r"http error (408|429|500|502|503|504)\b", message)
    if status:
        return {**fact, "reason": "http", "status": int(status[1])}
    for expression, reason in [(r"(?:read |connection |connect )?timed out|readtimeout|connecttimeout", "timeout"),
                               (r"connection reset by peer|winerror 10054", "connection_reset"),
                               (r"temporary failure in name resolution", "temporary_dns"),
                               (r"incompleteread|\d+ bytes read, \d+ more expected|downloaded .* bytes, expected", "interrupted_transfer")]:
        if re.search(expression, message):
            return {**fact, "reason": reason}
    return None
