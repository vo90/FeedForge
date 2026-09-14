from __future__ import annotations

import json
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .feedpak_semantics import (
    validate_arrangement_semantics,
    validate_manifest_semantics,
    validate_timeline_semantics,
)


SCHEMA_DIR = Path(__file__).resolve().parent / "data" / "feedpak_schemas"
SUPPORTED_MAJOR = 1
SIDE_FILE_SCHEMAS = {
    "lyrics": "lyrics.schema.json",
    "vocal_pitch": "vocal-pitch.schema.json",
    "song_timeline": "song-timeline.schema.json",
    "drum_tab": "drum-tab.schema.json",
    "vocal_pitch_contour": "vocal-pitch-contour.schema.json",
    "keys": "keys.schema.json",
    "harmony": "harmony.schema.json",
    "rigs": "rigs.schema.json",
}
NON_JSON_POINTERS = ("cover", "preview")
JSONC_STRIP_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|'
    r"//.*|"
    r"/\*[\s\S]*?\*/",
)

ISSUE_CATEGORY_PACKAGE = "package"
ISSUE_CATEGORY_CHART_SEMANTIC = "chart-semantic"
ISSUE_SEVERITY_ERROR = "error"
ISSUE_SEVERITY_WARNING = "warning"

# Only these reviewed chart incompatibilities may pass the package-safety gate.
# Keep this allowlist deliberately narrow: new semantic checks must remain
# blocking until FeedBack's tolerance for them has been established.
_PUBLISHABLE_CHART_SEMANTIC_PATTERNS = (
    (
        "chart.timeline.beat-order",
        re.compile(r"^.+: beats: time values must be in strictly increasing order$"),
    ),
    (
        "chart.timeline.downbeat-progression",
        re.compile(
            r"^.+: beats/\d+/measure: (?:"
            r"expected downbeat \d+, got -?\d+|"
            r"must be -1 or a 1-based downbeat number"
            r")$"
        ),
    ),
    (
        "chart.timeline.phrase-order",
        re.compile(
            r"^.+: phrases: start_time values must be in non-descending order$"
        ),
    ),
    (
        "chart.timeline.section-order",
        re.compile(r"^.+: sections: time values must be in non-descending order$"),
    ),
    (
        "chart.fret.out-of-range",
        re.compile(r"^.+/f: must be between 0 and 24$"),
    ),
    (
        "chart.template-fret.out-of-range",
        re.compile(r"^.+/frets/\d+: must be between -1 and 24$"),
    ),
    (
        "chart.handshape.reversed-span",
        re.compile(r"^.+handshapes/\d+: end_time must be >= start_time$"),
    ),
    (
        "chart.slide-destination.out-of-range",
        re.compile(r"^.+/sl(?:u)?: must be between -1 and 24$"),
    ),
)


@dataclass(frozen=True)
class FeedpakValidationIssue:
    """One classified validator finding.

    ``blocking`` is the package-publication decision. ``severity`` retains the
    strict validator's error/warning meaning, so a reviewed chart issue can be a
    strict error while still being nonblocking for tolerant conversion.
    """

    code: str
    message: str
    category: str
    severity: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "severity": self.severity,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedpakValidationIssue:
        return cls(
            code=str(data.get("code", "validation.unclassified")),
            message=str(data.get("message", "")),
            category=str(data.get("category", ISSUE_CATEGORY_PACKAGE)),
            severity=str(data.get("severity", ISSUE_SEVERITY_ERROR)),
            # A missing classification must fail closed.
            blocking=data.get("blocking") is not False,
        )


