"""YAML config. ``rollout.live`` must stay empty. The live host is refused."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .broker_guard import PAPER_URL
from .errors import ConfigError
from .universe import default_universe

FORBIDDEN_KEYS = {"half_scale", "scale_out", "tp_at_1r", "early_tp", "telegram", "telegram_token"}

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Rollout:
    live: list[str] = field(default_factory=list)


@dataclass
class AccountCfg:
    exclusive: bool = False
    base_url: str = PAPER_URL


@dataclass
class TimeoutCfg:
    connect_sec: float = 3.0
    read_sec: float = 20.0
    retries: int = 3
    backoff_sec: tuple[float, ...] = (0.5, 1.0, 2.0)


@dataclass
class RegimeCfg:
    sma_fast: int = 50
    sma_slow: int = 200
    slope_days: int = 20
    min_slope: float = 0.01
    min_atr_pct: float = 0.004
    max_atr_pct: float = 0.035
    atr_period: int = 14


@dataclass
class DayCfg:
    enabled: bool = True
    timeframe_minutes: int = 5
    atr_period: int = 14
    atr_mult: float = 1.5
    target_r: float = 2.75
    entry_cutoff_min_before_close: int = 60
    flatten_min_before_close: int = 10
    vol_target_pct: float = 0.01
    ema: int = 20
    vwap_ext_std: float = 2.0


@dataclass
class SwingCfg:
    enabled: bool = True
    target_r: float = 2.0
    ema_exit: int = 20
    ema_trend: int = 50
    atr_mult: float = 2.0
    max_hold_days: int = 15
    donchian_entry: int = 55
    donchian_exit: int = 20
    donchian_atr_mult: float = 1.5
    donchian_risk_pct: float = 0.005


@dataclass
class ScalpCfg:
    enabled: bool = False
    timeframe_minutes: int = 1
    target_r: float = 2.0


@dataclass
class ModulesCfg:
    """Optional entry modules. False means log the signal and do not send an order."""

    vwap_orb: bool = False
    vwap_reclaim: bool = False
    velez: bool = False
    mean_reversion: bool = False
    range_box: bool = False


@dataclass
class RiskCfg:
    daily_loss_pct: float = 0.02
    weekly_loss_pct: float = 0.04
    day_kill_dd_pct: float = 0.06
    swing_kill_dd_pct: float = 0.08
    scalp_kill_dd_pct: float = 0.04
    max_gross_frac: float = 0.50
    max_net_frac: float = 0.30
    max_symbol_frac: float = 0.10
    max_positions: int = 5
    max_per_sector: int = 1
    gross_open_risk_frac: float = 0.02
    risk_pct_day: float = 0.005
    risk_pct_swing: float = 0.005
    staircase_step_r: float = 0.5
    breakeven_r: float = 1.0


@dataclass
class NewsCfg:
    enabled: bool = False
    lookback_hours: float = 6.0


@dataclass
class SpyOpenCfg:
    """Intraday SPY-versus-open bias. Off: Muse's tests found it hurt every system."""

    enabled: bool = False


@dataclass
class TopDownCfg:
    """Daily swing. Off until the pre-registered IEX validation passes. Do not paper-trade a failure."""

    enabled: bool = False
    risk_pct: float = 0.005
    target_r: float = 2.0
    rs_margin: float = 0.03
    daily_loss_pct: float = 0.01
    ema: int = 20
    sma: int = 50
    spy_sma: int = 200
    atr_mult: float = 1.0
    atr_period: int = 14


@dataclass
class LiquidityCfg:
    max_spread_bps: float = 15.0
    min_rvol: float = 0.5
    min_price: float = 5.0
    min_dollar_volume: float = 20_000_000.0


@dataclass
class WeightsCfg:
    alpha: float = 0.2
    min_sample: int = 8
    lo: float = 0.5
    hi: float = 1.5


@dataclass
class PathsCfg:
    state_dir: str = "state"
    earnings_csv: str = ""


@dataclass
class Config:
    rollout: Rollout = field(default_factory=Rollout)
    account: AccountCfg = field(default_factory=AccountCfg)
    timeouts: TimeoutCfg = field(default_factory=TimeoutCfg)
    regime: RegimeCfg = field(default_factory=RegimeCfg)
    day: DayCfg = field(default_factory=DayCfg)
    swing: SwingCfg = field(default_factory=SwingCfg)
    scalp: ScalpCfg = field(default_factory=ScalpCfg)
    modules: ModulesCfg = field(default_factory=ModulesCfg)
    news: NewsCfg = field(default_factory=NewsCfg)
    spy_open: SpyOpenCfg = field(default_factory=SpyOpenCfg)
    topdown: TopDownCfg = field(default_factory=TopDownCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    liquidity: LiquidityCfg = field(default_factory=LiquidityCfg)
    weights: WeightsCfg = field(default_factory=WeightsCfg)
    paths: PathsCfg = field(default_factory=PathsCfg)
    universe: list[str] = field(default_factory=default_universe)
    feed: str = "iex"


def _reject_forbidden(obj) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ConfigError(f"forbidden config key: {key}")
            _reject_forbidden(value)
    elif isinstance(obj, list):
        for item in obj:
            _reject_forbidden(item)


def _overlay(dc, raw: dict):
    if not raw:
        return dc
    data = asdict(dc)
    for key, value in raw.items():
        if key not in data:
            raise ConfigError(f"unknown config key: {key}")
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            setattr(dc, key, _overlay(current, value))
        else:
            setattr(dc, key, value)
    return dc


def validate(cfg: Config) -> Config:
    if list(cfg.rollout.live):
        raise ConfigError("rollout.live must be empty; the live endpoint is refused")
    base = str(cfg.account.base_url).rstrip("/")
    if base != PAPER_URL:
        raise ConfigError(f"refusing trading host {base}")
    cfg.account.base_url = PAPER_URL
    if str(cfg.feed).lower() != "iex":
        raise ConfigError("feed must be iex")
    cfg.feed = "iex"
    if not 2.5 <= float(cfg.day.target_r) <= 3.0:
        raise ConfigError("day.target_r must stay inside 2.5-3.0 (no 1R hard target)")
    if float(cfg.swing.target_r) < 2.0:
        raise ConfigError("swing.target_r must be at least 2")
    if int(cfg.risk.max_positions) < 1:
        raise ConfigError("max_positions")
    if int(cfg.risk.max_per_sector) < 1:
        raise ConfigError("max_per_sector")
    if float(cfg.risk.daily_loss_pct) <= 0 or float(cfg.risk.weekly_loss_pct) <= 0:
        raise ConfigError("loss stops must be positive")
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else _DEFAULT_PATH
    raw = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    _reject_forbidden(raw)
    cfg = Config()
    # universe is a list, not a dataclass
    universe = raw.pop("universe", None)
    feed = raw.pop("feed", None)
    for key, value in raw.items():
        if not hasattr(cfg, key):
            raise ConfigError(f"unknown config key: {key}")
        current = getattr(cfg, key)
        if hasattr(current, "__dataclass_fields__"):
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ConfigError(f"{key} must be a mapping")
            _overlay(current, value)
        else:
            setattr(cfg, key, value)
    if universe is not None:
        cfg.universe = [str(s).upper() for s in universe]
    if feed is not None:
        cfg.feed = str(feed)
    if not cfg.universe:
        cfg.universe = default_universe()
    return validate(cfg)


def config_hash(cfg: Config) -> str:
    import hashlib
    import json

    payload = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
