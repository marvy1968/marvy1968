"""2026-11-05 (Marvin approved; NCAAW + bet card added same day, "fix all"): NCAAB/NCAAW 'Predict Max' card qualified-play fixes. Hooked from run_marv.py
(marv-bot / marv-bot-late daily card runs). Gates run on the predictions right before bridge.export (so the
bridge/predictions.json 'qualified' list matches the card) and again (idempotent) before format_card.
Order: model gap -> one per game -> nightly cap. Track-only picks get a 'track-only: <reason>' veto (so they are
NOT in picks.json / qualified plays), are logged to state/ncaab_track_only.json and graded there, and are shown on
a separate '🗂 Tracked (not plays)' line.
 1) ncaab_model_gap   : |model margin - market margin| > 10 (spread/ML picks) or |model total - market total| > 12 (totals)
 2) ncaab_one_per_game: keep the single best play per game (edge, then EV)
 3) ncaab_night_cap   : 15% bankroll total per ET night, best plays first; next play reduced to fit (>=0.5%) else track-only
 4) paper-only until the Nov 17 review: NCAAB card always renders paper mode (Marv never places bets; Telegram only)
OFF (all): MARV_NCAAB_CARD_FIX=off | /opt/marv-feed/MARV_NCAAB_CARD_FIX_OFF
OFF (each): MARV_NCAAB_GAP / MARV_NCAAB_ONE_PER_GAME / MARV_NCAAB_NIGHT_CAP / MARV_NCAAB_PAPER =off | same-named _OFF file
Tunables: MARV_NCAAB_GAP_MARGIN=10, MARV_NCAAB_GAP_TOTAL=12, MARV_NCAAB_CAP=0.15, MARV_NCAAB_PAPER_UNTIL=2026-11-17
NCAAW: identical rules, separate 15% night cap, ledger state/ncaaw_track_only.json; every switch/tunable above with
NCAAW in place of NCAAB (MARV_NCAAW_CARD_FIX=off | /opt/marv-feed/MARV_NCAAW_CARD_FIX_OFF). Reason tags ncaaw_*.
Bet card / daily plan (marv.betcard): track-only picks count as hard vetoes, and NCAAB/NCAAW bet-card entries are
limited to the qualified Predict Max plays (units = card stake %), plus a one-line paper-only note."""
import json, logging, os, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York"); log = logging.getLogger("marv_ncaab_card")
TAG = "track-only: "


def feed_root():
    """Switch files. Unset env keeps the feed directory the service already uses."""
    return Path(os.environ.get("MARV_FEED_ROOT", "/opt/marv-feed"))


def on(name, sport="ncaab"):
    name = name.replace("NCAAB", sport.upper()); main = f"MARV_{sport.upper()}_CARD_FIX"
    root = feed_root()
    f = lambda n: os.environ.get(n, "on").strip().lower() not in ("off", "0", "false", "no") and not (root / f"{n}_OFF").exists()
    return f(main) and (name == main or f(name))


SPORTS = ("ncaab", "ncaaw")


def _f(n, d, sport="ncaab"):
    n = n.replace("NCAAB", sport.upper())
    try: return float(os.environ.get(n, d))
    except ValueError: return float(d)


def stake_frac(pk, fraction=0.25, cap=0.03):  # same as marv.telegram.stake
    b = pk.price / 100 if pk.price > 0 else 100 / -pk.price
    k = (b * pk.prob - (1 - pk.prob)) / b
    return 0.0 if k <= 0 else min(k * fraction, cap)


def _track(pred, pk, reason, detail=""):
    pk.vetoes.append(TAG + reason + (f" ({detail})" if detail else ""))
    pk._ncaab_track = reason


def _row(p, pk):
    return {"game_id": p.game.id, "market": pk.market, "side": pk.side, "active": bool(pk.active),
            "track": getattr(pk, "_ncaab_track", None), "preserved": bool(getattr(pk, "_allocation_preserved", False)),
            "stake": getattr(pk, "_ncaab_stake", None), "edge": pk.edge}


