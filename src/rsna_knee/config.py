"""Small immutable configuration; no credential or environment discovery."""

from dataclasses import asdict, dataclass, fields, replace
import json
import os
from pathlib import Path
import stat
from typing import Mapping


class ConfigError(ValueError):
    """Configuration cannot safely represent the requested preflight."""


@dataclass(frozen=True)
class AuditConfig:
    input_base: str = "data"
    output_dir: str = "artifacts"
    data_root: str | None = None
    inspect_csv: bool = False
    max_depth: int = 2
    max_directories: int = 128
    max_entries: int = 2048
    max_file_bytes: int = 8 * 1024 * 1024
    max_csv_rows: int = 100000
    max_report_bytes: int = 1024 * 1024

    def __post_init__(self):
        for name in ("input_base", "output_dir", "data_root"):
            value = getattr(self, name)
            if value is None and name == "data_root":
                continue
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ConfigError(f"{name} must be a nonempty unpadded path string")
            if any(ord(character) < 32 for character in value):
                raise ConfigError(f"{name} contains a control character")
        if type(self.inspect_csv) is not bool:
            raise ConfigError("inspect_csv must be a JSON boolean")
        limits = {
            "max_depth": (0, 4), "max_directories": (1, 1024),
            "max_entries": (1, 65536), "max_file_bytes": (1, 64 * 1024 * 1024),
            "max_csv_rows": (1, 1000000), "max_report_bytes": (1, 4 * 1024 * 1024),
        }
        for name, (lower, upper) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ConfigError(f"{name} must be an integer in [{lower}, {upper}]")

    @classmethod
    def from_dict(cls, values: Mapping):
        if not isinstance(values, Mapping):
            raise ConfigError("Configuration must be a JSON object")
        unknown = set(values) - {field.name for field in fields(cls)}
        if unknown:
            raise ConfigError("Unknown configuration fields: " + ", ".join(sorted(str(key) for key in unknown)))
        return cls(**dict(values))

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, **values):
        try:
            return replace(self, **values)
        except TypeError as error:
            raise ConfigError("Invalid configuration override") from error


def _read_config_values(path: str | Path) -> dict:
    """Shared bounded JSON reader; never discover environment or credentials."""
    def unique_pairs(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ConfigError(f"Duplicate configuration field: {key}")
            values[key] = value
        return values

    source = Path(os.path.abspath(path))
    protected = {".git", ".aws", ".kaggle", ".codex", "credentials", "access_token", "kaggle.json"}
    if source.suffix.casefold() != ".json" or any(part.casefold() in protected or part.casefold().startswith(".env") for part in source.parts):
        raise ConfigError("Configuration must be a project JSON file outside protected paths")
    for ancestor in (source, *source.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ConfigError("Configuration cannot use symbolic links or reparse paths")
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ConfigError("Configuration must be a regular project JSON file")
    with source.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ConfigError("Configuration exceeds the 64 KiB budget")
    try:
        values = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ConfigError("Configuration must be valid UTF-8 JSON") from error
    return values


def load_config(path: str | Path) -> AuditConfig:
    return AuditConfig.from_dict(_read_config_values(path))
