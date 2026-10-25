"""Owner 2026-10-25 ("predict the best buy and sell zones from the previous setups of a particular forex pair and / or stock"): a learned
zone model, graded only on zone touches it never saw.

For each instrument, walking forward bar by bar (nothing from the future is used to describe a zone):
  ZONES    swing highs and lows (wing bars each side, known `wing` bars later) over the last `lookback` bars, merged within `merge` x ATR into
           levels; a level with >= 2 pivots is a candidate. Buy zones sit under price (support), sell zones over it: sell = the same code on the
           mirrored chart (prices negated, highs and lows swapped).
  EVENT    a bar probes a level from above (low within 0.15 ATR of it) and closes back on or above it, the previous close was above it, and the
           close is within 1 ATR of it. One event per level per 6 bars.
  OUTCOME  1 = bounce: within `horizon` bars price reaches level + 1.5 ATR before level - 1.0 ATR (a bar that does both counts as a break);
           0 = break; timeouts are left out of the rates but valued by their close in the R figures.
  FEATURES touches in the level, bars since its newest pivot, how past events at THIS level of THIS instrument turned out (only events whose
           outcome was already known by now), the instrument's own running bounce rate, trend (close vs the 50-bar average, in ATR), distance of
           the close from the level, volatility, distance to a round number.
  MODEL    logistic regression trained on the first `train_frac` of the events (by time), scored on the rest.
  R        the trade 'buy at the close, stop 1 ATR under the level, target 1.5 ATR over it', less 0.05 ATR of costs.
`zone_report` prints the pooled and per-instrument results and, for the last bar of each instrument, the model's next buy and sell zones."""

import numpy as np
import pandas as pd

WING, LOOKBACK, MERGE, HORIZON = 3, 250, 0.35, 24
UP, DOWN, COST_ATR = 1.5, 1.0, 0.05
FEATURES = ["touches", "age", "local_rate", "n_local", "instr_rate", "trend", "dist", "atr_pct", "round_dist"]