def gate(preds, prior=None, sport="ncaab", preserved=None, stages=None):
    """Mutates picks in place. Returns list of (pred, pk, reason). prior: {ET date: stake already on qualified
    plays for that night in games not in `preds`} (a late slate does not include the morning games).

    `preserved` pins an allocation already emitted for a game on this slate. Those picks are not
    re-labeled overflow, gap, or one-per-game, and their original stake stays in the night total.
    Caps, gap sizes, and one-per-game for every other pick are unchanged. Stages are recorded
    when `stages` is a list; this function does not write a ledger.
    """
    O = lambda n: on(n, sport); F = lambda n, d: _f(n, d, sport)
    if not O("MARV_NCAAB_CARD_FIX"):
        return []
    out, preserved, stages = [], preserved or {}, stages if stages is not None else []
    live = lambda p: [pk for pk in p.picks if pk.active and not getattr(pk, "_allocation_preserved", False)]

    def snap(stage):
        stages.append({"stage": stage, "sport": sport, "rows": [_row(p, pk) for p in preds for pk in p.picks
                       if pk.market in ("ml", "spread", "total")]})

    for p in preds:
        for pk in p.picks:
            prev = preserved.get((str(p.game.id), pk.market, str(pk.side).lower()))
            if not prev:
                continue
            night = p.game.start.astimezone(ET).date()
            prev_night = prev.get("et_date")
            if hasattr(prev_night, "isoformat"):
                prev_night = prev_night
            elif isinstance(prev_night, str):
                from datetime import date
                prev_night = date.fromisoformat(prev_night[:10])
            if prev_night not in (None, night):
                continue
            pk._allocation_preserved = True
            pk._decision_id = prev.get("decision_id")
            if prev.get("stake_fraction") is not None:
                pk._ncaab_stake = float(prev["stake_fraction"])
    snap("preserved")
    if O("MARV_NCAAB_GAP"):
        gm, gt = F("MARV_NCAAB_GAP_MARGIN", 10), F("MARV_NCAAB_GAP_TOTAL", 12)
        for p in preds:
            o = p.game.odds
            if not o: continue
            for pk in live(p):
                if pk.market in ("spread", "ml") and o.spread is not None:
                    gap = abs(p.model_margin - (-o.spread))
                    if gap > gm: _track(p, pk, f"{sport}_model_gap", f"model margin {p.model_margin:+.1f} vs market {-o.spread:+.1f}"); out.append((p, pk, f"{sport}_model_gap"))
                elif pk.market == "total" and o.total is not None:
                    gap = abs(p.model_total - o.total)
                    if gap > gt: _track(p, pk, f"{sport}_model_gap", f"model total {p.model_total:.1f} vs market {o.total:g}"); out.append((p, pk, f"{sport}_model_gap"))
        snap("gap")
    if O("MARV_NCAAB_ONE_PER_GAME"):
        for p in preds:
            held = [pk for pk in p.picks if getattr(pk, "_allocation_preserved", False)]
            act = sorted(live(p), key=lambda k: (-k.edge, -k.ev))
            if held:
                for pk in act:
                    _track(p, pk, f"{sport}_one_per_game", f"kept emitted: {held[0].market} {held[0].side}")
                    out.append((p, pk, f"{sport}_one_per_game"))
            else:
                for pk in act[1:]:
                    _track(p, pk, f"{sport}_one_per_game", f"best: {act[0].market} {act[0].side}"); out.append((p, pk, f"{sport}_one_per_game"))
        snap("one_per_game")
    if O("MARV_NCAAB_NIGHT_CAP"):
        cap = F("MARV_NCAAB_CAP", 0.15); used = dict(prior or {})
        for p in preds:
            night = p.game.start.astimezone(ET).date()
            for pk in p.picks:
                if getattr(pk, "_allocation_preserved", False):
                    used[night] = used.get(night, 0.0) + float(getattr(pk, "_ncaab_stake", 0.0) or 0.0)
        plays = sorted(((p, pk) for p in preds for pk in live(p)), key=lambda x: (-x[1].edge, -x[1].ev))
        for p, pk in plays:
            night = p.game.start.astimezone(ET).date()
            s = stake_frac(pk); u = used.get(night, 0.0); room = round(cap - u, 4)
            if s <= room + 1e-9:
                used[night] = u + s
            elif room >= 0.005:
                pk._ncaab_stake = room; used[night] = u + room
            else:
                _track(p, pk, f"{sport}_night_cap", f"{cap:.0%} night cap reached"); out.append((p, pk, f"{sport}_night_cap"))
        snap("night_cap")
    gate.last_stages = stages
    return out


