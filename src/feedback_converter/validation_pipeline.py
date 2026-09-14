from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping, TypeAlias

from . import feedpak_validator
from .feedpak_validator import FeedpakValidationIssue, FeedpakValidationResult


MAX_VALIDATION_WORKERS = 2
DEFAULT_POLL_INTERVAL_SECONDS = 0.05


ValidationResultData: TypeAlias = dict[str, object]
WorkerValidationPayload: TypeAlias = tuple[ValidationResultData, str | None]
LegacyWorkerValidationPayload: TypeAlias = tuple[
    bool,
    tuple[str, ...],
    tuple[str, ...],
    str | None,
]


@dataclass(frozen=True)
class PathValidationResult:
    """Serializable validation outcome for one absolute package path."""

    path: str
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    worker_error: str | None = None
    issues: tuple[FeedpakValidationIssue, ...] = ()

    def as_feedpak_result(self) -> FeedpakValidationResult:
        return FeedpakValidationResult(
            ok=self.ok,
            errors=list(self.errors),
            warnings=list(self.warnings),
            issues=list(self.issues),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "worker_error": self.worker_error,
        }


class ValidationCancelled(RuntimeError):
    """Raised after cancellation, carrying results completed before the request."""

    def __init__(self, completed: Iterable[PathValidationResult] = ()) -> None:
        self.completed = tuple(completed)
        super().__init__("FeedPak validation was cancelled")


def resolve_validation_worker_count(requested: int | None) -> int:
    """Keep validation deliberately small because each process is memory-heavy."""

    if requested is None:
        return MAX_VALIDATION_WORKERS
    try:
        value = int(requested)
    except (TypeError, ValueError) as exc:
        raise ValueError("validation workers must be an integer") from exc
    return max(1, min(MAX_VALIDATION_WORKERS, value))


