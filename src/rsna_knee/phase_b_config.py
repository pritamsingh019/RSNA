"""Validated, conservative CPU-only budgets for the read-only Phase B audit."""

from dataclasses import dataclass, fields

from .config import AuditConfig, ConfigError, _read_config_values


@dataclass(frozen=True)
class PhaseBConfig(AuditConfig):
    inspect_csv: bool = True
    max_file_bytes: int = 32 * 1024 * 1024
    max_inventory_depth: int = 6
    max_inventory_directories: int = 60000
    max_inventory_entries: int = 1200000
    max_inventory_seconds: int = 180
    max_sample_series: int = 32
    max_files_per_series: int = 8
    max_sample_file_entries: int = 4096
    max_sample_total_entries: int = 32768
    max_header_reads: int = 128
    max_header_bytes: int = 2 * 1024 * 1024
    max_dicom_file_bytes: int = 64 * 1024 * 1024
    max_pixel_decodes: int = 12
    max_pixel_elements: int = 4 * 1024 * 1024
    max_dicom_seconds: int = 90

    def __post_init__(self):
        super().__post_init__()
        if not self.inspect_csv:
            raise ConfigError("Phase B requires CSV inspection")
        limits = {
            "max_inventory_depth": (1, 12),
            "max_inventory_directories": (1, 100000),
            "max_inventory_entries": (1, 2000000),
            "max_inventory_seconds": (1, 600),
            "max_sample_series": (1, 128),
            "max_files_per_series": (1, 32),
            "max_sample_file_entries": (1, 16384),
            "max_sample_total_entries": (1, 131072),
            "max_header_reads": (1, 512),
            "max_header_bytes": (1024, 8 * 1024 * 1024),
            "max_dicom_file_bytes": (1024, 128 * 1024 * 1024),
            "max_pixel_decodes": (0, 12),
            "max_pixel_elements": (1, 8 * 1024 * 1024),
            "max_dicom_seconds": (1, 300),
        }
        for name, (lower, upper) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ConfigError(f"{name} must be an integer in [{lower}, {upper}]")

    def preflight_config(self) -> AuditConfig:
        """Use the unchanged CSV/discovery contracts, without Phase B fields."""
        return AuditConfig.from_dict({field.name: getattr(self, field.name) for field in fields(AuditConfig)})


def load_phase_b_config(path) -> PhaseBConfig:
    return PhaseBConfig.from_dict(_read_config_values(path))
