"""CSV-only Phase C configuration; no DICOM, environment or network settings."""

from dataclasses import dataclass, fields

from .config import AuditConfig, ConfigError, _read_config_values


@dataclass(frozen=True)
class PhaseCConfig(AuditConfig):
    inspect_csv: bool = True
    max_file_bytes: int = 32 * 1024 * 1024
    max_report_chars: int = 20000
    max_report_clauses: int = 256
    max_extraction_seconds: int = 120

    def __post_init__(self):
        super().__post_init__()
        if not self.inspect_csv:
            raise ConfigError("Phase C requires CSV preflight")
        if self.max_csv_rows > 100000:
            raise ConfigError("Phase C permits at most 100000 CSV rows per manifest")
        for name, lower, upper in (
            ("max_report_chars", 1, 20000),
            ("max_report_clauses", 1, 256),
            ("max_extraction_seconds", 1, 600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ConfigError(f"{name} must be an integer in [{lower}, {upper}]")

    def preflight_config(self) -> AuditConfig:
        return AuditConfig.from_dict({field.name: getattr(self, field.name) for field in fields(AuditConfig)})


def load_phase_c_config(path) -> PhaseCConfig:
    return PhaseCConfig.from_dict(_read_config_values(path))
