"""
Application Streamlit — Prédiction LoL en temps réel.
Lance une partie de LoL, puis : streamlit run app/main.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.counters.lookup import compute_pregame_matchup
from src.data.live_player_stats import fetch_all_player_stats, summarize_mastery
from src.live.live_client import (
    detect_active_team,
    extract_features,
    fetch_live_state,
    flip_features_perspective,
)
from src.models.predict import get_advice, predict_win_probability

# Poids initial du prior pré-game dans la proba finale (0 = pas de blend).
# Décroît linéairement et tombe à 0 à PRIOR_DECAY_MINUTES — passé ce temps les
# events live dominent largement.
PRIOR_WEIGHT_START = 0.15
PRIOR_DECAY_MINUTES = 15.0


def compute_prior_offset(matchup: dict | None, mastery: dict | None) -> tuple[float, dict]:
    """Combine counter + mastery signals → offset par rapport à 0.5 (= neutre).

    Retour : (offset_clampé_à_[-0.5,+0.5], contribs détaillées).
    """
    parts: dict[str, float] = {}

    if matchup and matchup.get("avg_wr_blue") is not None:
        # counter signal : (avg_wr-50)/100 typiquement dans [-0.1,+0.1], on amplifie ×2.5
        parts["counter"] = ((matchup["avg_wr_blue"] - 50.0) / 100.0) * 2.5

    if mastery and mastery.get("n_resolved", 0) >= 5:
        # mastery_offset déjà pré-scalé dans [-0.25, +0.25] par summarize_mastery
        parts["mastery"] = mastery["mastery_offset"]

    if not parts:
        return 0.0, {}

    raw = sum(parts.values())
    return max(-0.5, min(0.5, raw)), parts


def blend_with_pregame(
    p_live: float,
    matchup: dict | None,
    mastery: dict | None,
    game_time_min: float,
) -> tuple[float, float, dict]:
    """Blend live + prior pré-game (counter + mastery).

    Retour : (p_final, weight_utilisé, contribs).
    """
    offset, contribs = compute_prior_offset(matchup, mastery)
    weight = max(0.0, PRIOR_WEIGHT_START * (1.0 - game_time_min / PRIOR_DECAY_MINUTES))
    if not contribs or weight <= 0:
        return p_live, 0.0, contribs
    prior = max(0.0, min(1.0, 0.5 + offset))
    return (1.0 - weight) * p_live + weight * prior, weight, contribs

POLL_INTERVAL = 20  # secondes entre chaque update

# Dossier où sont loggées les parties (1 fichier JSONL par partie)
LIVE_LOG_DIR = Path(__file__).parent.parent / "data" / "live_games"
LIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="LoL Win Predictor",
    page_icon="⚔️",
    layout="wide",
)

st.title("⚔️ LoL Win Predictor")
st.caption("Prédiction en temps réel de la victoire — équipe bleue (ORDER)")

# Historique des prédictions pour le graphe de tendance
if "history" not in st.session_state:
    st.session_state.history = []
if "last_features" not in st.session_state:
    st.session_state.last_features = None
if "log_file" not in st.session_state:
    st.session_state.log_file = None
if "last_game_time" not in st.session_state:
    st.session_state.last_game_time = -1.0
if "pregame_matchup" not in st.session_state:
    st.session_state.pregame_matchup = None
if "mastery_summary" not in st.session_state:
    st.session_state.mastery_summary = None
if "my_team" not in st.session_state:
    st.session_state.my_team = None  # "ORDER" (BLUE) ou "CHAOS" (RED)


def _new_log_file() -> Path:
    name = datetime.now().strftime("game_%Y-%m-%dT%H-%M-%S.jsonl")
    return LIVE_LOG_DIR / name


def append_snapshot(
    features: dict,
    proba: float,
    advice: list[str],
    raw_events: list[dict] | None = None,
    active_team: str | None = None,
) -> None:
    """Logue un snapshot de la partie courante dans le fichier JSONL.
    Rollover vers un nouveau fichier si game_time a chuté (nouvelle partie).
    raw_events = liste brute des events de la Live API (debug parsing).
    active_team = équipe du joueur local ('ORDER' ou 'CHAOS') pour reconstruire
    a posteriori la vue 'mon équipe vs adversaires' à partir des logs BLUE-RED."""
    game_time = features.get("game_time_minutes", 0.0)
    # Détection de nouvelle partie : game_time recule de plus d'1 min
    if (
        st.session_state.log_file is None
        or game_time + 1.0 < st.session_state.last_game_time
    ):
        st.session_state.log_file = _new_log_file()
        st.session_state.history = []  # reset courbe pour la nouvelle partie

    my_proba = (1.0 - proba) if active_team == "CHAOS" else proba
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "game_time_minutes": round(game_time, 3),
        "blue_win_probability": round(proba, 4),
        "active_team": active_team,
        "my_win_probability": round(my_proba, 4),
        "features": features,
        "advice": advice,
        "_raw_events": raw_events or [],
    }
    try:
        with open(st.session_state.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except OSError as e:
        st.warning(f"⚠️ Impossible d'écrire le log : {e}")

    st.session_state.last_game_time = game_time


def make_gauge(proba: float) -> go.Figure:
    """Gauge sans titre interne — le titre est rendu par Streamlit au-dessus
    pour éviter d'écraser le grand chiffre central quand le label est long."""
    color = "green" if proba > 0.6 else ("red" if proba < 0.4 else "orange")
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(proba * 100, 1),
        number={"suffix": "%", "font": {"size": 56}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 40], "color": "#ffcccc"},
                {"range": [40, 60], "color": "#fff3cc"},
                {"range": [60, 100], "color": "#ccffcc"},
            ],
            "threshold": {
                "line": {"color": "black", "width": 3},
                "thickness": 0.75,
                "value": 50,
            },
        },
    ))
    fig.update_layout(height=280, margin=dict(t=10, b=0, l=20, r=20))
    return fig


