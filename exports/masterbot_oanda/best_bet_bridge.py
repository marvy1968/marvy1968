from __future__ import annotations

from typing import Any

from market_consensus import scan_books_for_best_edge


def build_model_probs_from_eval(eval_result: dict[str, Any]) -> dict[str, float]:
    """
    Map MarchEdge eval output into the keys expected by scan_books_for_best_edge().
    """
    return {
        "over": float(eval_result.get("over_prob", 0.0)),
        "under": float(eval_result.get("under_prob", 0.0)),
        "home_ml": float(eval_result.get("home_win_prob", 0.0)),
        "away_ml": float(eval_result.get("away_win_prob", 0.0)),
        "home_spread": float(eval_result.get("home_cover_prob", 0.0)),
        "away_spread": float(eval_result.get("away_cover_prob", 0.0)),
    }


def format_best_candidate(best_candidate: dict[str, Any] | None) -> str:
    if not best_candidate:
        return "⏸️ No bet clears the minimum edge threshold."

    lines: list[str] = []
    lines.append("🏀 *Best Market Edge*")
    lines.append(f"Book: *{best_candidate.get('book', 'unknown')}*")
    lines.append(f"Market: *{best_candidate.get('market_type', 'unknown')}*")

    if "team" in best_candidate:
        lines.append(f"Team: *{best_candidate['team']}*")

    side = str(best_candidate.get("side", "")).upper()
    if side:
        lines.append(f"Side: *{side}*")

    if "line" in best_candidate and best_candidate["line"] is not None:
        lines.append(f"Line: *{best_candidate['line']}*")

    if "odds" in best_candidate and best_candidate["odds"] is not None:
        lines.append(f"Odds: *{best_candidate['odds']}*")

    if "ev" in best_candidate:
        lines.append(f"EV: *{float(best_candidate['ev']):+.2%}*")

    if "model_prob" in best_candidate and best_candidate["model_prob"] is not None:
        lines.append(f"Model Prob: *{float(best_candidate['model_prob']):.1%}*")

    if "consensus_prob" in best_candidate and best_candidate["consensus_prob"] is not None:
        lines.append(f"Consensus Prob: *{float(best_candidate['consensus_prob']):.1%}*")

    if "edge_vs_consensus" in best_candidate and best_candidate["edge_vs_consensus"] is not None:
        lines.append(f"Edge vs Consensus: *{float(best_candidate['edge_vs_consensus']):+.2%}*")

    if best_candidate.get("is_outlier"):
        lines.append("⚠️ Line appears to be an outlier.")

    if best_candidate.get("outlier_reason"):
        lines.append(f"_{best_candidate['outlier_reason']}_")

    return "\n".join(lines)


def scan_and_format_best_bet(
    matched_event: dict[str, Any],
    eval_result: dict[str, Any],
    min_edge: float = 0.03,
) -> tuple[dict[str, Any] | None, str]:
    model_probs = build_model_probs_from_eval(eval_result)
    best_candidate = scan_books_for_best_edge(
        matched_event=matched_event,
        model_probs=model_probs,
        min_edge=min_edge,
    )
    return best_candidate, format_best_candidate(best_candidate)
