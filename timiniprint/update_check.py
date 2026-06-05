from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import __version__
from .app_settings import AppSettings

GITHUB_RELEASE_URL_BASE = "https://github.com/Dejniel/TiMini-Print/releases/tag"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/Dejniel/TiMini-Print/releases/latest"
UPDATE_SECTION = "update"
DEFAULT_CHECK_INTERVAL = timedelta(hours=24)
DEFAULT_TIMEOUT_SEC = 2.0


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    release_url: str
    from_cache: bool = False


@dataclass(frozen=True)
class ReleaseInfo:
    version: str


class UpdateChecker:
    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        current_version: str = __version__,
        fetch_latest_release: Callable[[float], ReleaseInfo] | None = None,
    ) -> None:
        self.settings = settings or AppSettings.default()
        self.current_version = current_version
        self.fetch_latest_release = fetch_latest_release or fetch_latest_release_from_github

    def check(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
        check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> UpdateCheckResult | None:
        now = _utc(now or datetime.now(UTC))
        try:
            settings = self.settings.load()
        except Exception:
            return None

        latest_version = settings.get(UPDATE_SECTION, "latest_version", fallback="").strip()
        cached = (
            UpdateCheckResult(self.current_version, latest_version, release_url_for_version(latest_version), from_cache=True)
            if latest_version and is_newer_version(latest_version, self.current_version)
            else None
        )

        last_checked_raw = settings.get(UPDATE_SECTION, "last_checked_at", fallback="").strip()
        last_checked = None
        if last_checked_raw:
            try:
                last_checked = _utc(datetime.fromisoformat(last_checked_raw.replace("Z", "+00:00")))
            except ValueError:
                last_checked = None
        cache_complete = bool(latest_version and last_checked)
        if not force and cache_complete and now - last_checked < check_interval:
            return cached

        if not settings.has_section(UPDATE_SECTION):
            settings.add_section(UPDATE_SECTION)
        settings.set(UPDATE_SECTION, "last_checked_at", now.isoformat().replace("+00:00", "Z"))
        try:
            latest = self.fetch_latest_release(timeout_sec)
        except Exception:
            try:
                self.settings.save(settings)
            except Exception:
                pass
            return cached

        settings.set(UPDATE_SECTION, "latest_version", latest.version)
        try:
            self.settings.save(settings)
        except Exception:
            pass

        if is_newer_version(latest.version, self.current_version):
            return UpdateCheckResult(
                current_version=self.current_version,
                latest_version=latest.version,
                release_url=release_url_for_version(latest.version),
                from_cache=False,
            )
        return None


def should_check_for_updates(*, source_builds: bool = False) -> bool:
    if os.environ.get("TIMINIPRINT_NO_UPDATE_CHECK"):
        return False
    if os.environ.get("TIMINIPRINT_UPDATE_CHECK"):
        return True
    return source_builds or bool(getattr(sys, "frozen", False))


def check_for_updates(
    *,
    current_version: str = __version__,
    settings_path: Path | None = None,
    now: datetime | None = None,
    force: bool = False,
    check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    fetch_latest_release: Callable[[float], ReleaseInfo] | None = None,
) -> UpdateCheckResult | None:
    settings = None if settings_path is None else AppSettings(settings_path)
    return UpdateChecker(
        settings=settings,
        current_version=current_version,
        fetch_latest_release=fetch_latest_release,
    ).check(
        now=now,
        force=force,
        check_interval=check_interval,
        timeout_sec=timeout_sec,
    )


def fetch_latest_release_from_github(timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> ReleaseInfo:
    request = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={"User-Agent": f"TiMini-Print/{__version__}"},
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = str(payload.get("tag_name") or "").strip()
    if not version:
        raise RuntimeError("Latest release response is missing tag_name")
    return ReleaseInfo(version=version)


def release_url_for_version(version: str) -> str:
    return f"{GITHUB_RELEASE_URL_BASE}/{version}"


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_match = re.search(r"\d+(?:\.\d+)*", candidate)
    current_match = re.search(r"\d+(?:\.\d+)*", current)
    candidate_parts = tuple(int(part) for part in candidate_match.group(0).split(".")) if candidate_match else (0,)
    current_parts = tuple(int(part) for part in current_match.group(0).split(".")) if current_match else (0,)
    max_len = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (max_len - len(candidate_parts)) > current_parts + (0,) * (max_len - len(current_parts))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
