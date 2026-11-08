"""Saturday = CFB priority (Marvin 2026-10-24: "College big day is Saturday so increase that day") + the
dated Odds API credit plan to the monthly reset.

Permanent rule (config `sat_cfb_priority`, CT calendar day == Saturday):
  * live odds: ncaaf refreshes faster, other leagues slower (live_interval_overrides); soccer gets a smaller
    share of the live allocation; non-priority leagues pause once today's credits reach others_max_share of
    the day's cap, so the late CFB slate is never starved.
  * live props (in-game CFB): Saturday overrides on top of the weekend values.
  * pregame CFB lines: half-hourly windows + earlier morning snapshot.
  * allocations: `saturday_daily` per strategy (minor-sport props lowered).
Dated plan (config `credit_plan`, until `ends`): per-CT-day global cap for this bot + weekday trims for minor
leagues. After `ends` (the key's reset) the plan is ignored automatically.

Off switches (each independent, checked on every call, no restart needed):
  Saturday rule : env SAT_CFB_PRIORITY=off, file data/SAT_CFB_PRIORITY_OFF or /opt/marv-feed/SAT_CFB_PRIORITY_OFF
                  (the /opt/marv-feed file also turns off the alt70 Saturday boost), or sat_cfb_priority.enabled: false
  credit plan   : env CREDIT_PLAN=off, file data/CREDIT_PLAN_OFF or /opt/marv-feed/CREDIT_PLAN_OFF,
                  or credit_plan.enabled: false
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
DATA = Path(__file__).resolve().parent.parent / "data"
_CFG: Dict[str, Any] = {}


def configure(cfg: Optional[Dict[str, Any]]) -> None:
    """Remember the bot config (called from OddsAPI/Scanner/ValueScanner init). Unconfigured => rule inactive."""
    global _CFG
    if cfg:
        _CFG = cfg


SHARED_OFF = Path("/opt/marv-feed")   # one file there turns the rule off for aj-bet-bot AND the Marv side (alt70)


def _off(env: str, flag: str) -> bool:
    if os.environ.get(env, "on").strip().lower() in ("0", "off", "false", "no") or (DATA / flag).exists():
        return True
    try:
        return (SHARED_OFF / flag).exists()
    except OSError:
        return False


def _now() -> datetime:
    return datetime.now(CT)


def _day(day: Optional[str] = None) -> date:
    return date.fromisoformat(day) if day else _now().date()


def sat_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return (cfg or _CFG).get("sat_cfb_priority") or {}


def sat_active(day: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> bool:
    c = sat_cfg(cfg)
    if not c or not c.get("enabled", False) or _off("SAT_CFB_PRIORITY", "SAT_CFB_PRIORITY_OFF"):
        return False
    return _day(day).weekday() == int(c.get("weekday", 5))


def plan_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = (cfg or _CFG).get("credit_plan") or {}
    if not c or not c.get("enabled", False) or _off("CREDIT_PLAN", "CREDIT_PLAN_OFF"):
        return {}
    ends = c.get("ends")
    if ends:
        try:
            if _now() >= datetime.fromisoformat(str(ends)):
                return {}
        except ValueError:
            return {}
    return c


def day_cap(today: str, base: Optional[float], cfg: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Global daily cap for this bot: the dated plan's cap for `today` when set, else the Saturday cap, else base."""
    p = plan_cfg(cfg)
    v = (p.get("daily_cap") or {}).get(today)
    if v is not None:
        return float(v)
    if sat_active(today, cfg) and sat_cfg(cfg).get("daily_credit_cap") is not None:
        return float(sat_cfg(cfg)["daily_credit_cap"])
    return base


def plan_day_rule(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dated plan's rule for today (CT): credit_plan.days.sun on Sundays, .weekday Mon-Fri. Saturday uses the
    Saturday rule instead (never the plan's day rules)."""
    days = plan_cfg(cfg).get("days") or {}
    wd = _now().weekday()
    if wd == 5:
        return {}
    return dict(days.get("sun" if wd == 6 else "weekday") or {})


def _rule(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return sat_cfg(cfg) if sat_active(cfg=cfg) else plan_day_rule(cfg)


def live_interval(key: str, kind: Optional[str], every: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    """Live-odds refresh seconds for league `key` after the Saturday rule / the plan's day trims."""
    ov = _rule(cfg).get("live_interval_overrides") or {}
    v = ov.get(key, ov.get("soccer") if kind == "soccer" else None)
    return float(v) if v is not None else every


def soccer_share(base: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    v = _rule(cfg).get("soccer_max_share_of_live_daily")
    return float(v) if v is not None else base


def others_paused(key: str, used_today: float, cap: Optional[float], cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Non-priority leagues stop live pulls once this bot's credits today >= others_max_share x the day's cap
    (Saturday: everything but ncaaf; plan days: e.g. everything but nfl on Sunday)."""
    r = _rule(cfg)
    share = r.get("others_max_share_of_day_cap")
    if share is None or not cap:
        return False
    pri = r.get("priority_sports") or (["ncaaf"] if sat_active(cfg=cfg) else [])
    return key not in pri and used_today >= float(share) * float(cap)


def saturday_alloc(a: Dict[str, Any], day: Optional[str] = None) -> Optional[float]:
    if a.get("saturday_daily") is not None and sat_active(day):
        return a["saturday_daily"]
    return None


def live_props_overrides(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return dict(sat_cfg(cfg).get("live_props") or {}) if sat_active(cfg=cfg) else {}


def pregame(sport: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Saturday pregame-line settings for `sport` ({} when not active): morning_time, half_hourly."""
    if not sat_active(cfg=cfg):
        return {}
    return dict((sat_cfg(cfg).get("pregame") or {}).get(sport) or {})
