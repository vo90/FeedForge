"""Audio acquisition and normalization for imports (no account/cookie discovery)."""
from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request

MAX_BYTES = 512 * 1024 * 1024
MAX_DURATION = 1200.0


class ImportFailure(ValueError):
    def __init__(self, code: str, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.code, self.diagnostics = code, diagnostics or {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ImportFailure("needs_audio", "Use an HTTP(S) audio link without embedded credentials.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("not public")
    except (ValueError, OSError) as exc:
        raise ImportFailure("needs_audio", "The audio link does not resolve to a public server.") from exc
    return url


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _public_url(newurl))


def _download_direct(url: str, target: Path) -> None:
    request = urllib.request.Request(_public_url(url), headers={"User-Agent": "FeedForge Song Import"})
    opener = urllib.request.build_opener(_PublicRedirect)
    try:
        with opener.open(request, timeout=30) as response, target.open("xb") as output:
            if "text/html" in response.headers.get("Content-Type", "").lower():
                raise ImportFailure("needs_audio", "This is a web page. Choose a direct audio link or local audio file.")
            length = int(response.headers.get("Content-Length", "0"))
            if length > MAX_BYTES:
                raise ImportFailure("needs_audio", "The audio download is too large.")
            total = 0
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ImportFailure("needs_audio", "The audio download is too large.")
                output.write(chunk)
    except ImportFailure:
        raise
    except (OSError, ValueError) as exc:
        raise ImportFailure("needs_audio", "The audio link could not be downloaded. Choose another link or a local file.") from exc


def _tool(tools: dict, name: str) -> str | None:
    value = tools.get(name)
    return str(value) if value and Path(str(value)).is_file() else shutil.which(name)


def _run(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=True,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError as exc:
        raise ImportFailure("dependency_missing", "An audio processing tool is missing.") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise ImportFailure("needs_audio", "Audio processing failed or timed out. Choose a readable audio file.") from exc


def _download_youtube(url: str, directory: Path, tools: dict) -> tuple[Path, dict]:
    # Only a selected video; never search, fetch a playlist, or discover browser cookies.
    if importlib.util.find_spec("yt_dlp") is None and not tools.get("ytDlp"):
        raise ImportFailure("dependency_missing", "YouTube audio requires the optional yt-dlp component.")
    ffmpeg = _tool(tools, "ffmpeg")
    if not ffmpeg:
        raise ImportFailure("dependency_missing", "YouTube audio requires FFmpeg.")
    template = str(directory / "download.%(ext)s")
    runtime = tools.get("jsRuntime")
    if tools.get("ytDlp"):
        command = [str(tools["ytDlp"]), "--ignore-config", "--no-playlist", "--no-progress",
                   "--max-filesize", str(MAX_BYTES), "--socket-timeout", "30", "--retries", "2",
                   "--ffmpeg-location", ffmpeg, "--write-info-json", "-f", "bestaudio/best", "-o", template]
        if runtime:
            command += ["--js-runtimes", str(runtime)]
        command += ["--", url]
        _run(command, 240)
        import json
        info = json.loads((directory / "download.info.json").read_text(encoding="utf-8"))
    else:
        import yt_dlp
        options = {"format": "bestaudio/best", "outtmpl": template, "noplaylist": True,
                   "quiet": True, "socket_timeout": 30, "retries": 2, "fragment_retries": 2,
                   "max_filesize": MAX_BYTES, "ffmpeg_location": ffmpeg,
                   "match_filter": lambda info, **_: "Recording is too long" if float(info.get("duration") or 0) > MAX_DURATION else None}
        if runtime:
            kind, _, runtime_path = str(runtime).partition(":")
            options["js_runtimes"] = {kind: {"path": runtime_path} if runtime_path else {}}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
        except Exception as exc:
            raise ImportFailure("needs_audio", "YouTube audio is unavailable. Choose another link or a local audio file.") from exc
    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
        raise ImportFailure("needs_audio", "Select one recording, not a playlist.")
    candidates = [p for p in directory.glob("download.*") if p.suffix not in {".json", ".part", ".ytdl"}]
    if len(candidates) != 1:
        raise ImportFailure("needs_audio", "The audio download did not produce one complete recording.")
    return candidates[0], {"kind": "youtube", "videoId": info.get("id"), "title": info.get("title"), "url": url}


def prepare_audio(audio: dict | None, directory: Path, tools: dict | None = None) -> dict:
    """Write full.ogg and preview.ogg in an owned, empty job directory."""
    import numpy as np
    import soundfile as sf

    tools = tools or {}
    if not audio:
        raise ImportFailure("needs_audio", "Choose an audio file or paste a recording link.")
    source = {"kind": audio.get("kind")}
    if audio.get("kind") == "file":
        input_path = Path(str(audio.get("path") or ""))
        if not input_path.is_file():
            raise ImportFailure("needs_audio", "The selected audio file does not exist.")
        source["filename"] = input_path.name
    elif audio.get("kind") == "url":
        url = str(audio.get("url") or "").strip()
        host = urllib.parse.urlsplit(url).hostname or ""
        _public_url(url)
        if host.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            input_path, source = _download_youtube(url, directory, tools)
        else:
            input_path = directory / "download.audio"
            _download_direct(url, input_path)
            source["url"] = url
    else:
        raise ImportFailure("needs_audio", "Choose a local audio file or an audio link.")
    if input_path.stat().st_size > MAX_BYTES:
        raise ImportFailure("needs_audio", "The audio file is too large.")
    source["sha256"] = sha256_file(input_path)
    decode_path = input_path
    try:
        sf.info(decode_path)
    except (RuntimeError, sf.LibsndfileError):
        ffmpeg = _tool(tools, "ffmpeg")
        if not ffmpeg:
            raise ImportFailure("dependency_missing", "This audio format requires FFmpeg. WAV, OGG or FLAC can also be used.")
        decode_path = directory / "decoded.wav"
        _run([ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(input_path), "-vn", "-t", str(MAX_DURATION + 1),
              "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(decode_path)])
    try:
        with sf.SoundFile(decode_path) as reader:
            duration = len(reader) / reader.samplerate
            if not 2.0 <= duration <= MAX_DURATION or not 1 <= reader.channels <= 2:
                raise ImportFailure("needs_audio", "Choose a mono or stereo recording between 2 seconds and 20 minutes.")
            target = directory / "full.ogg"
            peak = 0.0
            with sf.SoundFile(target, "w", samplerate=reader.samplerate, channels=reader.channels,
                              format="OGG", subtype="VORBIS") as writer:
                for block in reader.blocks(blocksize=32768, dtype="float32", always_2d=True):
                    if not np.isfinite(block).all():
                        raise ImportFailure("needs_audio", "The recording contains invalid audio samples.")
                    peak = max(peak, float(np.max(np.abs(block))))
                    writer.write(block)
            if peak < 1e-5:
                raise ImportFailure("needs_audio", "The selected recording is silent.")
        with sf.SoundFile(target) as reader:
            start = min(max(duration * 0.35, 0), max(duration - 30, 0))
            reader.seek(round(start * reader.samplerate))
            samples = reader.read(round(min(30.0, duration) * reader.samplerate), dtype="float32", always_2d=True)
            fade = min(len(samples) // 4, reader.samplerate)
            samples[:fade] *= np.linspace(0, 1, fade)[:, None]
            samples[-fade:] *= np.linspace(1, 0, fade)[:, None]
            preview = directory / "preview.ogg"
            # libsndfile's Vorbis encoder can exhaust the Windows stack for one
            # large write; keep both full audio and preview writes bounded.
            with sf.SoundFile(preview, "w", samplerate=reader.samplerate, channels=reader.channels,
                              format="OGG", subtype="VORBIS") as writer:
                for begin in range(0, len(samples), 32768):
                    writer.write(samples[begin:begin + 32768])
        return {"path": str(target), "previewPath": str(preview), "duration": duration,
                # Vorbis stream serial numbers are randomized by libsndfile.
                # Source bytes identify the recording for semantic reuse; the
                # encoded hash separately checks this particular output asset.
                "hash": source["sha256"], "encodedHash": sha256_file(target),
                "source": source, "previewStart": start}
    except ImportFailure:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ImportFailure("needs_audio", "The recording could not be decoded into playable audio.") from exc