@dataclass(frozen=True)
class FeedpakValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[FeedpakValidationIssue] = field(default_factory=list)

    @property
    def blocking_errors(self) -> list[str]:
        """Return strict errors that make publishing the package unsafe.

        Legacy or partially classified results fail closed: an error without a
        matching issue is considered blocking.
        """

        blocking = Counter(
            issue.message
            for issue in self.issues
            if issue.severity == ISSUE_SEVERITY_ERROR and issue.blocking
        )
        nonblocking = Counter(
            issue.message
            for issue in self.issues
            if issue.severity == ISSUE_SEVERITY_ERROR and not issue.blocking
        )
        result: list[str] = []
        for message in self.errors:
            if blocking[message]:
                blocking[message] -= 1
                result.append(message)
            elif nonblocking[message]:
                nonblocking[message] -= 1
            else:
                result.append(message)

        # A malformed reconstructed result must not become publishable merely
        # because its blocking issue was omitted from ``errors``.
        for message, count in blocking.items():
            result.extend([message] * count)
        return result

    @property
    def nonblocking_errors(self) -> list[str]:
        """Return reviewed chart errors that tolerant conversion may publish."""

        remaining = Counter(self.errors)
        result: list[str] = []
        for issue in self.issues:
            if (
                issue.severity == ISSUE_SEVERITY_ERROR
                and not issue.blocking
                and remaining[issue.message]
            ):
                remaining[issue.message] -= 1
                result.append(issue.message)
        return result

    @property
    def publishable(self) -> bool:
        """Whether the package has no structural or unclassified failures."""

        # A contradictory/legacy result must not become publishable merely
        # because it omitted the error details that made ``ok`` false.
        if not self.ok and not self.errors:
            return False
        return not self.blocking_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "publishable": self.publishable,
            "blocking_errors": self.blocking_errors,
            "nonblocking_errors": self.nonblocking_errors,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedpakValidationResult:
        errors = [str(message) for message in data.get("errors", [])]
        warnings = [str(message) for message in data.get("warnings", [])]
        raw_issues = data.get("issues", [])
        issues = [
            FeedpakValidationIssue.from_dict(issue)
            for issue in raw_issues
            if isinstance(issue, dict)
        ]
        return cls(
            ok=bool(data.get("ok", not errors)),
            errors=errors,
            warnings=warnings,
            issues=issues,
        )


class FeedpakValidationError(ValueError):
    def __init__(self, result: FeedpakValidationResult) -> None:
        self.result = result
        details = "; ".join(result.errors[:4])
        if len(result.errors) > 4:
            details += f" (+{len(result.errors) - 4} more)"
        super().__init__(f"FeedPak spec validation failed: {details}")


class _Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.issues: list[FeedpakValidationIssue] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def err(
        self,
        message: str,
        *,
        code: str = "validation.unclassified",
        category: str = ISSUE_CATEGORY_PACKAGE,
        blocking: bool = True,
    ) -> None:
        self.errors.append(message)
        self.issues.append(
            FeedpakValidationIssue(
                code=code,
                message=message,
                category=category,
                severity=ISSUE_SEVERITY_ERROR,
                blocking=blocking,
            )
        )

    def warn(
        self,
        message: str,
        *,
        code: str = "validation.warning",
        category: str = ISSUE_CATEGORY_PACKAGE,
    ) -> None:
        self.warnings.append(message)
        self.issues.append(
            FeedpakValidationIssue(
                code=code,
                message=message,
                category=category,
                severity=ISSUE_SEVERITY_WARNING,
                blocking=False,
            )
        )

    def chart_semantic_err(self, message: str) -> None:
        code = _publishable_chart_semantic_code(message)
        self.err(
            message,
            code=code or "chart.semantic.unclassified",
            category=ISSUE_CATEGORY_CHART_SEMANTIC,
            blocking=code is None,
        )


def _publishable_chart_semantic_code(message: str) -> str | None:
    for code, pattern in _PUBLISHABLE_CHART_SEMANTIC_PATTERNS:
        if pattern.match(message):
            return code
    return None