class FeedpakValidationPipeline:
    """Bounded, reusable process pipeline for staged FeedPak directories.

    Only absolute path strings cross into child processes. Workers return plain
    dictionaries so structured issue data is spawn-safe. Results are retained in
    submission order even though workers may finish in a different order.
    """

    def __init__(
        self,
        *,
        workers: int | None = MAX_VALIDATION_WORKERS,
        max_pending: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.workers = resolve_validation_worker_count(workers)
        default_pending = self.workers * 2
        self.max_pending = max(1, int(max_pending if max_pending is not None else default_pending))
        self.cancel_check = cancel_check
        self.poll_interval = max(0.01, float(poll_interval))

        self._executor: ProcessPoolExecutor | None = None
        self._pending: dict[
            Future[WorkerValidationPayload | LegacyWorkerValidationPayload],
            int,
        ] = {}
        self._paths: list[str] = []
        self._results: dict[int, PathValidationResult] = {}
        self._broken_reason: str | None = None
        self._closed = False
        self._finished = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def submitted_count(self) -> int:
        return len(self._paths)

    def __enter__(self) -> FeedpakValidationPipeline:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(cancel=exc is not None)

    def submit(self, package: str | os.PathLike[str]) -> int:
        """Submit one path and return its stable, zero-based result index."""

        if self._closed or self._finished:
            raise RuntimeError("validation pipeline is already closed")
        self._raise_if_cancelled()
        while len(self._pending) >= self.max_pending:
            self._wait_for_results(return_when=FIRST_COMPLETED)

        path = str(Path(package).resolve(strict=False))
        index = len(self._paths)
        self._paths.append(path)

        if self._broken_reason is not None:
            self._results[index] = _worker_failure_result(path, self._broken_reason)
            return index

        try:
            executor = self._ensure_executor()
            future = executor.submit(_validate_path_worker, path)
        except Exception as exc:  # a broken pool must become a per-path result
            message = _worker_failure_message(exc)
            if isinstance(exc, BrokenProcessPool):
                self._broken_reason = message
            self._results[index] = _worker_failure_result(path, message)
        else:
            self._pending[future] = index
        return index

    def finish(self) -> tuple[PathValidationResult, ...]:
        """Wait for submitted work and return results in submission order."""

        if self._finished:
            return self._ordered_results()
        if self._closed:
            raise RuntimeError("validation pipeline is already closed")
        while self._pending:
            self._wait_for_results(return_when=FIRST_COMPLETED)
        self._finished = True
        self.close()
        return self._ordered_results()

    def close(self, *, cancel: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if cancel:
            for future in self._pending:
                future.cancel()
        executor = self._executor
        self._executor = None
        if executor is not None:
            # At most ``max_pending`` jobs exist. Waiting here prevents orphaned
            # validator children; queued jobs are cancelled on a stop request.
            executor.shutdown(wait=True, cancel_futures=cancel)
        self._pending.clear()

    def _ensure_executor(self) -> ProcessPoolExecutor:
        if self._executor is None:
            # Harmless for source runs and required by frozen Windows builds.
            multiprocessing.freeze_support()
            self._executor = ProcessPoolExecutor(
                max_workers=self.workers,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self._executor

    def _wait_for_results(self, *, return_when: str) -> None:
        while self._pending:
            self._raise_if_cancelled()
            done, _ = wait(
                tuple(self._pending),
                timeout=self.poll_interval,
                return_when=return_when,
            )
            if not done:
                continue
            self._harvest_futures(done)
            return

    def _harvest_futures(
        self,
        futures: Iterable[
            Future[WorkerValidationPayload | LegacyWorkerValidationPayload]
        ],
    ) -> None:
        for future in futures:
            index = self._pending.pop(future)
            path = self._paths[index]
            try:
                validation, worker_error = _decode_worker_payload(future.result())
            except Exception as exc:
                message = _worker_failure_message(exc)
                if isinstance(exc, BrokenProcessPool):
                    self._broken_reason = message
                self._results[index] = _worker_failure_result(path, message)
            else:
                self._results[index] = PathValidationResult(
                    path=path,
                    ok=validation.ok,
                    errors=tuple(validation.errors),
                    warnings=tuple(validation.warnings),
                    issues=tuple(validation.issues),
                    worker_error=worker_error,
                )

    def _raise_if_cancelled(self) -> None:
        if self.cancel_check is None:
            return
        try:
            cancelled = bool(self.cancel_check())
        except Exception as exc:
            raise RuntimeError(f"validation cancellation check failed: {exc}") from exc
        if cancelled:
            self._harvest_futures(
                future for future in tuple(self._pending) if future.done()
            )
            completed = self._ordered_results(complete_only=True)
            self.close(cancel=True)
            raise ValidationCancelled(completed)

    def _ordered_results(self, *, complete_only: bool = False) -> tuple[PathValidationResult, ...]:
        if complete_only:
            return tuple(self._results[index] for index in sorted(self._results))
        missing = [index for index in range(len(self._paths)) if index not in self._results]
        if missing:
            raise RuntimeError(f"validation pipeline lost {len(missing)} result(s)")
        return tuple(self._results[index] for index in range(len(self._paths)))


def validate_feedpak_paths(
    packages: Iterable[str | os.PathLike[str]],
    *,
    workers: int | None = MAX_VALIDATION_WORKERS,
    max_pending: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[PathValidationResult, ...]:
    """Validate package paths with bounded process-level parallelism."""

    with FeedpakValidationPipeline(
        workers=workers,
        max_pending=max_pending,
        cancel_check=cancel_check,
    ) as pipeline:
        for package in packages:
            pipeline.submit(package)
        return pipeline.finish()


_WORKER_INITIALIZED = False


def _prepare_validation_worker() -> None:
    """Cache immutable schema validators once per long-lived child process."""

    global _WORKER_INITIALIZED
    if _WORKER_INITIALIZED:
        return
    schema_loader = feedpak_validator._schema_validator
    if not getattr(schema_loader, "_feedforge_process_cache", False):
        cached_loader = lru_cache(maxsize=None)(schema_loader)
        setattr(cached_loader, "_feedforge_process_cache", True)
        feedpak_validator._schema_validator = cached_loader
    _WORKER_INITIALIZED = True


def _validate_path_worker(
    package_path: str,
) -> WorkerValidationPayload:
    """Spawn-safe worker entry point; accepts and returns only primitive data."""

    try:
        _prepare_validation_worker()
        result = feedpak_validator.validate_feedpak(Path(package_path))
        return result.to_dict(), None
    except Exception as exc:  # protect the pool from an unexpected validator bug
        message = _worker_failure_message(exc)
        result = FeedpakValidationResult(ok=False, errors=[message])
        return result.to_dict(), message


def _decode_worker_payload(
    payload: WorkerValidationPayload | LegacyWorkerValidationPayload,
) -> tuple[FeedpakValidationResult, str | None]:
    """Rebuild a validation result while accepting the pre-issue payload shape."""

    if len(payload) == 2 and isinstance(payload[0], Mapping):
        result_data, worker_error = payload
        return FeedpakValidationResult.from_dict(dict(result_data)), worker_error

    if len(payload) == 4:
        ok, errors, warnings, worker_error = payload
        return (
            FeedpakValidationResult(
                ok=bool(ok),
                errors=list(errors),
                warnings=list(warnings),
            ),
            worker_error,
        )

    raise ValueError("validation worker returned an unsupported result payload")


def _worker_failure_message(error: BaseException) -> str:
    detail = str(error).strip() or "no details were provided"
    return f"validation worker failed ({type(error).__name__}): {detail}"


def _worker_failure_result(path: str, message: str) -> PathValidationResult:
    return PathValidationResult(
        path=path,
        ok=False,
        errors=(message,),
        worker_error=message,
    )
