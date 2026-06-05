from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "TiMini-Print"
SETTINGS_FILENAME = "settings.ini"


@dataclass(frozen=True)
class AppSettings:
    path: Path

    @classmethod
    def default(cls) -> "AppSettings":
        return cls(default_app_settings_path())

    def load(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read(self.path, encoding="utf-8")
        return parser

    def save(self, parser: configparser.ConfigParser) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            parser.write(handle)


def default_app_settings_path() -> Path:
    from platformdirs import user_config_path

    return user_config_path(APP_NAME, appauthor=False) / SETTINGS_FILENAME