_TRACKED = {}  # id(pk) -> (pred, pk, reason) from this run


_RUN = {}


def _exposure(preds, sport, state_dir):
    """Preserved in-slate allocations and prior stake for games not on this slate. Reads only."""
    from datetime import date
    from marv import decision_audit as audit
    path = Path(state_dir)
    ids = {str(p.game.id) for p in preds}

    def et_of(value):
        if value is None:
            return None
        if hasattr(value, "astimezone"):
            return value.astimezone(ET).date()
        text = str(value)
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(ET).date()
        return date.fromisoformat(text[:10])

    try:
        legacy = json.loads((path / "picks.json").read_text()) if (path / "picks.json").exists() else []
    except Exception:
        legacy = []
    return audit.exposure_context(sport, ids, legacy, lambda row: stake_frac(
        __import__("types").SimpleNamespace(price=row["price"], prob=row["prob"])), audit.events(path), et_of)


def _audit():
    from marv import decision_audit as audit
    return audit


def _record_stages(state_dir, sport):
    """Gate snapshots only. They do not reserve stake and do not block a later first send."""
    if "--dry-run" in sys.argv:
        return
    audit = _audit()
    state = Path(state_dir)
    run_id = _RUN.setdefault(sport, audit.new_run_id())
    for stage in getattr(gate, "last_stages", []) or []:
        if stage.get("sport") == sport:
            audit.record_stage(state, run_id=run_id, sport=sport, stage=stage["stage"], rows=stage["rows"])


def _emitted_keys(state_dir, sport):
    keys = set()
    audit = _audit()
    for row in audit.counting_exposure(audit.events(state_dir)):
        if row.get("sport") != sport:
            continue
        keys.add((str(row.get("game_id")), row.get("market"), str(row.get("side", "")).lower()))
    return keys


def _commit(state_dir, sport, preds):
    """Record the qualified stake after a send that was not a pre-request failure.

    The first allocation row is kept. A repeat appends a reevaluation and does not add stake.
    """
    if "--dry-run" in sys.argv:
        return
    audit = _audit()
    state = Path(state_dir)
    run_id = _RUN.setdefault(sport, audit.new_run_id())
    _record_stages(state, sport)
    for p in preds:
        for pk in p.picks:
            if pk.market not in ("ml", "spread", "total"):
                continue
            preserved = bool(getattr(pk, "_allocation_preserved", False))
            if not preserved and (not pk.active or getattr(pk, "_ncaab_track", None)):
                continue
            stake = getattr(pk, "_ncaab_stake", None)
            if stake is None:
                stake = stake_frac(pk)
            audit.record_allocation(
                state, run_id=run_id, sport=sport, game_id=p.game.id, market=pk.market, side=pk.side,
                et_date=p.game.start.astimezone(ET).date(), stake_fraction=float(stake),
                game=f"{p.game.away} @ {p.game.home}", home=p.game.home, away=p.game.away,
                line=pk.line, price=pk.price, preserved=preserved)


def _gate_once(preds, sport="ncaab", state_dir=None):
    fresh = [p for p in preds if not getattr(p, "_ncaab_gated", False)]
    for p in fresh: p._ncaab_gated = True
    prior, preserved = {}, {}
    if state_dir:
        try:
            preserved, prior = _exposure(fresh, sport, state_dir)
        except Exception:
            log.exception("exposure context failed")
            prior = _prior(fresh, sport=sport)
    else:
        prior = _prior(fresh, sport=sport)
    stages = []
    for p, pk, r in gate(fresh, prior, sport, preserved=preserved, stages=stages):
        _TRACKED[id(pk)] = (p, pk, r)


def _prior(preds, path=Path("/opt/marv-bot/state/picks.json"), sport="ncaab"):
    try:
        ids = {p.game.id for p in preds}; out = {}
        from types import SimpleNamespace as NS
        for e in json.loads(path.read_text()):
            if e.get("sport") != sport or e.get("game_id") in ids: continue
            d = datetime.fromisoformat(e["start"]).astimezone(ET).date()
            out[d] = out.get(d, 0.0) + (e.get(f"{sport}_stake") or stake_frac(NS(price=e["price"], prob=e["prob"])))
        return out
    except Exception:
        log.exception("ncaab prior stake read failed"); return {}


