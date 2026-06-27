"""
App post-game — Analyse d'une partie passée par pseudo Riot ID.

Usage : streamlit run app/postgame.py
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.fetch_player import (
    build_game_summary,
    extract_full_timeline,
    get_match_info,
    get_match_timeline,
    get_puuid,
    get_recent_matches,
)
from src.models.predict import predict_win_probability

st.set_page_config(
    page_title="LoL Post-Game Analyzer",
    page_icon="📊",
    layout="wide",
)

# ── CSS minimal ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.game-card { padding: 10px 16px; border-radius: 8px; margin: 4px 0;
             cursor: pointer; border-left: 4px solid; }
.win  { border-color: #4ade80; background: #f0fdf4; }
.loss { border-color: #f87171; background: #fef2f2; }
.kda  { font-size: 18px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.title("📊 LoL Post-Game Analyzer")
st.caption("Entrez votre Riot ID pour analyser vos parties récentes")

# ── Recherche joueur ────────────────────────────────────────────────────────────
col_input, col_btn = st.columns([3, 1])
riot_id = col_input.text_input("Riot ID", placeholder="GameName#TAG  (ex: Caps#EUW, Faker#T1)")
search  = col_btn.button("🔍 Analyser", use_container_width=True)

if not riot_id and not st.session_state.get("puuid"):
    st.info("Entrez votre Riot ID au format **GameName#TAG** et appuyez sur Analyser.")
    st.stop()

# ── Résolution Riot ID → PUUID ──────────────────────────────────────────────────
if search or (riot_id and not st.session_state.get("current_riot_id") == riot_id):
    if "#" not in riot_id:
        st.error("Format invalide — utilisez GameName#TAG (ex: Caps#EUW)")
        st.stop()

    game_name, tag = riot_id.split("#", 1)
    with st.spinner(f"Recherche de {riot_id}..."):
        puuid = get_puuid(game_name.strip(), tag.strip())

    if not puuid:
        st.error(f"Joueur **{riot_id}** introuvable. Vérifiez le nom et le tag.")
        st.stop()

    st.session_state.puuid = puuid
    st.session_state.current_riot_id = riot_id
    st.session_state.selected_match = None

puuid = st.session_state.get("puuid")
if not puuid:
    st.stop()

# ── Chargement des parties récentes ────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_recent_games(puuid: str):
    match_ids = get_recent_matches(puuid, count=10)
    games = []
    for mid in match_ids:
        info = get_match_info(mid)
        if not info:
            continue
        duration = info["info"].get("gameDuration", 0)
        if duration < 15 * 60:
            continue
        summary = build_game_summary(info, puuid)
        if summary:
            games.append(summary)
    return games

with st.spinner("Chargement des parties..."):
    games = load_recent_games(puuid)

if not games:
    st.warning("Aucune partie ranked solo récente trouvée.")
    st.stop()

# ── Layout 2 colonnes : liste des games | détail ────────────────────────────────
col_list, col_detail = st.columns([1, 2])

with col_list:
    st.subheader(f"🎮 {st.session_state.current_riot_id}")
    st.caption(f"{len(games)} parties récentes")

    for i, g in enumerate(games):
        result_class = "win" if g["won"] else "loss"
        result_emoji = "✅" if g["won"] else "❌"
        kda = f"{g['kills']}/{g['deaths']}/{g['assists']}"
        team_label = "Bleue" if g["player_team"] == "blue" else "Rouge"

        if st.button(
            f"{result_emoji} **{g['champion']}** — {kda}  |  {g['duration_min']} min  |  Éq. {team_label}",
            key=f"game_{i}",
            use_container_width=True,
        ):
            st.session_state.selected_match = g["match_id"]
            st.session_state.selected_game  = g

# ── Détail d'une partie ─────────────────────────────────────────────────────────
with col_detail:
    selected_id = st.session_state.get("selected_match")
    selected_g  = st.session_state.get("selected_game", {})

    if not selected_id:
        st.info("👈 Sélectionnez une partie pour l'analyser")
        st.stop()

    with st.spinner("Analyse de la partie..."):
        info     = get_match_info(selected_id)
        timeline = get_match_timeline(selected_id)

    if not timeline or not info:
        st.error("Impossible de charger cette partie.")
        st.stop()

    game_data  = extract_full_timeline(timeline, info)
    features   = game_data["features_by_min"]
    key_events = game_data["key_events"]
    blue_won   = game_data["blue_won"]
    player_team = selected_g.get("player_team", "blue")

    # Calcul de la courbe de win probability minute par minute
    probas_blue = []
    minutes     = []
    for snap in features:
        if snap["minute"] < 3:
            continue
        p = predict_win_probability(snap)
        probas_blue.append(p)
        minutes.append(snap["minute"])

    # Convertir en win proba du joueur
    if player_team == "blue":
        probas_player = probas_blue
    else:
        probas_player = [1 - p for p in probas_blue]

    # ── Titre + résultat ────────────────────────────────────────────────────────
    result_text = "✅ Victoire" if selected_g.get("won") else "❌ Défaite"
    st.subheader(f"{result_text} — {selected_g.get('champion')} ({selected_g.get('duration_min')} min)")
    kda = f"{selected_g['kills']}/{selected_g['deaths']}/{selected_g['assists']}"
    st.caption(f"KDA : {kda}  ·  Équipe {player_team.capitalize()}")

    # ── Courbe principale ───────────────────────────────────────────────────────
    final_proba = probas_player[-1] if probas_player else 0.5
    color_line  = "#4ade80" if selected_g.get("won") else "#f87171"

    fig = go.Figure()

    # Zone colorée sous la courbe
    fig.add_trace(go.Scatter(
        x=minutes, y=[p * 100 for p in probas_player],
        fill='tozeroy', fillcolor='rgba(74,222,128,0.08)' if selected_g.get("won") else 'rgba(248,113,113,0.08)',
        line=dict(color=color_line, width=2.5),
        name="Win probability",
    ))

    # Ligne 50%
    fig.add_hline(y=50, line_dash="dash", line_color="gray",
                  annotation_text="50/50", annotation_position="right")

    # Annotations events clés (baron, dragon soul...)
    notable = [e for e in key_events if "Soul" in e["label"] or "Baron" in e["label"]]
    for ev in notable:
        ev_min = int(ev["min"])
        if ev_min in minutes:
            idx = minutes.index(ev_min)
            y_val = probas_player[idx] * 100
            fig.add_annotation(
                x=ev_min, y=y_val,
                text=ev["label"],
                showarrow=True, arrowhead=2,
                bgcolor="white", bordercolor="gray",
                font=dict(size=11),
                yshift=20,
            )

    # ── Zones décisives avec hysteresis ─────────────────────────────────────────
    def find_decisive_zones(mins, probs, thr_hi=0.75, thr_lo=0.25,
                            hysteresis=0.10, min_dur=4):
        """
        Détecte les fenêtres où la proba reste décisivement haute/basse.
        hysteresis : il faut tomber sous (thr_hi - hysteresis) pour quitter
                     une zone haute — absorbe les mini-dips sans couper la zone.
        min_dur    : durée minimum en minutes pour qu'une zone soit retenue.
        """
        zones, in_zone, z0, zd = [], False, None, None
        ex_hi, ex_lo = thr_hi - hysteresis, thr_lo + hysteresis
        for i, (m, p) in enumerate(zip(mins, probs)):
            if not in_zone:
                if   p > thr_hi: in_zone, z0, zd = True, i, "high"
                elif p < thr_lo: in_zone, z0, zd = True, i, "low"
            else:
                exiting = (zd == "high" and p < ex_hi) or (zd == "low" and p > ex_lo)
                if exiting:
                    dur = mins[i - 1] - mins[z0]
                    zp  = probs[z0:i]
                    if dur >= min_dur:
                        zones.append({
                            "start": mins[z0], "end": mins[i - 1],
                            "peak": max(zp) if zd == "high" else min(zp),
                            "duration": dur, "dir": zd,
                        })
                    in_zone = False
                    if   p > thr_hi: in_zone, z0, zd = True, i, "high"
                    elif p < thr_lo: in_zone, z0, zd = True, i, "low"
        if in_zone and z0 is not None:
            dur, zp = mins[-1] - mins[z0], probs[z0:]
            if dur >= min_dur:
                zones.append({
                    "start": mins[z0], "end": mins[-1],
                    "peak": max(zp) if zd == "high" else min(zp),
                    "duration": dur, "dir": zd,
                })
        return zones

    decisive_zones = find_decisive_zones(minutes, probas_player)

    for z in decisive_zones:
        label   = "Avantage décisif" if z["dir"] == "high" else "Situation critique"
        opacity = min(0.05 + z["duration"] * 0.004, 0.18)
        fig.add_vrect(
            x0=z["start"], x1=z["end"],
            fillcolor=color_line, opacity=opacity,
            layer="below",
            annotation_text=f"{label} ({z['duration']} min)",
            annotation_position="top left",
        )

    fig.update_layout(
        title=f"Probabilité de victoire — minute par minute",
        xaxis_title="Minute de jeu",
        yaxis_title="Win probability (%)",
        yaxis=dict(range=[0, 100]),
        height=380,
        margin=dict(t=50, b=40, l=50, r=20),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Résumé zones décisives ──────────────────────────────────────────────────
    st.subheader("🔍 Quand la partie a-t-elle été jouée ?")
    if decisive_zones:
        for z in decisive_zones:
            emoji    = "🔵" if z["dir"] == "high" else "🔴"
            team_lbl = "ton équipe dominait" if z["dir"] == "high" else "l'adversaire dominait"
            peak_pct = int(z["peak"] * 100)
            st.markdown(
                f"{emoji} **{z['start']} → {z['end']} min** — "
                f"{z['duration']} min de contrôle · pic à **{peak_pct}%** · {team_lbl}"
            )
    else:
        st.caption("Aucune fenêtre décisive prolongée — partie équilibrée jusqu'au bout.")

    # ── Moments clés texte ──────────────────────────────────────────────────────
    st.subheader("Moments clés")
    notable_all = [e for e in key_events if any(k in e["label"] for k in ["Baron", "Soul", "Blood"])]
    if notable_all:
        for ev in notable_all[:6]:
            team_emoji = "🔵" if ev["team"] == "blue" else "🔴"
            st.write(f"{team_emoji} **{ev['min']} min** — {ev['label']}")
    else:
        st.caption("Aucun événement majeur détecté.")

    # ── Stats finales ───────────────────────────────────────────────────────────
    st.subheader("Évolution des écarts (bleu - rouge)")
    if features:
        last = features[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Gold final", f"{last['gold_diff']:+,}")
        c2.metric("Kills", f"{last['kills_diff']:+}")
        c3.metric("Tours", f"{last['towers_diff']:+}")
        c4.metric("Dragons", f"{last['dragons_diff']:+}")
