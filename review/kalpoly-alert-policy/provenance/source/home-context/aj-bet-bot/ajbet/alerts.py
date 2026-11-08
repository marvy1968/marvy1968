"""Alert formatting and Telegram delivery (or dry-run print)."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
import json
from typing import Any, Dict, List, Optional

import requests

from .oddsmath import fmt_american
from .strategies import Signal

log = logging.getLogger(__name__)


def read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def format_alert(sig: Signal, prefix: str = "AJ Bet Bot:") -> str:
    g, d = sig.game, sig.details
    tag = "VALUE" if sig.value else "NO VALUE"
    lines = [f"{prefix} [{tag}] {'Favorite comeback' if sig.strategy == 'fav_comeback' else 'Soccer slow grind'}"
             f" ({g.league.upper()})",
             f"Game: {g.away.name} @ {g.home.name}",
             f"Score: {g.away.abbr or g.away.name} {g.away.score} - {g.home.abbr or g.home.name} {g.home.score}"
             f" | {g.detail or g.display_clock}"]
    if sig.strategy == "fav_comeback":
        bs = d.get("best_spread") or {}
        lines.append(f"Pregame: {sig.selection} ML {fmt_american(d.get('pre_ml'))}, spread "
                     f"{d['pre_spread']:+g}" if d.get("pre_spread") is not None else
                     f"Pregame: {sig.selection} ML {fmt_american(d.get('pre_ml'))}")
        live = f"Live: ML {fmt_american(d.get('live_ml'))} (median)"
        if d.get("live_spread") is not None:
            live += f", spread {d['live_spread']:+g} (best {bs.get('best_point', 0):+g} "
            live += f"{fmt_american(bs.get('best_price'))} @ {bs.get('best_book')})"
        lines.append(live)
        lines.append("Trigger: " + "; ".join(sig.triggers))
        if d.get("prior_source") == "ratings":
            ri = d.get("rating_info") or {}
            prior = (f"ratings prior: fav by {d['rating_margin_fav']:+.1f} pre-game ({ri.get('source', 'ratings')}, "
                     f"games {ri.get('games')})")
        else:
            prior = f"market prior: spread {d.get('pregame_spread_used', 0):+g}"
        lines.append(f"Model aj_edge (Stern, {d['frac_left']:.0%} left, margin {d['margin']:+d}, {prior}): "
                     f"{_pct(sig.fair_p)} vs market (de-vig) {_pct(sig.market_p)} -> edge {sig.edge * 100:+.1f} pp")
        if d.get("prior_source") == "ratings" and d.get("market_prior_p") is not None:
            lines.append(f"(Same Stern with the market pregame spread: {_pct(d['market_prior_p'])})")
        for n in d.get("notes") or []:
            lines.append(f"Note: {n}")
        lines.append(f"Bet idea: {sig.selection} ML {fmt_american(sig.price)} @ {sig.book}")
    else:
        lines.append(f"Pregame total {d['pregame_total']:g} -> live total {d['live_total']:g}")
        lines.append("Trigger: " + "; ".join(sig.triggers))
        lines.append(f"Suggest: Under {d['target_line']:g} (= max(live+3, goals+5))"
                     + ("" if sig.line == d["target_line"] else f"; best offered line Under {sig.line:g}"))
        lines.append(f"Poisson (lambda {d['lam']:.2f} over {d['minutes_left']:.0f} min): {_pct(sig.fair_p)}"
                     f" = fair {fmt_american(d['fair_odds'])}; market {_pct(sig.market_p)}"
                     + ("" if sig.edge is None else f" -> edge {sig.edge * 100:+.1f} pp"))
        if sig.price is not None:
            lines.append(f"Best price: Under {sig.line:g} {fmt_american(sig.price)} @ {sig.book}; "
                         f"stake to win $20: ${d['stake_to_win_20']:.2f}")
        else:
            lines.append(f"Best price: not offered by US books via The Odds API; only take at "
                         f"{fmt_american(d['fair_odds'])} or better")
    conf = "low (in-sample calibration; live-price backtest thin)" if not getattr(sig, "value", False) else \
        "medium (model fair beats price; trailing-fav bump is an in-sample fit)"
    lines.append(f"Confidence: {conf}")
    if sig.stake is not None:
        lines.append(f"Paper stake: ${sig.stake:.2f} to win ${sig.to_win:.0f} (tracking only, no bet placed)")
    return "\n".join(lines)


def looks_html(text: str) -> bool:
    return "<b>" in text and not text.startswith("AJ Bet Bot")


class Notifier:
    def __init__(self, cfg: dict, dry_run: bool = True, printer=print):
        tc = cfg.get("telegram", {})
        self.prefix = tc.get("prefix", "AJ Bet Bot:")
        self.dry_run = dry_run
        self.printer = printer
        # Process environment first (systemd EnvironmentFile), then the .env file. Never logged.
        ef = tc.get("env_file", "")
        if ef and not Path(ef).is_absolute():
            ef = str(Path(cfg.get("_root", ".")) / ef)
        env = read_env_file(ef) if ef else {}
        tv, cv = tc.get("token_var", "TELEGRAM_BOT_TOKEN"), tc.get("chat_id_var", "TELEGRAM_CHAT_ID")
        self._token = os.environ.get(tv) or env.get(tv, "")
        self._chat = os.environ.get(cv) or env.get(cv, "")      # primary (Marvin)
        # Extra private recipients (e.g. AJ): config list + a JSON file editable without a restart.
        self._static = [str(x.get("chat_id") if isinstance(x, dict) else x) for x in (tc.get("recipients") or [])]
        rf = tc.get("recipients_file", "~/.config/aj-bet-bot/recipients.json")
        self._rfile = Path(os.path.expanduser(rf)) if rf else None
        self._rcache: tuple = (None, [])
        # 2026-10-20 2:34 PM (Marvin): per-league Telegram allow-list. A league listed here sends ONLY the listed
        # alert types (anything else = paper only, row stays unsent and graded); listed types get the label line.
        self._league_allow = {str(k).lower(): {str(t) for t in (v or [])}
                              for k, v in (tc.get("league_allow") or {}).items()}
        self._league_label = {str(k).lower(): str(v) for k, v in (tc.get("league_label") or {}).items()}

    def extra_recipients(self) -> List[Dict[str, str]]:
        """[{chat_id, name}] from recipients_file (re-read when it changes) + config `telegram.recipients`."""
        out = [{"chat_id": c, "name": ""} for c in self._static if c]
        if self._rfile is not None:
            try:
                mt = self._rfile.stat().st_mtime
                if self._rcache[0] != mt:
                    import json as _json
                    data = _json.loads(self._rfile.read_text() or "[]")
                    self._rcache = (mt, [{"chat_id": str(d["chat_id"]), "name": str(d.get("name", ""))}
                                         for d in data if d.get("chat_id")])
                out += self._rcache[1]
            except FileNotFoundError:
                pass
            except Exception as e:  # noqa: BLE001
                log.warning("recipients file unreadable: %s", e)
        seen, uniq = {str(self._chat)}, []
        for r in out:
            if r["chat_id"] not in seen:
                seen.add(r["chat_id"])
                uniq.append(r)
        return uniq

    def recipients(self) -> List[str]:
        return ([str(self._chat)] if self._chat else []) + [r["chat_id"] for r in self.extra_recipients()]

    # ------------------------------------------------------------------ delivery (2026-10-02 review fix #1)
    # Each Telegram call is retried with backoff (network errors, HTTP 429 honouring retry_after, 5xx).
    # A recipient that still fails is queued in the DB `outbox` (when a DB is attached) and retried on the
    # next loop / after a restart; the alert row is marked sent only after the PRIMARY chat accepted it.
    RETRIES = 3
    BACKOFF_S = (1.0, 3.0)
    MAX_RETRY_AFTER_S = 15.0
    TG_LIMIT = 4096

    def attach_outbox(self, db, max_age_s: float = 900.0) -> None:
        """Persist failed sends in `db` (the bot's DB). Entries older than max_age_s are dropped (stale odds)."""
        self._outbox_db = db
        self._outbox_max_age = float(max_age_s)

    def send(self, text: str, html: Optional[bool] = None, alert_id=None) -> bool:
        """html=None: auto (friendly cards are Telegram HTML; they start with an emoji, no prefix).
        alert_id: int or list of alert ids marked sent once the primary chat has the message (now or on retry)."""
        if html is None:
            html = looks_html(text)
        self._marv_verdict = ""
        _base_n = text.count("\n")
        try:  # 2026-10-27 (Marvin): CFB line swings + CFB player props TRACK-ONLY (row stays unsent, still graded)
            from . import cfb_track as _cft   # off: CFB_SWING_PROP_TRACK=off | data/CFB_SWING_PROP_TRACK_OFF
            _cat = _cft.blocked_alert(getattr(self, "_outbox_db", None), alert_id)
        except Exception:  # noqa: BLE001
            _cat = None
        if _cat:
            log.info("CFB %s alert %s: tracked, not sent (%s)", _cat, alert_id, _cft.REASON)
            return False
        try:  # 2026-11-04 Marvin: NFL LIVE receptions props track-only; AJ_NFL_LIVE_REC_TRACK=off | data/AJ_NFL_LIVE_REC_TRACK_OFF
            from . import nfl_live_rec_track as _nlr
            if _nlr.blocked_alert(getattr(self, "_outbox_db", None), alert_id):
                log.info("NFL live receptions alert %s: tracked, not sent (%s)", alert_id, _nlr.REASON)
                return False
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-04 Marvin: NCAAF live value + CFB live TD (D/ST, >30min) track-only; AJ_NCAAF_LIVE_VALUE_TRACK / AJ_CFB_TD_TRACK=off
            from . import ncaaf_live_track as _nlt
            _w = _nlt.blocked_alert(getattr(self, "_outbox_db", None), alert_id)
            if _w:
                log.info("NCAAF alert %s: tracked, not sent (%s)", alert_id, _w)
                return False
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-04 Marvin: LIVE player props stated EV > 0.5 track-only; AJ_LIVE_PROP_EV50_TRACK=off | data/AJ_LIVE_PROP_EV50_TRACK_OFF
            from . import live_prop_ev50_track as _lpe
            if _lpe.blocked_alert(getattr(self, "_outbox_db", None), alert_id):
                log.info("live prop EV>%.2f alert %s: tracked, not sent (%s)", _lpe.ev_max(), alert_id, _lpe.REASON)
                return False
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-11-02 Marvin: total_swing not sent (still paper-tracked + graded); TOTAL_SWING_NOSEND=off
            from . import me_gates as _mg
            if _mg.nosend_alert(getattr(self, "_outbox_db", None), alert_id):
                log.info("total_swing alert %s: tracked, not sent", alert_id)
                return False
        except Exception:  # noqa: BLE001
            pass
        gate = self._league_gate(alert_id)
        if gate is False:
            return False          # league allow-list (NBA: only live comeback + line swing) -> paper only
        if gate and gate not in text:
            text = f"{text}\n{gate}"
        text = self._with_marv_overlay(text, alert_id)
        try:  # 2026-10-17 (Marvin): one info tag line + stake tier (ajbet/enrich.py); never blocks; MARV_ENRICH=off
            from . import enrich as _enrich
            text = _enrich.enrich(getattr(self, "_outbox_db", None), alert_id, text)
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-10-23 (Marvin approved Oct 20): CFB H2H matchup model line on NCAAF alerts; H2H_MODEL=off
            from . import h2h_model as _h2h
            text = _h2h.alert_append(getattr(self, "_outbox_db", None), alert_id, text)
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-10-25 (Marvin): Bovada buy edge for AJ, display only; BUY_EDGE_LINE=off | data/BUY_EDGE_LINE_OFF
            from . import buy_edge as _be
            text = _be.append(getattr(self, "_outbox_db", None), alert_id, text, _base_n)
        except Exception:  # noqa: BLE001
            pass
        if self._marv_veto(alert_id, text):
            return False          # Marv H2H 👎 bad -> not sent (alert row stays unsent = paper only, still graded)
        if self._double_dip(alert_id):
            return False          # 2026-10-18 (Marvin): no double dipping unless hedging (row stays unsent, paper only)
        if getattr(self, "_dd_note", ""):     # resent prop after a line move: note on the headline (no extra line)
            _h, _sep, _rest = text.partition("\n")
            text = f"{_h} · 🔁 {self._dd_note}{_sep}{_rest}"
            self._dd_note = ""
        if not html and not text.startswith(self.prefix):
            text = f"{self.prefix} {text}"
        if self.dry_run:
            from .friendly import plain
            self.printer("----- [DRY RUN: Telegram not sent] -----\n" + (plain(text) if html else text) + "\n" + "-" * 40)
            return False
        if not (self._token and self._chat):
            log.error("Telegram credentials missing; printing instead")
            self.printer(text)
            return False
        ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
        ok_primary = False
        for chat in self.recipients():          # broadcast: primary first, then extra recipients
            primary = chat == str(self._chat)
            ok = self._send_one(chat, text, html)
            if not ok:
                self._enqueue(chat, text, html, ids if primary else [])
            if primary:
                ok_primary = ok
        return ok_primary

    def _league_gate(self, alert_id):
        """None = no rule for this alert; False = league allow-listed and this type is not on it (do not send);
        str = allowed, label line to add ('' if none). Needs the alert row; sends without an id are not gated."""
        if not self._league_allow:
            return None
        ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
        db = getattr(self, "_outbox_db", None)
        if not ids or db is None:
            return None
        try:
            row = db.conn.execute("SELECT league, strategy, market FROM alerts WHERE id = ?", (int(ids[0]),)).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        lg, st = str(row[0] or "").lower(), str(row[1] or "")
        if lg == "nba" and st == "value" and str(row[2] or "").startswith("player_"):
            from . import nba_props_tg   # 2026-10-24 (Marvin): NBA player props ON; off: NBA_PROPS_TG=off
            if nba_props_tg.enabled():
                return ""
        if lg == "nba":   # 2026-10-24 bug fix: NBA = player props only on Telegram; NBA_GAMELINES_TG_BLOCK=off reverts
            from . import nba_props_tg
            if nba_props_tg.gamelines_blocked():
                log.info("NBA game-line alert %s (%s) -> paper only (NBA Telegram = player props only)", ids[0], st)
                return False
        if lg not in self._league_allow:
            return None
        if st not in self._league_allow[lg]:
            log.info("league allow-list: %s %s alert %s -> paper only (not sent)", lg, st, ids[0])
            return False
        return self._league_label.get(lg, "")

    # ---- Marv Predict H2H overlay (one extra line on the SAME alert; never a new message, never blocks)
    def _with_marv_overlay(self, text: str, alert_id) -> str:
        """Append Marv's team head-to-head read ("looks good / doesn't look good + why", a % only when Marv's logic is
        backtest-proven) from the local Marv bridge. Alert types, scanners and sending are unchanged: on any
        problem (bridge down, game unknown, timeout) the alert goes out exactly as before.
        MARV_OVERLAY=off disables; MARV_OVERLAY_LEAGUES (default ncaaf) picks the leagues."""
        try:
            if os.environ.get("MARV_OVERLAY", "on").lower() in ("0", "off", "false", "no"):
                return text
            ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
            db = getattr(self, "_outbox_db", None)
            if not ids or db is None or "Marv H2H" in text:
                return text
            row = db.conn.execute("SELECT league, market, selection, line, price, message, details_json FROM alerts "
                                  "WHERE id = ?", (int(ids[0]),)).fetchone()
            if row is None:
                return text
            league, market, selection, line, price, message, details = (row[k] for k in range(7))
            leagues = {x.strip() for x in os.environ.get("MARV_OVERLAY_LEAGUES", "ncaaf").lower().split(",") if x.strip()}
            if (league or "").lower() not in leagues:
                return text
            try:
                det = json.loads(details or "{}")
            except ValueError:
                det = {}
            live = bool(det.get("in_game") or det.get("score") or det.get("live_total"))
            from .friendly import plain
            params = {"sport": league, "market": market or "", "side": selection or "",
                      "line": "" if line is None else line, "price": "" if price is None else price,
                      "text": (plain(text) if looks_html(text) else text)[:1500], "live": "1" if live else ""}
            # Oct 11 2026 (Marvin, explicit: "Cut Marv H2H back on"): name both teams from the alert's matchup so live
            # cards ("49ers 29-26 Seahawks") and props still find Marv's game and show the H2H take
            mu = str(det.get("matchup") or "")
            if " @ " in mu:
                aw, hm = (x.strip() for x in mu.split(" @ ", 1))
                if aw and hm:
                    params["team"], params["other"] = hm, aw
            url = os.environ.get("MARV_OVERLAY_URL", "http://REDACTED_PRIVATE_HOST.invalid")
            r = requests.get(url, params=params, timeout=1.5)
            js = (r.json() or {}) if r.status_code == 200 else {}
            line_txt = js.get("line", "")
            self._marv_verdict = str(js.get("verdict") or "")
            if not self._marv_verdict and "👎" in (line_txt or ""):
                self._marv_verdict = "not"
            if live and line_txt:  # 2026-10-16 (Marvin): track live Marv H2H reads; graded with the alert's paper bet
                try:
                    for a in ids:
                        r0 = db.conn.execute("SELECT details_json FROM alerts WHERE id = ?", (int(a),)).fetchone()
                        d0 = json.loads((r0[0] if r0 else None) or "{}") or {}
                        d0["marv_live_read"] = {"verdict": self._marv_verdict or "n/a", "line": line_txt[:300]}
                        db.conn.execute("UPDATE alerts SET details_json = ? WHERE id = ?", (json.dumps(d0), int(a)))
                    db.conn.commit()
                except Exception as e2:  # noqa: BLE001
                    log.debug("Marv live read tag skipped: %s", e2)
            if line_txt:
                return f"{text}\n{line_txt}"
        except Exception as e:  # noqa: BLE001
            log.debug("Marv overlay skipped: %s", e)
        return text

    # ---- Marv 👎 veto (Marvin, Oct 10 2026, explicit: "If Marv H2H is 👎 bad, do not send the March_edge alert")
    _VETO_EXEMPT = ("props", "team_total_over")      # props ⭐⭐+ keep their own rule (player/team props, not H2H)

    def _marv_veto(self, alert_id, text: str) -> bool:
        """True when Marv's H2H read on this alert is 👎 bad (bridge verdict 'not'): the alert is not sent; the row
        is tagged marv_veto in details_json and stays sent=0 (paper only, still graded).
        Props (player/team PROP VALUE): H2H 👎 is information only — still shown on the card, never blocks —
        unless MARV_VETO_PROPS=on (same log-only pattern as MARV_VETO_LIVE for in-game H2H; 2026-10-16 Marvin).
        Oct 11 2026 PM (Marvin, explicit: "Block Marv H2H 👎 again"): ON by default again for game alerts;
        MARV_VETO=off disables. The Marv H2H line still shows on sent cards. Any problem -> False (alert goes out)."""
        try:
            if getattr(self, "_marv_verdict", "") != "not":
                return False
            if os.environ.get("MARV_VETO", "on").lower() in ("0", "off", "false", "no"):
                return False
            ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
            db = getattr(self, "_outbox_db", None)
            if not ids or db is None:
                return False
            row = db.conn.execute("SELECT strategy, market FROM alerts WHERE id = ?", (int(ids[0]),)).fetchone()
            if row is None:
                return False
            strat, mk = str(row[0] or ""), str(row[1] or "")
            line = next((x for x in text.split("\n") if "Marv H2H" in x), "")
            r0 = db.conn.execute("SELECT details_json FROM alerts WHERE id = ?", (int(ids[0]),)).fetchone()
            try:
                d0 = json.loads((r0[0] if r0 else None) or "{}") or {}
            except ValueError:
                d0 = {}
            # 2026-10-16 (Marvin): pregame/live PROP VALUE — Marv game H2H 👎 is info/log-only (still on the card),
            # never blocks, unless MARV_VETO_PROPS=on. Mirrors MARV_VETO_LIVE. Game (non-prop) vetoes unchanged.
            is_prop = (
                strat in self._VETO_EXEMPT
                or strat.startswith("props")
                or strat == "live_props"
                or mk.startswith(("player_", "batter_", "pitcher_"))
                or "team_totals" in mk
                or "PROP VALUE" in (text or "")
            )
            if is_prop and os.environ.get("MARV_VETO_PROPS", "off").lower() not in ("1", "on", "true", "yes"):
                log.info("alert %s: prop Marv H2H 👎 logged only (MARV_VETO_PROPS off): %s", ids, line[:200])
                return False
            # 2026-10-16 (Marvin): live H2H reads (Marv in-game projection) are unproven -> log only, never block,
            # unless MARV_VETO_LIVE=on
            if (d0.get("in_game") or d0.get("score") or d0.get("live_total")) and \
                    os.environ.get("MARV_VETO_LIVE", "off").lower() not in ("1", "on", "true", "yes"):
                log.info("alert %s: live Marv H2H 👎 logged only (MARV_VETO_LIVE off): %s", ids, line[:200])
                return False
            for a in ids:
                r = db.conn.execute("SELECT details_json FROM alerts WHERE id = ?", (int(a),)).fetchone()
                try:
                    det = json.loads((r[0] if r else None) or "{}") or {}
                except ValueError:
                    det = {}
                det["marv_veto"] = line[:300] or "👎 bad"
                det["paper_reason"] = "marv_h2h_bad"
                db.conn.execute("UPDATE alerts SET details_json = ? WHERE id = ?", (json.dumps(det), int(a)))
            db.conn.commit()
            log.info("alert %s not sent: Marv H2H 👎 bad (paper only)", ids)
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("Marv veto check failed: %s", e)
            return False

    # ---- 2026-10-18 (Marvin): NO DOUBLE DIPPING UNLESS HEDGING
    @staticmethod
    def _dd_side(market: str, selection: str, line, det: dict):
        """(group, side) for one alert. Game sides: ML / spread / alt spread on a team = ('team', team); game totals =
        ('total', over|under). Team totals = ('tt:<team>', dir). Props = ('prop:<player>:<market>', dir)."""
        mk, sel = str(market or "").lower(), str(selection or "").strip().lower()
        if mk.startswith(("player_", "batter_", "pitcher_")):
            pl = str(det.get("player") or det.get("player_name") or "").strip().lower()
            return (f"prop:{pl}:{mk.replace('_alternate', '')}", sel.split()[0] if sel else "")
        if "team_totals" in mk:
            tm = str(det.get("team") or det.get("description") or "").strip().lower()
            return (f"tt:{tm}", sel.split()[0] if sel else "")
        if mk in ("totals", "alternate_totals") or sel.startswith(("over", "under")):
            return ("total", sel.split()[0] if sel else "")
        if mk in ("h2h", "spreads", "alternate_spreads", "h2h_lay"):
            return ("team", sel)
        return (mk, sel)

    @staticmethod
    def _dd_moved(line, price, pline, pprice) -> str:
        """'line moved from X' when the line moved >= 1 (one full step on half-point props) or the price moved
        >= 3 implied-probability points since the last sent alert on this player prop; '' = identical repeat."""
        try:
            if line is not None and pline is not None and abs(float(line) - float(pline)) >= 1.0 - 1e-9:
                return f"line moved from {float(pline):g}"
            def ip(p):
                p = float(p)
                return 100 / (p + 100) if p > 0 else -p / (-p + 100)
            if price is not None and pprice is not None and abs(ip(price) - ip(pprice)) >= 0.03:
                return f"price moved from {int(float(pprice)):+d}"
        except (TypeError, ValueError):
            pass
        return ""

    def _double_dip(self, alert_id) -> bool:
        """True (do not send) when a SENT alert on the same game already backs the same side: the same team across
        ML / spread / repeat value alerts, the same over/under direction on the game total, or the same player + prop +
        direction. An opposite-side alert is allowed: it is a hedge against the already-sent (open) side. Props on other
        players / other stats are separate. Blocked rows stay sent=0 (paper only) and are tagged double_dip in
        details_json. NO_DOUBLE_DIP=off disables. Any error -> False (alert goes out as before)."""
        try:
            if os.environ.get("NO_DOUBLE_DIP", "on").lower() in ("0", "off", "false", "no"):
                return False
            ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
            db = getattr(self, "_outbox_db", None)
            if not ids or db is None:
                return False
            self._dd_note = ""
            row = db.conn.execute("SELECT event_id, market, selection, line, details_json, id, message, price FROM alerts WHERE id = ?",
                                  (int(ids[0]),)).fetchone()
            if row is None or not row[0]:
                return False
            def _det(j):
                try:
                    return json.loads(j or "{}") or {}
                except ValueError:
                    return {}
            def _pl(aid, msg, det):
                """2026-10-18 fix: player name from the alert's own record (details, paper_bets.player, message)."""
                d = dict(det)
                if not (d.get("player") or d.get("player_name")):
                    try:
                        r = db.conn.execute("SELECT player FROM paper_bets WHERE alert_id = ? AND player IS NOT NULL AND player != '' LIMIT 1",
                                            (int(aid),)).fetchone()
                        if r: d["player"] = r[0]
                    except Exception:  # noqa: BLE001
                        pass
                if not (d.get("player") or d.get("player_name")):
                    import re as _re
                    m = _re.search(r"<b>(.+?)\s+(?:OVER|UNDER|Over|Under)\s", str(msg or ""))
                    if m: d["player"] = m.group(1).strip()
                return d
            det0 = _pl(row[5], row[6], _det(row[4]))
            grp, side = self._dd_side(row[1], row[2], row[3], det0)
            if grp.startswith("prop:") and grp.split(":")[1] == "":
                return False      # prop with no player name found -> allow (never block blind)
            prior = db.conn.execute(
                "SELECT id, market, selection, line, details_json, message, price FROM alerts WHERE event_id = ? AND sent = 1 AND id NOT IN (%s) ORDER BY id"
                % ",".join("?" * len(ids)), (row[0], *[int(a) for a in ids])).fetchall()
            same = [p for p in prior if self._dd_side(p[1], p[2], p[3], _pl(p[0], p[5], _det(p[4]))) == (grp, side)
                    and not (grp.startswith("prop:") and grp.split(":")[1] == "")]
            if not same:
                return False      # first alert on this side, or a hedge against an opposite open side
            if grp.startswith("prop:"):   # Oct 18 2026 (Marvin): same player prop may resend after a real line/price move
                mv = self._dd_moved(row[3], row[7], same[-1][3], same[-1][6])
                if mv:
                    self._dd_note = mv
                    return False
            why = f"double dip: already sent {grp} {side} on this game (alert {same[-1][0]}); hedges only"
            for a in ids:
                r = db.conn.execute("SELECT details_json FROM alerts WHERE id = ?", (int(a),)).fetchone()
                det = _det(r[0] if r else None)
                det["double_dip"] = why
                det["paper_reason"] = "double_dip"
                db.conn.execute("UPDATE alerts SET details_json = ? WHERE id = ?", (json.dumps(det), int(a)))
            db.conn.commit()
            log.info("alert %s not sent: %s", ids, why)
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("double-dip check failed: %s", e)
            return False

    # ---- outbox (persisted failed sends)
    def _enqueue(self, chat: str, text: str, html: bool, ids: List[int]) -> None:
        db = getattr(self, "_outbox_db", None)
        if db is None:
            log.error("Telegram send to %s failed after retries; no outbox attached (dropped)",
                      "primary" if chat == str(self._chat) else "extra")
            return
        try:
            db.outbox_add(chat, text, bool(html), ids)
            log.warning("Telegram send to %s failed after retries; queued for retry",
                        "primary" if chat == str(self._chat) else "extra")
        except Exception:  # noqa: BLE001
            log.exception("outbox enqueue failed")

    def flush_outbox(self) -> Dict[str, int]:
        """Retry queued sends (called every loop and at startup). Returns counts."""
        out = {"sent": 0, "expired": 0, "failed": 0}
        db = getattr(self, "_outbox_db", None)
        if db is None or self.dry_run or not self._token:
            return out
        for r in db.outbox_pending():
            try:
                age = time.time() - datetime.fromisoformat(r["ts"]).timestamp()
            except (TypeError, ValueError):
                age = 0.0
            if age > self._outbox_max_age:
                db.outbox_done(r["id"], "expired")
                out["expired"] += 1
                log.warning("Telegram outbox #%s expired after %.0f min (not resent: odds are stale)", r["id"], age / 60)
                continue
            text = r["text"]
            hdr = f"⏱ Delayed delivery (first tried {int(age // 60)} min ago); recheck the price before betting"
            text = (f"<i>{hdr}</i>\n" if r["html"] else f"{self.prefix} [{hdr}]\n") + \
                (text[len(self.prefix):].lstrip() if not r["html"] and text.startswith(self.prefix) else text)
            if self._send_one(r["chat_id"], text, bool(r["html"]), retries=1):
                db.outbox_done(r["id"], "sent")
                for aid in json.loads(r["alert_ids"] or "[]"):
                    db.mark_sent(int(aid))
                out["sent"] += 1
            else:
                db.outbox_attempt(r["id"])
                out["failed"] += 1
        if any(out.values()):
            log.info("telegram outbox: %s", out)
        return out

    # ---- single recipient, with retries
    def _sleep(self, s: float) -> None:
        time.sleep(s)

    def _post(self, data: Dict[str, Any]):
        return requests.post(f"https://api.telegram.org/bot{self._token}/sendMessage", data=data, timeout=15)

    def _send_one(self, chat: str, text: str, html: bool, retries: Optional[int] = None) -> bool:
        if html:
            from .friendly import tg_safe
            text = tg_safe(text)                   # escape stray '<' / '&' so Telegram never 400s on parse
        for part in chunk_text(text, self.TG_LIMIT):
            if not self._send_part(chat, part, html, self.RETRIES if retries is None else retries):
                return False
        return True

    def _send_part(self, chat: str, text: str, html: bool, retries: int) -> bool:
        who = "primary" if chat == str(self._chat) else "extra"
        for attempt in range(max(1, retries)):
            wait = None
            try:
                data = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
                if html:
                    data["parse_mode"] = "HTML"
                r = self._post(data)
                if r.status_code == 200:
                    return True
                if r.status_code == 429:
                    try:
                        wait = float(((r.json() or {}).get("parameters") or {}).get("retry_after") or 1)
                    except Exception:  # noqa: BLE001
                        wait = 1.0
                    wait = min(wait, self.MAX_RETRY_AFTER_S)
                elif r.status_code >= 500:
                    pass                               # Telegram hiccup -> retry
                else:
                    if html:                           # formatting rejected -> plain text, never drop it
                        from .friendly import plain
                        r = self._post({"chat_id": chat, "text": plain(text), "disable_web_page_preview": True})
                        if r.status_code == 200:
                            return True
                    log.error("Telegram send failed (%s): HTTP %s (not retried)", who, r.status_code)
                    return False
                log.warning("Telegram send (%s) attempt %d/%d: HTTP %s", who, attempt + 1, retries, r.status_code)
            except requests.RequestException as e:
                log.warning("Telegram send (%s) attempt %d/%d error: %s", who, attempt + 1, retries,
                            str(e).replace(self._token, "***"))
            if attempt + 1 < retries:
                self._sleep(wait if wait is not None else self.BACKOFF_S[min(attempt, len(self.BACKOFF_S) - 1)])
        log.error("Telegram send failed (%s) after %d attempt(s)", who, max(1, retries))
        return False


def chunk_text(text: str, limit: int = 4096) -> List[str]:
    """Split on line breaks so each part fits Telegram's 4,096-character limit (HTML tags are per line)."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts
 