def _log_tracked(state_dir, preds, sport="ncaab"):
    """Append a track row only when that game/market/side is new. Existing rows are not relabeled."""
    import fcntl
    path = Path(state_dir) / f"{sport}_track_only.json"
    lockp = path.with_suffix(".lock")
    lockp.parent.mkdir(parents=True, exist_ok=True)
    with lockp.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: ledger = json.loads(path.read_text()) if path.exists() else []
        except Exception: ledger = []
        idx = {(e["game_id"], e["market"], e["side"]): e for e in ledger}
        now = datetime.now(timezone.utc).isoformat(); ids = {id(pk) for p in preds for pk in p.picks}
        added = False
        for k, (p, pk, r) in list(_TRACKED.items()):
            if k not in ids: continue
            key = (p.game.id, pk.market, pk.side)
            if key in idx: continue
            ledger.append({"sport": sport, "game_id": p.game.id, "start": p.game.start.isoformat(), "home": p.game.home, "away": p.game.away,
                           "market": pk.market, "side": pk.side, "line": pk.line, "price": pk.price, "prob": pk.prob, "ev": pk.ev, "edge": pk.edge,
                           "track_only": True, "track_gate": r, "vetoes": list(pk.vetoes), "stake": 0.0, "logged_at": now, "result": None})
            idx[key] = ledger[-1]
            added = True
        if added:
            tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(ledger, indent=1)); tmp.replace(path)


def _grade_tracked(state_dir, finished, sport="ncaab"):
    from marv.engine import grade
    from marv.models import Pick
    path = Path(state_dir) / f"{sport}_track_only.json"
    if not path.exists(): return 0
    ledger = json.loads(path.read_text()); by = {g.id: g for g in finished if g.completed}; n = 0
    for e in ledger:
        if e.get("result") is None and e["game_id"] in by:
            e["result"] = grade(Pick(e["market"], e["side"], e["line"], e["price"], e["prob"], e["ev"], e["edge"]), by[e["game_id"]]); n += 1
    if n: path.write_text(json.dumps(ledger, indent=1))
    return n


def render(text, preds, sp=None):
    from marv.telegram import describe, esc
    rows = [(p, pk) for p in preds for pk in p.picks if getattr(pk, "_ncaab_track", None)]
    if not rows: return text
    lines = ["<b>🗂 Tracked (not plays)</b> — logged &amp; graded, no stake"]
    for p, pk in sorted(rows, key=lambda x: -x[1].edge):
        lines.append(f"• {describe(pk, sp or _SPORT[0])} — {esc(p.game.away)} @ {esc(p.game.home)}: {esc(pk._ncaab_track)}")
    blk = "\n".join(lines); foot = "\n\n<i>Model output"
    return text.replace(foot, "\n\n" + blk + foot, 1) if foot in text else text + "\n\n" + blk


_SPORT = [None]


def paper_on(now=None, sport="ncaab"):
    if not on("MARV_NCAAB_PAPER", sport): return False
    until = os.environ.get(f"MARV_{sport.upper()}_PAPER_UNTIL", "2026-11-17")
    return (now or datetime.now(ET)).astimezone(ET).date().isoformat() < until or (feed_root() / f"MARV_{sport.upper()}_PAPER_KEEP").exists()