def make_trend(history: list[tuple], y_label: str = "Ta probabilité de victoire (%)") -> go.Figure:
    if len(history) < 2:
        return None
    times, probas = zip(*history)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(times), y=[p * 100 for p in probas],
        mode="lines+markers", line=dict(color="steelblue", width=2),
        name="Win %",
    ))
    fig.add_hline(y=50, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="Évolution de la probabilité",
        xaxis_title="Temps de jeu (min)",
        yaxis_title=y_label,
        yaxis=dict(range=[0, 100]),
        height=250,
        margin=dict(t=40, b=40, l=40, r=20),
    )
    return fig


# Layout principal
col_gauge, col_stats = st.columns([1, 1])
gauge_placeholder = col_gauge.empty()
stats_placeholder = col_stats.empty()
trend_placeholder = st.empty()
advice_placeholder = st.empty()
status_placeholder = st.empty()

while True:
    state = fetch_live_state()
    features = extract_features(state) if state else None

    if features is None:
        status_placeholder.warning(
            "🔌 Aucune partie détectée. Lance League of Legends et rejoins une partie."
        )
        gauge_placeholder.plotly_chart(make_gauge(0.5), use_container_width=True)
        # Reset des données pré-game (au cas où on reprend une autre game)
        st.session_state.pregame_matchup = None
        st.session_state.mastery_summary = None
        st.session_state.my_team = None
        time.sleep(POLL_INTERVAL)
        st.rerun()

    status_placeholder.success(f"🎮 Partie en cours — {features['game_time_minutes']:.1f} min")

    # Compute les données pré-game UNE FOIS au début de la partie (figées ensuite)
    game_time = features["game_time_minutes"]
    is_new_game = (
        st.session_state.pregame_matchup is None
        or game_time + 1.0 < st.session_state.last_game_time
    )
    if is_new_game:
        st.session_state.my_team = detect_active_team(state)
        st.session_state.pregame_matchup = compute_pregame_matchup(state)
        with st.spinner("🔄 Fetch des stats joueurs (Riot API)…"):
            raw_stats = fetch_all_player_stats(state)
            st.session_state.mastery_summary = summarize_mastery(raw_stats)

    my_team = st.session_state.my_team
    flip = (my_team == "CHAOS")

    proba_live = predict_win_probability(features)
    proba, prior_weight, prior_contribs = blend_with_pregame(
        proba_live,
        st.session_state.pregame_matchup,
        st.session_state.mastery_summary,
        game_time,
    )

    # Perspective joueur : flip si CHAOS, sinon identité
    proba_me = (1.0 - proba) if flip else proba
    proba_live_me = (1.0 - proba_live) if flip else proba_live
    features_me = flip_features_perspective(features) if flip else features
    prior_contribs_me = {k: (-v if flip else v) for k, v in prior_contribs.items()}

    advice = get_advice(features_me, proba_me)
    raw_events = (state.get("events") or {}).get("Events", []) if state else []
    append_snapshot(features, proba, advice, raw_events=raw_events, active_team=my_team)
    st.session_state.history.append((features["game_time_minutes"], proba_me))
    st.session_state.last_features = features

    # Labels textuels pour l'affichage selon la team du joueur
    if my_team == "ORDER":
        my_label, foe_label = "TON équipe (BLUE)", "Adversaires (RED)"
    elif my_team == "CHAOS":
        my_label, foe_label = "TON équipe (RED)", "Adversaires (BLUE)"
    else:
        my_label, foe_label = "Équipe BLUE", "Équipe RED"

    def _side_badge(team_in_data: str) -> str:
        """Renvoie un badge 'TON ÉQUIPE' ou 'ADVERSAIRES' selon ton my_team."""
        if my_team is None:
            return "🔵 BLUE" if team_in_data == "ORDER" else "🔴 RED"
        return "🟢 TON ÉQUIPE" if team_in_data == my_team else "🔴 ADVERSAIRES"

    # ── Sidebar : matchup + mastery pré-game (figé toute la partie) ────────
    with st.sidebar:
        if my_team:
            st.success(f"🎮 Tu joues en **{my_team}** ({'BLUE' if my_team == 'ORDER' else 'RED'} side)")

        # ── Section 1 : Matchups counters ──────────────────────────────────
        st.header("🎯 Matchup pré-game")
        m = st.session_state.pregame_matchup
        if m is None:
            st.info("En attente du début de partie…")
        else:
            avg = m.get("avg_wr_blue")
            if avg is not None:
                # WR de TON équipe : si CHAOS, c'est 100 - avg_wr_blue
                wr_me = (100.0 - avg) if flip else avg
                st.metric(
                    "WR moyen TON équipe (counter)",
                    f"{wr_me:.1f}%",
                    delta=f"{wr_me - 50.0:+.1f} pts",
                )
            st.caption(
                f"{m['n_resolved']}/{m['n_lanes']} matchups documentés "
                f"(top 5 hard counters par champ, OP.GG)"
            )

            for lane in m["lanes"]:
                wr_b = lane["wr_blue"]
                # wr du point de vue joueur
                wr_view = (100.0 - wr_b) if (wr_b is not None and flip) else wr_b
                me_champ = lane["red"] if flip else lane["blue"]
                foe_champ = lane["blue"] if flip else lane["red"]
                if wr_view is None:
                    badge, detail = "⚪", "neutre / pas dans top 5"
                elif wr_view >= 53:
                    badge, detail = "🟢", f"{wr_view:.1f}% tu es favori"
                elif wr_view <= 47:
                    badge, detail = "🔴", f"{wr_view:.1f}% tu es défavorisé"
                else:
                    badge, detail = "🟡", f"{wr_view:.1f}% équilibré"
                st.markdown(
                    f"{badge} **{lane['role'].upper()}** — "
                    f"{me_champ} vs {foe_champ}  \n"
                    f"<span style='color:#888;font-size:0.85em;'>{detail}</span>",
                    unsafe_allow_html=True,
                )

        # ── Section 2 : Mastery joueurs ───────────────────────────────────
        st.divider()
        st.header("⭐ Mastery joueurs")
        ms = st.session_state.mastery_summary
        if ms is None:
            st.info("Fetch Riot en attente du début de partie…")
        elif ms["n_resolved"] == 0:
            st.warning("Aucune donnée Riot récupérée (clé invalide ou comptes introuvables).")
        else:
            # On bascule blue/red en me/foe selon my_team
            blue_k = ms["blue_total"] / 1000
            red_k = ms["red_total"] / 1000
            me_total = ms["red_total"] if flip else ms["blue_total"]
            foe_total = ms["blue_total"] if flip else ms["red_total"]
            me_k = me_total / 1000
            foe_k = foe_total / 1000
            delta_me = (me_total - foe_total) / 1000
            ratio = me_total / max(foe_total, 1)
            st.metric(
                "Mastery totale TON équipe",
                f"{me_k:.0f}k pts",
                delta=f"{delta_me:+.0f}k vs adversaires",
            )
            st.caption(f"Adversaires: {foe_k:.0f}k pts  •  Ratio toi/eux = {ratio:.2f}×")
            st.caption(f"{ms['n_resolved']}/{ms['n_total']} joueurs résolus via Riot API")

            # Per-player breakdown — TON équipe d'abord
            order_players = [p for p in ms["per_player"] if p.get("team") == "ORDER"]
            chaos_players = [p for p in ms["per_player"] if p.get("team") == "CHAOS"]
            me_players, foe_players = (chaos_players, order_players) if flip else (order_players, chaos_players)

            st.markdown(f"**{_side_badge(my_team or 'ORDER')}**")
            for p in me_players:
                pts_k = p.get("mastery_points", 0) / 1000
                lvl = p.get("mastery_level", 0)
                err = p.get("error")
                line = f"  {p['champion']} — **{pts_k:.0f}k** (M{lvl})"
                if err:
                    line += f" <span style='color:#c00;font-size:0.8em;'>[{err}]</span>"
                st.markdown(line, unsafe_allow_html=True)

            st.markdown(f"**{_side_badge('CHAOS' if my_team == 'ORDER' else 'ORDER')}**")
            for p in foe_players:
                pts_k = p.get("mastery_points", 0) / 1000
                lvl = p.get("mastery_level", 0)
                err = p.get("error")
                line = f"  {p['champion']} — **{pts_k:.0f}k** (M{lvl})"
                if err:
                    line += f" <span style='color:#c00;font-size:0.8em;'>[{err}]</span>"
                st.markdown(line, unsafe_allow_html=True)

        # ── Section 3 : État du prior ──────────────────────────────────────
        st.divider()
        st.header("📐 Prior pré-game")
        if not prior_contribs_me:
            st.caption("Aucun signal pré-game activable → proba live non modifiée.")
        elif prior_weight <= 0:
            st.caption(f"Prior inactif (poids = 0 après {PRIOR_DECAY_MINUTES:.0f}min)")
        else:
            st.caption(f"Poids actuel : **{prior_weight:.3f}** "
                       f"(décroît linéairement jusqu'à 0 à {PRIOR_DECAY_MINUTES:.0f}min)")
            for name, val in prior_contribs_me.items():
                arrow = "↑ toi" if val > 0 else ("↓ toi" if val < 0 else "neutre")
                st.markdown(f"- **{name}** : {val:+.3f} → {arrow}")

    # Gauge
    with gauge_placeholder.container():
        st.subheader(f"Probabilité de victoire — {my_label}")
        st.plotly_chart(make_gauge(proba_me), use_container_width=True)
        if prior_weight > 0 and prior_contribs_me:
            details = "  +  ".join(f"{n}: {v:+.3f}" for n, v in prior_contribs_me.items())
            st.caption(
                f"📐 Live {proba_live_me*100:.1f}%  ×  {1-prior_weight:.2f}  "
                f"+  Prior ({details})  ×  {prior_weight:.2f}  =  **{proba_me*100:.1f}%**"
            )

    # Stats tableau
    with stats_placeholder:
        st.subheader("État actuel")
        cols = st.columns(2)
        metrics = [
            ("Kills", features_me["kills_diff"]),
            ("Deaths", features_me["deaths_diff"]),
            ("CS", features_me["cs_diff"]),
            ("Gold", features_me["gold_diff"]),
            ("Tours", features_me["towers_diff"]),
            ("Dragons", features_me["dragons_diff"]),
            ("Barons", features_me["barons_diff"]),
            ("Hérauts", features_me["heralds_diff"]),
        ]
        for i, (label, val) in enumerate(metrics):
            delta_color = "normal" if val != 0 else "off"
            cols[i % 2].metric(
                label=f"{label} (toi - eux)",
                value=f"{val:+d}" if isinstance(val, int) else f"{val:+.0f}",
                delta_color=delta_color,
            )

    # Tendance
    trend_fig = make_trend(st.session_state.history)
    if trend_fig:
        with trend_placeholder:
            st.plotly_chart(trend_fig, use_container_width=True)

    # Conseils (déjà calculés plus haut pour le logging)
    with advice_placeholder:
        st.subheader("Conseils stratégiques")
        for tip in advice:
            st.info(tip)

    time.sleep(POLL_INTERVAL)
    st.rerun()