def validate_feedpak(package: Path) -> FeedpakValidationResult:
    """Validate a FeedPak directory or zip against the official FeedPak schemas."""
    package = Path(package)
    report = _Report()
    try:
        if package.is_dir():
            _validate_dir(package, report)
        elif package.is_file() and zipfile.is_zipfile(package):
            with tempfile.TemporaryDirectory(prefix="feedforge-validate-feedpak-") as temp:
                root = Path(temp) / "package.feedpak"
                with zipfile.ZipFile(package) as zf:
                    for name in zf.namelist():
                        if name.startswith("/") or ".." in Path(name).parts or "\\" in name or ":" in name:
                            report.err(f"unsafe path inside archive: {name}")
                    if report.ok:
                        zf.extractall(root)
                        _validate_dir(root, report)
        else:
            report.err("not a FeedPak directory or zip archive")
    except Exception as exc:  # noqa: BLE001
        report.err(f"validation failed: {exc}")
    return FeedpakValidationResult(
        ok=report.ok,
        errors=report.errors,
        warnings=report.warnings,
        issues=report.issues,
    )


def require_valid_feedpak(package: Path) -> FeedpakValidationResult:
    """Return the validation report or raise with concise, user-facing details."""
    result = validate_feedpak(package)
    if not result.ok:
        raise FeedpakValidationError(result)
    return result


def require_publishable_feedpak(package: Path) -> FeedpakValidationResult:
    """Return a report when only reviewed, nonblocking chart errors exist."""

    result = validate_feedpak(package)
    if not result.publishable:
        raise FeedpakValidationError(result)
    return result