def apply():
    import marv.main as M, marv.bridge as BR, marv.telegram as T
    from marv.state import Store
    if getattr(M.format_card, "_ncaab", False): return
    ex0, fc0, st0, lp0, rs0 = BR.export, M.format_card, T.stake, Store.log_picks, Store.resolve

    def export(state_dir, sport, preds):
        if str(sport).lower() in SPORTS:
            try: _gate_once(preds, str(sport).lower(), state_dir=state_dir)
            except Exception: log.exception("ncaab gate failed (export)")
        return ex0(state_dir, sport, preds)

    def stake(pk, *a, **k):
        r = getattr(pk, "_ncaab_stake", None)
        return f"stake: {r:.1%} of bankroll (cut to fit 15% night cap)" if r is not None else st0(pk, *a, **k)

    def format_card(sport, preds, record=None, now=None, paper=False, label=""):
        k = getattr(sport, "key", "")
        if k not in SPORTS:
            return fc0(sport, preds, record, now, paper=paper, label=label)
        try: _gate_once(preds, k, state_dir=os.environ.get("MARV_STATE_DIR", "/opt/marv-bot/state"))
        except Exception: log.exception("%s gate failed (card)", k)
        _SPORT[0] = sport
        hidden = {id(p): [pk for pk in p.picks if getattr(pk, "_ncaab_track", None)] for p in preds}
        for p in preds: p.picks = [pk for pk in p.picks if not getattr(pk, "_ncaab_track", None)]
        try:
            text = fc0(sport, preds, record, now, paper=paper or paper_on(now, k), label=label)
        finally:
            for p in preds: p.picks = p.picks + hidden[id(p)]
        try:
            text = render(text, preds, sport)
            if paper_on(now, k): text = text.replace("until the record proves an edge.</i>", f"until the record proves an edge. {k.upper()} paper-only until the Nov 17 review.</i>", 1)
        except Exception: log.exception("ncaab tracked render failed")
        return text

    def log_picks(self, sport, preds):
        r = lp0(self, sport, preds)
        if sport in SPORTS and on("MARV_NCAAB_CARD_FIX", sport):
            try:
                cut = {(p.game.id, pk.market): pk._ncaab_stake for p in preds for pk in p.picks
                       if getattr(pk, "_ncaab_stake", None) is not None and not getattr(pk, "_allocation_preserved", False)}
                if cut:
                    f = _sd(self) / "picks.json"; L = json.loads(f.read_text())
                    for e in L:
                        if e.get("sport") == sport and (e["game_id"], e["market"]) in cut: e[f"{sport}_stake"] = cut[(e["game_id"], e["market"])]
                    f.write_text(json.dumps(L, indent=1))
            except Exception: log.exception("ncaab stake note failed")
            try: _log_tracked(_sd(self), preds, sport)
            except Exception: log.exception("track-only log failed")
            try: _commit(_sd(self), sport, preds)
            except Exception: log.exception("decision audit failed")
        return r

    np0 = Store.new_picks

    def new_picks(self, sport, preds):  # called on every card run; log track-only picks unless --dry-run
        n = np0(self, sport, preds)
        if sport in SPORTS and on("MARV_NCAAB_CARD_FIX", sport) and "--dry-run" not in sys.argv:
            try: _log_tracked(_sd(self), preds, sport)
            except Exception: log.exception("track-only log failed")
            try: _record_stages(_sd(self), sport)
            except Exception: log.exception("gate snapshot failed")
            try:
                emitted = _emitted_keys(_sd(self), sport)
                ledger = json.loads((_sd(self) / "picks.json").read_text()) if (_sd(self) / "picks.json").exists() else []
            except Exception:
                log.exception("emitted-key read failed")
                emitted, ledger = set(), None
            if emitted and ledger is not None:
                logged = {(str(e.get("game_id")), e.get("market"), str(e.get("side", "")).lower())
                          for e in ledger if e.get("sport") == sport}
                n = 0
                for p in preds:
                    for pk in p.picks:
                        if not pk.active or getattr(pk, "_ncaab_track", None) or pk.market not in ("ml", "spread", "total"):
                            continue
                        key = (str(p.game.id), pk.market, str(pk.side).lower())
                        if key in emitted or key in logged:
                            continue
                        n += 1
        return n

    def resolve(self, sport, finished):
        r = rs0(self, sport, finished)
        if sport in SPORTS:
            try: _grade_tracked(_sd(self), finished, sport)
            except Exception: log.exception("ncaab track-only grade failed")
        return r

    def _emit(state, sport, preds):
        if str(sport).lower() in SPORTS and "--dry-run" not in sys.argv:
            try: _commit(state, str(sport).lower(), preds)
            except Exception: log.exception("emitted allocation failed")

    def _reserve_rows(state, sport, preds, intent_id, run_id):
        """Stake rows for the intent. No audit writes: deliver() stores them with the intent."""
        sport = str(sport).lower()
        if sport not in SPORTS or "--dry-run" in sys.argv:
            return []
        rows = []
        audit = _audit()
        for p in preds or []:
            for pk in getattr(p, "picks", []) or []:
                if pk.market not in ("ml", "spread", "total"):
                    continue
                preserved = bool(getattr(pk, "_allocation_preserved", False))
                if not preserved and (not pk.active or getattr(pk, "_ncaab_track", None)):
                    continue
                stake = getattr(pk, "_ncaab_stake", None)
                if stake is None:
                    stake = stake_frac(pk)
                et = p.game.start.astimezone(ET).date()
                rows.append({
                    "decision_id": audit.decision_id(ledger="qualified", sport=sport, game_id=p.game.id,
                                                    market=pk.market, side=pk.side, et_date=et),
                    "sport": sport, "game_id": p.game.id, "market": pk.market, "side": pk.side,
                    "et_date": et.isoformat(), "stake_fraction": float(stake), "ledger": "qualified",
                })
        return rows

    import marv.decision_audit as _audit_mod
    _audit_mod.commit_emitted_card = _emit
    _audit_mod.reserve_card_stakes = _reserve_rows
    format_card._ncaab = True
    BR.export = export; T.stake = stake; M.format_card = format_card
    Store.log_picks = log_picks; Store.new_picks = new_picks; Store.resolve = resolve
    try: apply_betcard()
    except Exception: log.exception("betcard hook not applied")