def _atr(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def _pivots(h, l):
    """[(price, index, known_at)]: swing highs and lows, each known `WING` bars after its bar."""
    n = len(h)
    out = []
    for j in range(WING, n - WING):
        known = j + WING
        if h[j] == h[j - WING:j + WING + 1].max():
            out.append((h[j], j, known))
        if l[j] == l[j - WING:j + WING + 1].min():
            out.append((l[j], j, known))
    out.sort(key=lambda p: p[2])
    return out


def _levels(piv, known, i, a):
    """Merged levels at bar i: [(level, touches, newest_pivot_index)] for pivots known by i within LOOKBACK bars (>= 2 pivots)."""
    hi = int(np.searchsorted(known, i, side="right"))
    lo = int(np.searchsorted(known, i - LOOKBACK, side="left"))
    pts = sorted((p[0], p[1]) for p in piv[lo:hi] if p[1] > i - LOOKBACK)
    out, cur = [], []
    for p in pts:
        if cur and p[0] - cur[-1][0] > MERGE * a:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return [(float(np.mean([q[0] for q in g])), len(g), max(q[1] for q in g)) for g in out if len(g) >= 2]


def _round_dist(level, a, step):
    return abs(level - round(level / step) * step) / a if step else 0.0


def _chart(d: pd.DataFrame, mirror: bool):
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    return (-o, -l, -h, -c) if mirror else (o, h, l, c)


def instrument_events(sym: str, d: pd.DataFrame, mirror: bool = False, round_step: float | None = None) -> pd.DataFrame:
    """All touch events of one instrument (buy zones; mirror=True gives sell zones), oldest first, with outcomes and features."""
    o, h, l, c = _chart(d, mirror)
    n = len(c)
    a = _atr(h, l, c)
    sma = pd.Series(c).rolling(50, min_periods=50).mean().to_numpy()
    piv = _pivots(h, l)
    known = np.array([p[2] for p in piv]) if piv else np.array([])
    rows, last_ev, resolved = [], {}, []        # resolved: (resolve_idx, level, outcome 0/1)
    for i in range(60, n - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(sma[i]):
            continue
        for lv, touches, newest in _levels(piv, known, i, a[i]):
            if not (l[i] <= lv + 0.15 * a[i] and c[i] > lv - 0.1 * a[i] and c[i - 1] > lv and c[i] - lv <= 1.0 * a[i]):
                continue
            if any(abs(lv - k) < 0.3 * a[i] and i - t < 6 for k, t in last_ev.items()):
                continue                                                # one event per level per 6 bars
            last_ev[lv] = i
            tgt, brk = lv + UP * a[i], lv - DOWN * a[i]
            y, ridx, end = np.nan, i + HORIZON, min(n - 1, i + HORIZON)
            for j in range(i + 1, end + 1):
                hit_b, hit_t = l[j] <= brk, h[j] >= tgt
                if hit_b:
                    y, ridx = 0.0, j
                    break
                if hit_t:
                    y, ridx = 1.0, j
                    break
            t2, s2, y2 = c[i] + UP * a[i], c[i] - DOWN * a[i], np.nan                # the same bracket measured from the ENTRY close
            for j in range(i + 1, end + 1):
                if l[j] <= s2:
                    y2 = 0.0
                    break
                if h[j] >= t2:
                    y2 = 1.0
                    break
            r2 = (UP if y2 == 1.0 else -DOWN if y2 == 0.0 else (c[end] - c[i]) / a[i]) - COST_ATR
            risk = (c[i] - brk)
            if y == 1.0:
                r = (tgt - c[i]) / risk
            elif y == 0.0:
                r = -1.0
            else:
                r = (c[end] - c[i]) / risk
            r -= COST_ATR * a[i] / risk
            done = [(ri, lvl, yy) for ri, lvl, yy in resolved if ri <= i]
            near = [yy for ri, lvl, yy in done if abs(lvl - lv) <= 0.5 * a[i]]
            allr = [yy for ri, lvl, yy in done]
            rows.append({"sym": sym, "kind": "sell" if mirror else "buy", "i": i, "time": d.index[i], "level": (-lv if mirror else lv),
                         "touches": touches, "age": min(i - newest, LOOKBACK) / LOOKBACK,
                         "local_rate": (sum(near) + 1) / (len(near) + 2), "n_local": np.log1p(len(near)),
                         "instr_rate": (sum(allr) + 2) / (len(allr) + 4), "trend": (c[i] - sma[i]) / a[i],
                         "dist": (c[i] - lv) / a[i], "atr_pct": a[i] / abs(c[i]), "round_dist": _round_dist(lv, a[i], round_step),
                         "y": y, "R": r, "y2": y2, "R2": r2, "resolve": ridx, "rtime": d.index[end]})
            if np.isfinite(y):
                resolved.append((ridx, lv, y))
    return pd.DataFrame(rows)


def all_bars(sym: str, d: pd.DataFrame, mirror: bool = False) -> pd.DataFrame:
    """The same entry-relative bracket (+UP / -DOWN ATR from the close, HORIZON bars) for EVERY bar: what an arbitrary entry does."""
    o, h, l, c = _chart(d, mirror)
    n = len(c)
    a = _atr(h, l, c)
    rows = []
    for i in range(60, n - 1):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        end = min(n - 1, i + HORIZON)
        t2, s2, y2 = c[i] + UP * a[i], c[i] - DOWN * a[i], np.nan
        for j in range(i + 1, end + 1):
            if l[j] <= s2:
                y2 = 0.0
                break
            if h[j] >= t2:
                y2 = 1.0
                break
        r2 = (UP if y2 == 1.0 else -DOWN if y2 == 0.0 else (c[end] - c[i]) / a[i]) - COST_ATR
        rows.append((d.index[i], y2, r2, "sell" if mirror else "buy"))
    return pd.DataFrame(rows, columns=["time", "y2", "R2", "kind"])


def next_zones(sym: str, d: pd.DataFrame, model, scaler, mirror: bool, round_step, events: pd.DataFrame, top: int = 2) -> list[dict]:
    """The model's best zones for the NEXT touch, from the last bar: levels under (buy) / over (sell) the close within 4 ATR."""
    o, h, l, c = _chart(d, mirror)
    n = len(c)
    a = _atr(h, l, c)
    sma = pd.Series(c).rolling(50, min_periods=50).mean().to_numpy()
    i = n - 1
    if not np.isfinite(a[i]) or not np.isfinite(sma[i]) or events.empty:
        return []
    piv = _pivots(h, l)
    known = np.array([p[2] for p in piv]) if piv else np.array([])
    ev = events[events.resolve <= i]
    ya = ev["y"].dropna()
    instr = (ya.sum() + 2) / (len(ya) + 4) if len(ya) else 0.5
    out = []
    for lv, touches, newest in _levels(piv, known, i, a[i]):
        gap = (c[i] - lv) / a[i]
        if gap < 0.25 or gap > 4.0:
            continue
        near = ev[(ev["level"] * (-1 if mirror else 1) - lv).abs() <= 0.5 * a[i]]["y"].dropna()
        row = {"touches": touches, "age": min(i - newest, LOOKBACK) / LOOKBACK, "local_rate": (near.sum() + 1) / (len(near) + 2),
               "n_local": np.log1p(len(near)), "instr_rate": instr, "trend": (c[i] - sma[i]) / a[i], "dist": 0.3,
               "atr_pct": a[i] / abs(c[i]), "round_dist": _round_dist(lv, a[i], round_step)}
        p = float(model.predict_proba(scaler.transform(pd.DataFrame([row])[FEATURES]))[0, 1])
        out.append({"sym": sym, "kind": "sell" if mirror else "buy", "level": (-lv if mirror else lv), "atr_away": gap, "prob": p,
                    "touches": touches, "past_here": len(near)})
    return sorted(out, key=lambda x: -x["prob"])[:top]


def _pf(r):
    r = np.asarray(r, float)
    gw, gl = r[r > 0].sum(), -r[r <= 0].sum()
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def zone_report(data: dict, label: str, round_steps: dict | None = None, train_frac: float = 0.65) -> list[str]:
    """data = {symbol: OHLC frame (mid prices)}. Pooled model on the entry-relative outcome (a long / short entered at the touch close with
    target +UP ATR and stop -DOWN ATR), graded on later touches it never saw, against (a) every bar of the same instruments and (b) all touches."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler
    round_steps = round_steps or {}
    ev, base = {}, []
    for sym, d in data.items():
        for mirror in (False, True):
            e = instrument_events(sym, d, mirror, round_steps.get(sym))
            if len(e):
                ev[(sym, mirror)] = e
            base.append(all_bars(sym, d, mirror).assign(sym=sym))
    if not ev:
        return [f"### {label}: no events"]
    allev = pd.concat(ev.values(), ignore_index=True)
    res = allev[np.isfinite(allev["y2"])].copy()
    cut = res["time"].sort_values().iloc[int(len(res) * train_frac)]
    tr, te = res[res["rtime"] < cut], res[res["time"] >= cut].copy()
    sc = StandardScaler().fit(tr[FEATURES])
    lr = LogisticRegression(max_iter=500, C=1.0).fit(sc.transform(tr[FEATURES]), tr["y2"])
    te["p"] = lr.predict_proba(sc.transform(te[FEATURES]))[:, 1]
    auc = roc_auc_score(te["y2"], te["p"]) if te["y2"].nunique() > 1 else float("nan")
    b = pd.concat(base, ignore_index=True)
    b = b[(b["time"] >= cut) & np.isfinite(b["y2"])]
    out = [f"### {label}: {len(allev)} zone touches over {len(data)} instruments; model trained on touches before {str(cut)[:10]} ({len(tr)}), "
           f"graded on {len(te)} later touches it never saw. A trade = enter at the close, target +{UP} ATR, stop -{DOWN} ATR, {HORIZON} bars max, costs in.",
           f"  ANY bar, same bracket (the baseline): {len(b)} entries, hit target first {b['y2'].mean():.1%}, avg {b['R2'].mean():+.3f}R, PF {_pf(b['R2']):.2f}",
           f"  EVERY zone touch (buy zones long, sell zones short): {len(te)} touches, hit target first {te['y2'].mean():.1%}, avg {te['R2'].mean():+.3f}R, PF {_pf(te['R2']):.2f}",
           f"  model AUC on those touches {auc:.3f} (0.5 = no skill); weights: " +
           ", ".join(f"{f} {w:+.2f}" for f, w in sorted(zip(FEATURES, lr.coef_[0]), key=lambda x: -abs(x[1]))[:6]),
           "  by the model's predicted probability (test touches):"]
    te["q"] = pd.qcut(te["p"], 4, labels=["lowest 25%", "25-50%", "50-75%", "top 25%"], duplicates="drop")
    for q, g in te.groupby("q", observed=True):
        out.append(f"    {q}: {len(g)} touches, predicted {g['p'].mean():.0%}, hit {g['y2'].mean():.0%}, avg {g['R2'].mean():+.3f}R, PF {_pf(g['R2']):.2f}")
    for kind in ("buy", "sell"):
        g, bk = te[te["kind"] == kind], b[b["kind"] == kind]
        top = g[g["p"] >= g["p"].quantile(0.75)]
        out.append(f"  {kind} zones: any-bar baseline {bk['R2'].mean():+.3f}R (PF {_pf(bk['R2']):.2f}); all touches {g['R2'].mean():+.3f}R (PF {_pf(g['R2']):.2f}); "
                   f"model top 25% {len(top)} touches {top['R2'].mean():+.3f}R (PF {_pf(top['R2']):.2f})")
    ps = []
    for sym, g in te.groupby("sym"):
        t = g[g["p"] >= g["p"].quantile(0.75)]
        ps.append((sym, len(g), g["R2"].mean(), len(t), t["R2"].mean(), _pf(t["R2"])))
    pos = sum(1 for x in ps if x[4] > 0)
    out.append(f"  per instrument: the model's top-quartile picks beat zero R on {pos} of {len(ps)} instruments (if it had no skill, about half would by chance): " +
               "; ".join(f"{x[0]} {x[3]} picks {x[4]:+.2f}R" for x in sorted(ps, key=lambda x: -x[4])[:6]) + " ... worst: " +
               "; ".join(f"{x[0]} {x[4]:+.2f}R" for x in sorted(ps, key=lambda x: x[4])[:3]))
    out.append("  NEXT ZONES (last bar; probability that an entry at the zone hits +target before -stop; ATR = distance from the close):")
    full_sc = StandardScaler().fit(res[FEATURES])
    full = LogisticRegression(max_iter=500, C=1.0).fit(full_sc.transform(res[FEATURES]), res["y2"])
    for sym, d in data.items():
        for mirror in (False, True):
            e = ev.get((sym, mirror))
            if e is None:
                continue
            for z in next_zones(sym, d, full, full_sc, mirror, round_steps.get(sym), e):
                out.append(f"    {sym} {z['kind'].upper()} zone {z['level']:.5g}: {z['atr_away']:.1f} ATR away, hit-target probability {z['prob']:.0%} "
                           f"({z['touches']} pivots, {z['past_here']} past touches here)")
    return out