def _schema_validator(name: str) -> Draft202012Validator:
    with (SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return Draft202012Validator(json.load(fh))


def _semver_pattern() -> re.Pattern[str]:
    with (SCHEMA_DIR / "manifest.schema.json").open(encoding="utf-8") as fh:
        pattern = json.load(fh)["$defs"]["semver"]["pattern"]
    return re.compile(pattern)


SEMVER_RE = _semver_pattern()


def _validate_dir(root: Path, report: _Report) -> None:
    manifest_path = root / "manifest.yaml"
    if not _within_root(root, manifest_path):
        report.err("manifest.yaml escapes the package root")
        return
    if not manifest_path.is_file():
        report.err("no manifest.yaml at package root")
        return
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        report.err(f"manifest.yaml: not valid YAML ({exc})")
        return
    if not isinstance(manifest, dict):
        report.err("manifest.yaml: top level must be a mapping")
        return

    error_count = len(report.errors)
    _validate_object(manifest, _schema_validator("manifest.schema.json"), "manifest.yaml", report)
    if len(report.errors) == error_count:
        validate_manifest_semantics(manifest, "manifest.yaml", report.err)
    feedpak_version = manifest.get("feedpak_version", "1.0.0")
    if not isinstance(feedpak_version, str) or not SEMVER_RE.match(feedpak_version):
        report.err(f"feedpak_version is not a valid semver string: {feedpak_version!r}")
    else:
        major = int(feedpak_version.split(".")[0])
        if major > SUPPORTED_MAJOR:
            report.warn(
                f"feedpak_version {feedpak_version} has major {major} > supported {SUPPORTED_MAJOR}; "
                "this validator may not understand it"
            )

    arrangement_validator = _schema_validator("arrangement.schema.json")
    notation_validator = _schema_validator("notation.schema.json")
    for index, arrangement in enumerate(manifest.get("arrangements", []) or []):
        if not isinstance(arrangement, dict):
            continue
        file_name = arrangement.get("file")
        if file_name is not None and _check_pointer(root, file_name, f"arrangements[{index}].file", report):
            error_count = len(report.errors)
            data = _validate_json_file(root, file_name, arrangement_validator, report)
            if isinstance(data, dict) and len(report.errors) == error_count:
                _validate_phrase_level_shapes(data, arrangement_validator, file_name, report)
            if isinstance(data, dict) and len(report.errors) == error_count:
                validate_arrangement_semantics(
                    data,
                    arrangement,
                    file_name,
                    report.chart_semantic_err,
                )
        notation = arrangement.get("notation")
        if notation is not None and _check_pointer(root, notation, f"arrangements[{index}].notation", report):
            _validate_json_file(root, notation, notation_validator, report)

    for index, stem in enumerate(manifest.get("stems", []) or []):
        if isinstance(stem, dict) and "file" in stem:
            _check_pointer(root, stem["file"], f"stems[{index}].file", report)

    for key, schema_name in SIDE_FILE_SCHEMAS.items():
        relpath = manifest.get(key)
        if relpath is not None and _check_pointer(root, relpath, key, report):
            error_count = len(report.errors)
            data = _validate_json_file(root, relpath, _schema_validator(schema_name), report)
            if key == "song_timeline" and isinstance(data, dict) and len(report.errors) == error_count:
                validate_timeline_semantics(data, relpath, report.chart_semantic_err)

    for key in NON_JSON_POINTERS:
        relpath = manifest.get(key)
        if relpath is not None:
            _check_pointer(root, relpath, key, report)


def _validate_object(data: Any, validator: Draft202012Validator, label: str, report: _Report) -> None:
    for error in sorted(validator.iter_errors(data), key=lambda err: list(err.path)):
        location = "/".join(str(item) for item in error.path) or "<root>"
        report.err(f"{label}: {location}: {error.message}")


def _validate_phrase_level_shapes(
    data: dict[str, Any],
    validator: Draft202012Validator,
    label: str,
    report: _Report,
) -> None:
    """Apply the arrangement's event schemas to the otherwise-open phrase levels."""
    event_keys = ("notes", "chords", "anchors", "handshapes")
    for phrase_index, phrase in enumerate(data.get("phrases", [])):
        if not isinstance(phrase, dict):
            continue
        for level_index, level in enumerate(phrase.get("levels", [])):
            level_label = f"{label}: phrases/{phrase_index}/levels/{level_index}"
            if not isinstance(level, dict):
                _validate_object(level, validator, level_label, report)
                continue
            # Validate only the known event collections. Other level keys are
            # extensions and must not inherit unrelated top-level constraints.
            known_shape = {key: level[key] for key in event_keys if key in level}
            _validate_object(known_shape, validator, level_label, report)


def _validate_json_file(
    root: Path, relpath: str, validator: Draft202012Validator, report: _Report
) -> Any | None:
    target = root / relpath
    if not target.is_file():
        report.err(f"missing file referenced by manifest: {relpath}")
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        data = _parse_jsonc(raw) if relpath.endswith(".jsonc") else json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        kind = "not valid JSON after JSONC comment-stripping" if relpath.endswith(".jsonc") else "not valid JSON"
        report.err(f"{relpath}: {kind} ({exc})")
        return None
    _validate_object(data, validator, relpath, report)
    return data


def _parse_jsonc(text: str) -> object:
    def strip(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.startswith('"'):
            return value
        if value.startswith("/*"):
            return "\n" * value.count("\n") if "\n" in value else " "
        return ""

    return json.loads(JSONC_STRIP_RE.sub(strip, text))


def _check_pointer(root: Path, relpath: str, key: str, report: _Report) -> bool:
    if not _safe_relpath(relpath):
        report.err(f"manifest '{key}' is not a safe relative path: {relpath!r}")
        return False
    target = root / relpath
    if not _within_root(root, target):
        report.err(f"manifest '{key}' escapes the package root: {relpath}")
        return False
    if not target.resolve().is_file():
        report.err(f"missing file referenced by manifest '{key}': {relpath}")
        return False
    return True


def _safe_relpath(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if path.startswith("/") or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    return ".." not in parts and "" not in parts[:-1]


def _within_root(root: Path, target: Path) -> bool:
    root_real = root.resolve()
    target_real = target.resolve()
    return root_real == target_real or root_real in target_real.parents