def _sd(store):
    for a in ("state_dir", "dir", "path", "root"):
        v = getattr(store, a, None)
        if v: return Path(v)
    return Path("/opt/marv-bot/state")


# ---------- 2026-11-05 bet card / daily plan (".bak-20261105-betcard") ----------
_ALLOWED = {}  # sport -> {(game, market, side): units} qualified Predict Max plays seen this run


def _bk(sport, game, market, side):
    return (sport, game, market, str(side).lower())


def apply_betcard():
    """Bet card + daily plan must match the NCAAB/NCAAW Predict Max cards: gate preds, treat track-only as a hard
    veto, keep only qualified card plays for these sports (units = card stake %), add a paper-only line."""
    import marv.betcard as B
    if getattr(B.select, "_ncaab", False): return
    if "track-only" not in B.HARD: B.HARD = tuple(B.HARD) + ("track-only",)
    c0, s0, t0 = B.candidates, B.select, B.text

    def candidates(sport, preds, projections=None):
        if sport in SPORTS and on("MARV_NCAAB_CARD_FIX", sport):
            try:
                _gate_once(preds, sport, state_dir=os.environ.get("MARV_STATE_DIR", "/opt/marv-bot/state"))
                al = _ALLOWED.setdefault(sport, {})
                for p in preds:
                    for pk in p.picks:
                        if pk.active and pk.market in ("ml", "spread", "total"):
                            st = getattr(pk, "_ncaab_stake", None)
                            al[_bk(sport, f"{p.game.away} @ {p.game.home}", pk.market, pk.side)] = round((st if st is not None else stake_frac(pk)) * 100, 1)
            except Exception: log.exception("%s betcard gate failed", sport)
        return c0(sport, preds, projections)

    def select(cands, *a, **k):
        out = []
        for b in cands:
            if b.sport in SPORTS and on("MARV_NCAAB_CARD_FIX", b.sport):
                u = _ALLOWED.get(b.sport, {}).get(_bk(b.sport, b.game, b.market, b.side))
                if u is None: continue  # not a qualified Predict Max play (track-only / signal / lean)
                b.tier = "HIGH"; b.units = u
            out.append(b)
        return s0(out, *a, **k)

    def text(bets, when):
        t = t0(bets, when)
        sp = sorted({b.sport for b in bets if b.sport in SPORTS and paper_on(when, b.sport)})
        if sp:
            note = f"📝 {'/'.join(x.upper() for x in sp)}: same plays as the Predict Max card (15% night cap), paper-only until the Nov 17 review."
            t = t.replace("<i>Paper-tracked and graded.", note + "\n<i>Paper-tracked and graded.", 1)
        return t

    select._ncaab = True
    B.candidates, B.select, B.text = candidates, select, text

