"""Configuration loading.

The TOML file holds everything needed to reproduce a run except secrets:
the database connection string is read from the environment variable named
by ``database.url_env`` and is never written to config, logs or reports.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Target columns that may be filled from a source attribute. Anything not
# listed here is still preserved verbatim in ``source_attributes``.
MAPPABLE_TARGETS = (
    "source_id",
    "site_name",
    "municipality_name",
    "municipality_code",
    "cadastral_district_name",
    "cadastral_district_code",
    "cadastral_parcel_refs",
    "compensation_status",
    "source_area",
    "credits_total",
    "credits_debited",
    "credits_available",
)

STATUS_VOCABULARY = (
    "planned",
    "active",
    "partially_debited",
    "fully_debited",
    "closed",
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    name: str
    endpoint: str
    type_name: str
    licence: str
    provider: str
    version: str = "2.0.0"
    # None means "use the DefaultCRS the service advertises for the layer".
    request_crs: str | None = None
    page_size: int = 500
    max_features: int = 100_000
    timeout_s: float = 60.0
    max_retries: int = 3
    backoff_s: float = 2.0
    request_delay_s: float = 1.0
    user_agent: str = "iris-oek-rp-harvester/0.1"


@dataclass(frozen=True)
class MappingConfig:
    # target column -> source attribute name (absent key = not mapped)
    fields: dict[str, str]
    # casefolded source status value -> controlled vocabulary value
    status_values: dict[str, str]
    # "m2" or "ha"; None means the source area unit is unknown, so the
    # source area is kept raw only and never converted.
    source_area_unit: str | None = None
    credit_unit: str | None = None
    # "de" = 1.234,5 ; "en" = 1,234.5
    decimal_style: str = "de"


@dataclass(frozen=True)
class QualityConfig:
    # Accept a make_valid() repair only if it changes area by at most this
    # fraction of the original ring area.
    max_repair_area_change: float = 0.01
    min_area_m2: float = 1.0
    # Relative tolerance between source-reported and computed area.
    area_mismatch_tolerance: float = 0.05
    # lon/lat bounding box of the state, used as a coarse membership check.
    state_bbox_wgs84: tuple[float, float, float, float] = (6.05, 48.90, 8.55, 51.00)
    # Prefix of official municipality keys (AGS) for the state; RP = "07".
    municipality_code_prefix: str | None = "07"


@dataclass(frozen=True)
class DatabaseConfig:
    url_env: str = "IRIS_DATABASE_URL"
    schema: str = "iris_staging"

    def url(self) -> str:
        value = os.environ.get(self.url_env)
        if not value:
            raise ConfigError(
                f"Database URL not set: export {self.url_env}=postgresql://..."
            )
        return value


@dataclass(frozen=True)
class Config:
    state_code: str
    site_id_prefix: str
    source: SourceConfig
    mapping: MappingConfig
    quality: QualityConfig = field(default_factory=QualityConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    output_dir: Path = Path("output")


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    try:
        return _build(raw, base_dir=path.parent)
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Invalid config {path}: {exc}") from exc


def _build(raw: dict, base_dir: Path) -> Config:
    harvest = raw["harvest"]
    state_code = str(harvest["state_code"]).upper()
    if len(state_code) != 2:
        raise ConfigError("harvest.state_code must be a two-letter code")

    source = SourceConfig(**raw["source"])
    if source.page_size < 1:
        raise ConfigError("source.page_size must be >= 1")

    mapping_raw = raw.get("mapping", {})
    fields = dict(mapping_raw.get("fields", {}))
    unknown = set(fields) - set(MAPPABLE_TARGETS)
    if unknown:
        raise ConfigError(f"Unknown mapping targets: {sorted(unknown)}")
    status_values = {
        str(k).strip().casefold(): str(v)
        for k, v in mapping_raw.get("status_values", {}).items()
    }
    bad_status = set(status_values.values()) - set(STATUS_VOCABULARY)
    if bad_status:
        raise ConfigError(
            f"status_values map to unknown statuses {sorted(bad_status)}; "
            f"allowed: {STATUS_VOCABULARY}"
        )
    unit = mapping_raw.get("source_area_unit")
    if unit not in (None, "m2", "ha"):
        raise ConfigError("mapping.source_area_unit must be 'm2', 'ha' or omitted")
    decimal_style = mapping_raw.get("decimal_style", "de")
    if decimal_style not in ("de", "en"):
        raise ConfigError("mapping.decimal_style must be 'de' or 'en'")
    mapping = MappingConfig(
        fields=fields,
        status_values=status_values,
        source_area_unit=unit,
        credit_unit=mapping_raw.get("credit_unit"),
        decimal_style=decimal_style,
    )

    quality_raw = dict(raw.get("quality", {}))
    if "state_bbox_wgs84" in quality_raw:
        quality_raw["state_bbox_wgs84"] = tuple(quality_raw["state_bbox_wgs84"])
    quality = QualityConfig(**quality_raw)

    database = DatabaseConfig(**raw.get("database", {}))

    output_dir = Path(harvest.get("output_dir", "output"))
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir

    return Config(
        state_code=state_code,
        site_id_prefix=str(harvest.get("site_id_prefix", "")),
        source=source,
        mapping=mapping,
        quality=quality,
        database=database,
        output_dir=output_dir,
    )
