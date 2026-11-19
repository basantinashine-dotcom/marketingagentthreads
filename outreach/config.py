"""Configuration loaded from a TOML file. Secrets stay in environment variables."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class VoiceConfig:
    # True things about you the drafts may draw on. The model never invents others.
    facts: list[str] = field(default_factory=list)
    # How you write, e.g. "casual, mostly lowercase, no exclamation marks".
    style: str = ""


@dataclass(frozen=True)
class SiteConfig:
    description: str
    # The URL that replaces [LINK]. UTM tags let you see Threads traffic in analytics.
    link_url: str
    # At most this share of your replies (trailing window) carry the link.
    max_link_ratio: float = 0.67
    ratio_window_days: int = 7


@dataclass(frozen=True)
class SearchConfig:
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    search_type: str = "RECENT"
    max_post_age_hours: int = 36
    results_per_query: int = 25
    min_text_length: int = 25


@dataclass(frozen=True)
class LimitsConfig:
    max_drafts_per_run: int = 25
    max_posts_per_day: int = 10
    seconds_between_posts: int = 300
    author_cooldown_days: int = 30
    max_reply_age_hours: int = 72
    retention_days: int = 30


@dataclass(frozen=True)
class ModelConfig:
    name: str = "claude-opus-5-5"
    effort: str = "medium"


@dataclass(frozen=True)
class Config:
    voice: VoiceConfig
    site: SiteConfig
    search: SearchConfig
    limits: LimitsConfig
    model: ModelConfig
    db_path: Path


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found. Copy config.example.toml to {path} and edit it.")
    raw = tomllib.loads(path.read_text())

    try:
        cfg = Config(
            voice=VoiceConfig(**raw.get("voice", {})),
            site=SiteConfig(**raw["site"]),
            search=SearchConfig(**raw.get("search", {})),
            limits=LimitsConfig(**raw.get("limits", {})),
            model=ModelConfig(**raw.get("model", {})),
            db_path=(path.parent / raw.get("storage", {}).get("db_path", "outreach.db")),
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Invalid config in {path}: {exc}") from exc

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.search.search_type not in ("RECENT", "TOP"):
        raise ConfigError("search.search_type must be RECENT or TOP.")
    if not cfg.site.link_url.startswith("https://"):
        raise ConfigError("site.link_url must be an https:// URL.")
    if not 0 <= cfg.site.max_link_ratio <= 1:
        raise ConfigError("site.max_link_ratio must be between 0 and 1.")
    if cfg.model.effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ConfigError("model.effort must be low, medium, high, xhigh or max.")
    if cfg.limits.retention_days < cfg.limits.author_cooldown_days:
        # The cooldown check reads usernames from the DB; wiping them sooner breaks it.
        raise ConfigError("limits.retention_days must be >= limits.author_cooldown_days.")
