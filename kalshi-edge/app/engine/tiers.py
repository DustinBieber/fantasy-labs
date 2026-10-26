"""Two-gate tier rule.

S    raw edge > s_min_edge_pts AND net EV > s_min_net_ev_pct
G    raw edge >= g_min_edge_pts AND net EV >= g_min_net_ev_pct AND a proven expert agrees (half size)
W    raw edge >= w_min_edge_pts, or a proven expert agrees at a fair price (edge >= 0). No trade by default.
Pass below w_min_edge_pts, or any proven expert disagrees.

Edges above the S edge gate that miss the S EV gate fall through to the G check.
G-level numbers without proven expert agreement are shown as W.
"""
from __future__ import annotations

from typing import Dict, Tuple

AGREE, DISAGREE, NONE = "agree", "disagree", "none"


def assign_tier(edge_pts: float, net_ev_pct: float, expert: str, t: Dict) -> Tuple[str, str]:
    if expert == DISAGREE:
        return "P", "Pass: a proven expert in this sport is on the other side."
    s_edge, s_ev = t["s_min_edge_pts"], t["s_min_net_ev_pct"]
    g_edge, g_ev = t["g_min_edge_pts"], t["g_min_net_ev_pct"]
    w_edge = t["w_min_edge_pts"]

    if edge_pts > s_edge and net_ev_pct > s_ev:
        return "S", f"Tier S: edge {edge_pts:.1f} pts > {s_edge:g} and net EV {net_ev_pct:.1f}% > {s_ev:g}%."
    if edge_pts >= g_edge and net_ev_pct >= g_ev:
        if expert == AGREE:
            return "G", (f"Tier G: edge {edge_pts:.1f} pts and net EV {net_ev_pct:.1f}% clear the G gates "
                         f"and a proven expert agrees. Half size.")
        return "W", (f"Watch: G-level numbers (edge {edge_pts:.1f} pts, net EV {net_ev_pct:.1f}%) "
                     f"but no proven expert agreement, so no trade.")
    if edge_pts >= w_edge:
        why = "net EV below the G gate" if edge_pts >= g_edge else f"edge below {g_edge:g} pts"
        return "W", f"Watch: edge {edge_pts:.1f} pts, net EV {net_ev_pct:.1f}% ({why}). No trade by default."
    if expert == AGREE and edge_pts >= 0:
        return "W", f"Watch: a proven expert agrees and the price is fair (edge {edge_pts:.1f} pts). No trade by default."
    return "P", f"Pass: edge {edge_pts:.1f} pts is below {w_edge:g}."


def size_multiplier(tier: str, t: Dict) -> float:
    if tier == "S":
        return 1.0
    if tier == "G":
        return float(t["g_size_multiplier"])
    return 0.0
