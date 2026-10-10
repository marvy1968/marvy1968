"""PDF reports in the Marv templates.

  * weekly_chart  - "Marv <Sport> Max Predict - Weekly ML / O-U Chart" (landscape letter):
                    top-10 games by model confidence, market numbers, Marv Max status, prediction.
  * sport_sheet   - "Marv <Sport> Predict Max" one-pager (A4): architecture, veto logic and the
                    *real* walk-forward backtest table from backtest_results.json.

Integrity rules carried over from the templates: a game is only "CERTIFIED" when it passed every
veto in a completed run, and backtest numbers are the held-out results, never targets.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import proven
from .models import Pick, Prediction
from .sports import Sport

ET = ZoneInfo("America/New_York")
NAVY = colors.HexColor("#1f2a4f")
BLUE = colors.HexColor("#2b4aa0")
STRIPE = colors.HexColor("#eef0f5")
GRID = colors.HexColor("#9aa0ad")
RESULTS = Path(__file__).with_name("backtest_results.json")
SS = getSampleStyleSheet()
CELL = ParagraphStyle("cell", parent=SS["Normal"], fontName="Helvetica", fontSize=8.5, leading=10.5, alignment=TA_CENTER)
CELL_HEAD = ParagraphStyle("head", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)


def _price(p: float | None) -> str:
    return "-" if p is None else f"{p:+.0f}"


def _short(team: str) -> str:
    return team if len(team) <= 22 else team[:21] + "…"


def _row_for(pred: Prediction, rank: int) -> list:
    g, o = pred.game, pred.game.odds
    ml = f"{_short(g.away)} {_price(o.away_ml)} / {_short(g.home)} {_price(o.home_ml)}" if o and o.home_ml else "-"
    ou = f"{o.total:g}" if o and o.total is not None else "-"
    active = [pk for pk in pred.picks if pk.active]
    if active:
        status = "CERTIFIED PLAY"
        pred_txt = "<br/>".join(_pick_text(pk, g.sport) for pk in active)
    else:
        reasons = sorted({v for pk in pred.picks for v in pk.vetoes}, key=len)
        status = "VETOED"
        fav = g.home if pred.home_win >= 0.5 else g.away
        conf = max(pred.home_win, 1 - pred.home_win)
        conf_txt = f"{conf:.0%}" if proven.show_prob(g.sport, "ml") else f"({proven.UNPROVEN})"
        pred_txt = f"Lean {_short(fav)} {conf_txt}, total {pred.model_total:.1f} · no play"
        if reasons:
            status += f"<br/><font size=7>{reasons[0][:48]}</font>"
    return [str(rank), f"{_short(g.away)} @ {_short(g.home)}", ml, ou, status, pred_txt]


def _pick_text(pk: Pick, sport: str | None = None) -> str:
    if not proven.show_prob(sport, pk.market):
        what = (f"{pk.side} ML" if pk.market == "ml" else f"{pk.side} {pk.line:g}" if pk.market == "total"
                else f"{pk.side} {pk.line:+g}")
        return f"<b>{what}</b> ({proven.UNPROVEN})"
    if pk.market == "ml":
        return f"<b>{pk.side} ML</b> ({pk.prob:.0%})"
    if pk.market == "total":
        return f"<b>{pk.side} {pk.line:g}</b> ({pk.prob:.0%})"
    return f"<b>{pk.side} {pk.line:+g}</b> ({pk.prob:.0%})"


def weekly_chart(sport: Sport, preds: list[Prediction], out: Path, now: datetime | None = None,
                 paper: bool = True, top: int = 10) -> Path:
    now = (now or datetime.now(ET)).astimezone(ET)
    ranked = sorted(preds, key=lambda p: (not any(pk.active for pk in p.picks), -max(p.home_win, 1 - p.home_win)))[:top]
    doc = SimpleDocTemplate(str(out), pagesize=landscape(letter), leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    title = ParagraphStyle("t", parent=SS["Title"], fontName="Helvetica-Bold", fontSize=18, spaceAfter=4)
    sub = ParagraphStyle("s", parent=SS["Normal"], fontSize=8.5, alignment=TA_CENTER, leading=11)
    certified = sum(1 for p in ranked if any(pk.active for pk in p.picks))
    story = [
        Paragraph(f"Marv {sport.name} Max Predict - Weekly ML / O-U Chart", title),
        Paragraph(f"Generated {now:%a %b %d, %Y %I:%M %p} ET from a completed Marv run (stats experts, Monte Carlo, "
                  f"injury and freshness checks, veto stack). {len(preds)} games modeled; top {len(ranked)} by "
                  f"confidence shown; {certified} certified." + (" Paper-trading mode." if paper else ""), sub),
        Spacer(1, 10),
    ]
    head = ["Rank", "Matchup", "Moneyline", "O/U", "Marv Max Status", "Prediction"]
    rows = [[Paragraph(h, CELL_HEAD) for h in head]]
    for i, p in enumerate(ranked, 1):
        rows.append([Paragraph(c, CELL) for c in _row_for(p, i)])
    if not ranked:
        rows.append([Paragraph(c, CELL) for c in ["-", "No games with market numbers in this window", "-", "-", "-", "-"]])
    widths = [0.5, 2.3, 2.6, 0.6, 1.6, 2.2]
    t = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.5, GRID),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    for r in range(1, len(rows)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), STRIPE))
        if "CERTIFIED" in rows[r][4].text:
            style.append(("TEXTCOLOR", (4, r), (4, r), colors.HexColor("#1b7f3a")))
    t.setStyle(TableStyle(style))
    story += [t, Spacer(1, 12)]
    res = sport_results(sport.key)
    note = ("<b>Integrity note:</b> Only CERTIFIED rows passed every veto (expert agreement, confidence floor, "
            "injuries, stale data). Everything else is a lean, not a pick.")
    if proven.gated(sport.key):
        note += (" No % is shown: no game-pick logic for this sport has passed the proven bar (walk-forward, 100+ bets, "
                 "positive ROI after the vig). High win rates come from heavy favorites and lost money at Bovada.")
    elif res and res.get("ml"):
        ml = res["ml"]
        note += (f" Backtested accuracy of certified moneylines on unseen seasons: {ml['test_acc']:.1%} "
                 f"over {ml['test_picks']} picks; heavy favorites, so accuracy is not profit.")
    story.append(Paragraph(note, ParagraphStyle("n", parent=SS["Normal"], fontSize=8.5, leading=11)))
    doc.build(story)
    return out


# ---------- sport sheet ----------

ARCH = {
    "basketball": ("Every team box-score stat (shooting, rebounding, turnovers, fouls, plus four factors and offensive/"
                   "defensive rating per 100 possessions) feeds three experts: a weighted stat formula, a random "
                   "forest and opponent-adjusted power ratings. Their consensus score drives {sims} Monte Carlo "
                   "simulations with correlated pace and overtime."),
    "football": ("All team stats (passing, rushing, EPA, passer rating, defense, special teams, weather and rest) feed "
                 "three experts: a weighted stat formula, a random forest and opponent-adjusted power ratings with "
                 "blowout dampening. The consensus drives {sims} drive-by-drive Monte Carlo simulations with "
                 "garbage-time effects."),
}


def sport_results(key: str) -> dict | None:
    if not RESULTS.exists():
        return None
    return json.loads(RESULTS.read_text()).get(key)


def sport_sheet(sport: Sport, out: Path, simulations: int = 20000) -> Path:
    res = sport_results(sport.key) or {}
    kind = "football" if sport.key in ("nfl", "cfb") else "basketball"
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.6 * inch)
    banner_t = ParagraphStyle("bt", fontName="Helvetica-Bold", fontSize=20, textColor=colors.white, leading=24)
    banner_s = ParagraphStyle("bs", fontName="Helvetica", fontSize=9, textColor=colors.white, leading=12)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, textColor=BLUE, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=14)
    banner = Table([[Paragraph(f"Marv {sport.name} Predict Max", banner_t)],
                    [Paragraph("Stats-expert consensus · Monte Carlo · Edge-Filter Veto · walk-forward backtest", banner_s)]],
                   colWidths=[6.8 * inch])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 14),
                                ("TOPPADDING", (0, 0), (-1, 0), 14), ("BOTTOMPADDING", (0, -1), (-1, -1), 14)]))
    story = [banner, Paragraph("1. Simulation Architecture", h2),
             Paragraph(ARCH[kind].format(sims=f"{simulations:,}"), body),
             Paragraph("2. Edge-Filter &amp; Veto Module Logic", h2)]
    floor = res.get("ml", {}).get("floor")
    story.append(Paragraph(
        "A play is certified only when all three experts agree, model confidence clears the backtested floor"
        + (f" ({floor:.0%} for moneylines)" if floor else "")
        + ", every team has current stats, and no key player is newly injured. Games failing any check are "
          "vetoed" + (f" (about {res['pass_rate']:.0%} of slates)." if res.get("pass_rate") else "."), body))
    story.append(Paragraph("3. Historical Backtest Performance (held-out seasons)", h2))
    head = ["Market", "Sample", "Certified Plays", "Win Rate", "Target 90%+"]
    rows = [[Paragraph(h, CELL_HEAD) for h in head]]
    for label, key in (("Moneyline", "ml"), ("Over / Under", "ou")):
        m = res.get(key)
        if m:
            met = "Met" if m["test_acc"] >= 0.9 else "Not met"
            rows.append([Paragraph(c, CELL) for c in (label, f"{m['games']:,} games ({res['test_seasons']})",
                                                       f"{m['test_picks']:,}", f"{m['test_acc']:.1%}", met)])
        else:
            rows.append([Paragraph(c, CELL) for c in (label, "-", "-", "-", m_reason(res, key))])
    t = Table(rows, colWidths=[1.3 * inch, 2.0 * inch, 1.2 * inch, 1.0 * inch, 1.3 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, GRID),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [STRIPE, colors.white]),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [t, Paragraph("4. Core Engineering Takeaways", h2)]
    for line in res.get("takeaways", ["Run `python -m marv stats-backtest` for this sport to fill in results."]):
        story.append(Paragraph("• " + line, body))
    foot = ParagraphStyle("f", fontName="Helvetica", fontSize=7.5, textColor=colors.grey, spaceBefore=16)
    story.append(Paragraph(f"Marv {sport.name} Predict Max · generated {datetime.now(ET):%b %d, %Y} · "
                           "accuracy figures are walk-forward results on seasons the tuning never saw", foot))
    doc.build(story)
    return out


def m_reason(res: dict, key: str) -> str:
    return res.get(f"{key}_note", "Not backtestable")
