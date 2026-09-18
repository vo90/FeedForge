"""Portable runtime discovery; no downloads or machine-wide configuration changes."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import sys


def resolve_tools(overrides: dict | None = None) -> dict:
    result = dict(overrides or {})
    directory = Path(__file__).resolve().parents[1] / "tools"
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "ffprobe", "node"):
        bundled = directory / (name + suffix)
        located = str(bundled) if bundled.is_file() else (None if getattr(sys, "frozen", False) else shutil.which(name))
        if name == "node":
            if located:
                result.setdefault("jsRuntime", "node:" + located)
        elif located:
            result.setdefault(name, located)
    return result


def runtime_health() -> dict:
    tools = resolve_tools()
    modules = {}
    for module, distribution in (("numpy", "numpy"), ("soundfile", "soundfile"), ("yt_dlp", "yt-dlp"), ("yt_dlp_ejs", "yt-dlp-ejs")):
        present = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if present else None
        except importlib.metadata.PackageNotFoundError:
            version = "bundled" if present else None
        modules[module] = {"available": present, "version": version}
    available = {name: bool(tools.get(name)) for name in ("ffmpeg", "ffprobe", "jsRuntime")}
    return {"ok": all(item["available"] for item in modules.values()) and all(available.values()),
            "modules": modules, "tools": available, "portable": bool(getattr(sys, "frozen", False))}
