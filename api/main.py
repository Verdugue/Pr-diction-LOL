"""FastAPI backend — sert les prédictions et les analyses post-game."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.data.fetch_player import (
    build_game_summary,
    extract_full_timeline,
    get_match_info,
    get_match_timeline,
    get_puuid,
    get_recent_matches,
    get_puuid_region,
    get_recent_matches_region,
)
from src.models.predict import predict_win_probability, get_advice
from src.features.build_features import FEATURE_COLS
from src.counters.lookup import get_matchup_data, LANE_WEIGHTS, _confidence

import joblib
import numpy as np
import pandas as pd

app = FastAPI(title="LoL Win Predictor API")

import os
_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_CORS_ORIGINS] if _CORS_ORIGINS != "*" else ["*"],
    allow_credentials=_CORS_ORIGINS != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chargement modèle + SHAP au démarrage
_model = None
_scaler = None
_shap_explainer = None

def _load_model():
    global _model, _scaler, _shap_explainer
    import shap
    _model = joblib.load("models/xgboost_final.pkl")
    if Path("models/scaler.pkl").exists():
        _scaler = joblib.load("models/scaler.pkl")
    base = _model.calibrated_classifiers_[0].estimator
    _shap_explainer = shap.TreeExplainer(base)

@app.on_event("startup")
def startup():
    _load_model()


class FeatureInput(BaseModel):
    kills_diff: float = 0
    deaths_diff: float = 0
    cs_diff: float = 0
    gold_diff: float = 0
    level_diff: float = 0
    towers_diff: float = 0
    dragons_diff: float = 0
    heralds_diff: float = 0
    barons_diff: float = 0
    kills_last_3min: float = 0
    game_time_minutes: float = 20
    # v2 features
    wards_diff: float = 0
    inhibitors_diff: float = 0
    damage_diff: float = 0
    first_blood: float = 0
    # v3 features
    xp_diff: float = 0
    plates_diff: float = 0
    current_gold_diff: float = 0
    dragon_soul: float = 0
    cc_diff: float = 0
    # v4 features
    void_grubs_diff: float = 0
    first_tower: float = 0
    infernal_diff: float = 0
    ocean_diff: float = 0
    elder_active: float = 0
    powerspike_diff: float = 0
    # v5 features
    mountain_diff: float = 0
    cloud_diff: float = 0
    chemtech_diff: float = 0
    hextech_diff: float = 0


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
def predict(features: FeatureInput):
    f = features.model_dump()
    proba = predict_win_probability(f)
    advice = get_advice(f, proba)

    # SHAP — contribution de chaque feature en delta de probabilité
    import shap
    X = pd.DataFrame([f])[FEATURE_COLS]
    shap_vals = _shap_explainer.shap_values(X)
    # shap_vals shape: (1, n_features) ou (2, 1, n_features) selon version
    if isinstance(shap_vals, list):
        sv = shap_vals[1][0]  # classe 1 (victoire)
    else:
        sv = shap_vals[0]

    # Convertir log-odds SHAP en delta probabilité approximatif
    base_log_odds = np.log(0.5 / 0.5)  # prior 50/50
    feature_impacts = {}
    for feat, sv_val in zip(FEATURE_COLS, sv):
        # delta prob = sigmoid(base + sv) - sigmoid(base)
        delta = float(1 / (1 + np.exp(-(base_log_odds + sv_val))) - 0.5)
        feature_impacts[feat] = round(delta * 100, 2)  # en %

    return {
        "blue_win_probability": round(proba * 100, 1),
        "advice": advice,
        "feature_impacts": feature_impacts,
    }


@app.get("/health")
def health():
    key = os.getenv("RIOT_API_KEY", "")
    return {"status": "ok", "riot_key_loaded": bool(key), "key_prefix": key[:12] if key else "MISSING"}

@app.get("/debug-env")
def debug_env():
    key = os.getenv("RIOT_API_KEY", "")
    return {"riot_key_loaded": bool(key), "key_prefix": key[:12] if key else "MISSING", "region": os.getenv("REGION", "NOT_SET")}


@app.get("/player/{riot_id}")
def get_player(riot_id: str):
    """riot_id = 'GameName-TAG' (tiret comme séparateur dans l'URL)"""
    if "-" not in riot_id:
        raise HTTPException(400, "Format invalide — utilisez GameName-TAG")
    parts = riot_id.rsplit("-", 1)
    game_name, tag = parts[0], parts[1]

    puuid = get_puuid(game_name, tag)
    if not puuid:
        raise HTTPException(404, f"Joueur {riot_id} introuvable")

    match_ids = get_recent_matches(puuid, count=10)
    games = []
    for mid in match_ids:
        info = get_match_info(mid)
        if not info:
            continue
        if info["info"].get("gameDuration", 0) < 15 * 60:
            continue
        summary = build_game_summary(info, puuid)
        if summary:
            games.append(summary)

    return {"puuid": puuid, "riot_id": riot_id, "games": games}


@app.get("/game/{match_id}")
def get_game(match_id: str, player_team: str = "blue"):
    info = get_match_info(match_id)
    timeline = get_match_timeline(match_id)
    if not info or not timeline:
        raise HTTPException(404, "Partie introuvable")

    game_data = extract_full_timeline(timeline, info)
    features_by_min = game_data["features_by_min"]
    key_events = game_data["key_events"]
    blue_won = game_data["blue_won"]

    # Matchup pré-game depuis les compositions (calculé avant la courbe pour le point minute 0)
    participants = info["info"].get("participants", [])
    blue_slots = [
        SlotInput(champion=p["championName"], role=p.get("teamPosition", ""))
        for p in participants if p.get("teamId") == 100 and p.get("championName")
    ]
    red_slots = [
        SlotInput(champion=p["championName"], role=p.get("teamPosition", ""))
        for p in participants if p.get("teamId") == 200 and p.get("championName")
    ]
    pregame_matchup = _compute_matchup_breakdown(blue_slots, red_slots)

    curve = []
    for snap in features_by_min:
        if snap["minute"] < 3:
            continue
        p_blue = predict_win_probability(snap)
        p_player = p_blue if player_team == "blue" else 1 - p_blue
        curve.append({
            "minute": snap["minute"],
            "blue_win_prob": round(p_blue * 100, 1),
            "player_win_prob": round(p_player * 100, 1),
            "gold_diff": snap["gold_diff"],
            "kills_diff": snap["kills_diff"],
            "is_pregame": False,
        })

    # Analyse "qui est fautif / qui a carry"
    blame    = _compute_blame(timeline, info, blue_won)
    snapshots = game_data.get("snapshots", [])
    player_analysis = _compute_player_analysis(timeline, info, key_events, snapshots)

    return {
        "match_id": match_id,
        "blue_won": blue_won,
        "duration_min": round(info["info"].get("gameDuration", 0) / 60, 1),
        "curve": curve,
        "key_events": key_events,
        "blame": blame,
        "pregame_matchup": pregame_matchup,
        "snapshots": snapshots,
        "player_analysis": player_analysis,
    }


def _compute_blame(timeline: dict, info: dict, blue_won: bool) -> list[dict]:
    """Score d'impact individuel par joueur."""
    participants = {p["participantId"]: p for p in info["info"]["participants"]}
    frames = timeline.get("info", {}).get("frames", [])

    # Accumulateurs par joueur
    stats = {pid: {
        "kills": 0, "deaths": 0, "assists": 0,
        "wards_placed": 0, "wards_killed": 0,
        "name": (lambda p: p.get("riotIdGameName") or p.get("summonerName") or f"P{pid}")(participants.get(pid, {})),
        "champion": participants.get(pid, {}).get("championName", "?"),
        "team": "blue" if pid <= 5 else "red",
        "won": participants.get(pid, {}).get("win", False),
        "cs": 0, "gold": 0,
    } for pid in range(1, 11)}

    team_kills = {"blue": 0, "red": 0}

    for frame in frames:
        # Stats à la dernière frame
        for pid_str, pf in frame.get("participantFrames", {}).items():
            pid = int(pid_str)
            if pid not in stats:
                continue
            cs = pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0)
            stats[pid]["cs"] = cs
            stats[pid]["gold"] = pf.get("totalGold", 0)

        for event in frame.get("events", []):
            etype = event.get("type")
            if etype == "CHAMPION_KILL":
                killer = event.get("killerId", 0)
                victim = event.get("victimId", 0)
                assists = event.get("assistingParticipantIds", [])
                if killer in stats:
                    stats[killer]["kills"] += 1
                    team_kills[stats[killer]["team"]] += 1
                if victim in stats:
                    stats[victim]["deaths"] += 1
                for a in assists:
                    if a in stats:
                        stats[a]["assists"] += 1
            elif etype == "WARD_PLACED":
                creator = event.get("creatorId", 0)
                if creator in stats:
                    stats[creator]["wards_placed"] += 1
            elif etype == "WARD_KILL":
                killer = event.get("killerId", 0)
                if killer in stats:
                    stats[killer]["wards_killed"] += 1

    result = []
    for pid, s in stats.items():
        team = s["team"]
        team_k = team_kills.get(team, 1) or 1
        kp = round((s["kills"] + s["assists"]) / team_k * 100)
        # Score impact : kill participation + vision - deaths penalty
        vision_score = s["wards_placed"] * 1.5 + s["wards_killed"] * 2
        impact = kp * 0.5 + vision_score * 0.3 - s["deaths"] * 3
        result.append({
            "pid": pid,
            "name": s["name"],
            "champion": s["champion"],
            "team": team,
            "won": s["won"],
            "kills": s["kills"],
            "deaths": s["deaths"],
            "assists": s["assists"],
            "kda_str": f"{s['kills']}/{s['deaths']}/{s['assists']}",
            "kill_participation": kp,
            "wards_placed": s["wards_placed"],
            "wards_killed": s["wards_killed"],
            "cs": s["cs"],
            "gold": s["gold"],
            "impact_score": round(impact, 1),
        })

    result.sort(key=lambda x: x["impact_score"], reverse=True)
    return result


_ROLE_LABEL = {"TOP": "top", "JUNGLE": "jungler", "MIDDLE": "mid", "BOTTOM": "ADC", "UTILITY": "support"}


def _compute_player_analysis(
    timeline: dict,
    info: dict,
    key_events: list[dict],
    snapshots: list[dict],
) -> dict[str, dict]:
    """Analyse contextuelle des morts par joueur : objectifs, tueurs récurrents, impact gold."""
    participants = {p["participantId"]: p for p in info["info"]["participants"]}
    frames = timeline.get("info", {}).get("frames", [])

    snap_by_min: dict[int, dict] = {s["minute"]: s for s in snapshots}
    max_snap = max(snap_by_min.keys(), default=0)

    OBJECTIVE_TYPES = {"dragon", "baron", "dragon_soul", "elder_dragon", "rift_herald"}
    objective_events = [e for e in key_events if e["type"] in OBJECTIVE_TYPES]

    deaths_by_pid: dict[int, list[dict]] = {pid: [] for pid in range(1, 11)}
    killer_counts: dict[int, dict[int, int]] = {pid: {} for pid in range(1, 11)}

    for frame in frames:
        for event in frame.get("events", []):
            if event.get("type") != "CHAMPION_KILL":
                continue
            victim = event.get("victimId", 0)
            killer = event.get("killerId", 0)
            if not (1 <= victim <= 10):
                continue

            ts_min = event.get("timestamp", 0) / 60000

            # Fenêtre ±1.5 min (plus précise que ±2)
            near_obj = next(
                (o for o in objective_events if abs(ts_min - o["min"]) <= 1.5), None
            )

            snap_min = min(int(ts_min), max_snap)
            snap_players = snap_by_min.get(snap_min, {}).get("players", {})
            killer_gold = snap_players.get(str(killer), {}).get("gold", 0) if killer else 0
            victim_gold = snap_players.get(str(victim), {}).get("gold", 0)

            killer_champ = participants.get(killer, {}).get("championName", "?") if killer else "?"

            deaths_by_pid[victim].append({
                "minute":    round(ts_min, 1),
                "killer_pid": killer,
                "killer_champ": killer_champ,
                "near_obj":  near_obj,
                "gold_delta": killer_gold - victim_gold,
            })

            if killer and 1 <= killer <= 10:
                killer_counts[victim][killer] = killer_counts[victim].get(killer, 0) + 1

    analysis: dict[str, dict] = {}

    for pid in range(1, 11):
        deaths   = deaths_by_pid[pid]
        n_deaths = len(deaths)
        p_info   = participants.get(pid, {})
        role     = p_info.get("teamPosition", "")
        rl       = _ROLE_LABEL.get(role, "")
        kills    = p_info.get("kills", 0)
        assists  = p_info.get("assists", 0)
        # Heuristique "joueur positif" : kills+assists >> morts
        is_positive = (kills + assists) >= n_deaths * 2 and n_deaths < 6

        facts:  list[str] = []
        advice: list[str] = []

        if n_deaths == 0:
            analysis[str(pid)] = {"facts": facts, "advice": advice}
            continue

        obj_deaths  = [d for d in deaths if d["near_obj"]]
        obj_ratio   = len(obj_deaths) / n_deaths

        # ── Fait 1 : morts sur timing objectif ───────────────────────────────
        # Seuil : min 3 morts obj OU 50%+ des morts, et non-pertinent pour joueurs positifs <3 obj deaths
        if obj_deaths and (len(obj_deaths) >= 3 or obj_ratio >= 0.5) and not (is_positive and len(obj_deaths) < 4):
            obj_mins  = ", ".join(f"{int(d['minute'])}m" for d in obj_deaths[:5])
            uniq_objs = list(dict.fromkeys(d["near_obj"]["label"] for d in obj_deaths))[:3]
            facts.append(
                f"{len(obj_deaths)}/{n_deaths} morts lors de combats d'objectifs "
                f"({', '.join(uniq_objs)}) — à {obj_mins}"
            )
            if role == "JUNGLE":
                advice.append(
                    f"En tant que jungler, setup la vision côté ennemi 2 min avant chaque objectif. "
                    "N'engage que si tu as la vision complète et le chiffre en ta faveur — "
                    "mourir en essayant de prendre un objectif non sécurisé fait perdre les deux."
                )
            elif role == "UTILITY":
                advice.append(
                    "Warde les accès ennemis vers l'objectif 2 minutes avant son apparition. "
                    "Communique l'info à ton équipe et recule si vous n'êtes pas en bonne position — "
                    "forcer un combat déséquilibré sur un objectif est souvent pire que de le laisser."
                )
            elif role == "BOTTOM":
                advice.append(
                    "2 minutes avant chaque drake ou baron, replie-toi vers ta team plutôt que de rester en lane. "
                    "Ta survie dans le teamfight d'objectif pèse plus que quelques CS supplémentaires."
                )
            elif role == "TOP":
                advice.append(
                    "Anticipe les timings d'objectifs pour rejoindre ta team ou avoir ton TP disponible. "
                    "Rester bloqué en top lane lors d'un combat de drake met ton équipe en sous-nombre."
                )
            else:
                advice.append(
                    "Rejoins ton équipe en avance sur les timings d'objectifs. "
                    "Chaque mort lors d'un combat d'objectif coûte double : la mort ET potentiellement l'objectif."
                )

        # ── Fait 2 : tueur récurrent ─────────────────────────────────────────
        if killer_counts[pid]:
            top_kpid  = max(killer_counts[pid], key=killer_counts[pid].get)
            top_count = killer_counts[pid][top_kpid]
            top_champ = participants.get(top_kpid, {}).get("championName", "?")

            if top_count >= 3:
                relevant   = [d for d in deaths if d["killer_pid"] == top_kpid]
                avg_delta  = int(sum(d["gold_delta"] for d in relevant) / len(relevant))
                death_mins = ", ".join(f"{int(d['minute'])}m" for d in relevant[:5])

                if abs(avg_delta) > 500:
                    gold_ctx = (
                        f" — à ces moments, {top_champ} avait en moyenne "
                        f"{abs(avg_delta):,}g {'d\'avance' if avg_delta > 0 else 'de retard'} sur toi"
                    )
                else:
                    gold_ctx = ""

                facts.append(
                    f"Tué {top_count} fois par {top_champ} (à {death_mins}){gold_ctx}"
                )

                if top_count >= 6:
                    advice.append(
                        f"{top_champ} t'a tué {top_count} fois — ce duel est perdu. "
                        "Ne t'expose plus seul contre cet adversaire : joue groupé et attends un teamfight "
                        "pour avoir de l'aide autour de toi."
                    )
                elif top_count >= 4:
                    advice.append(
                        f"Tu n'es pas en mesure de gagner les duels contre {top_champ} dans cet état du jeu. "
                        "Demande un camp à ton jungler sur cette lane, joue sous ta tour et attends des ressources."
                    )
                else:
                    advice.append(
                        f"Adapte ton positionnement face à {top_champ} : "
                        "limite les trades en dehors de ta zone de sécurité "
                        "et communique avec ton jungler pour un gank sur ce matchup."
                    )

        # ── Fait 3 : volume de morts élevé ───────────────────────────────────
        if n_deaths >= 8:
            bounty_est = 300 + min(n_deaths // 3, 5) * 50
            facts.append(
                f"{n_deaths} morts au total — en moyenne ~{bounty_est}g par mort remis à l'ennemi, "
                f"soit ~{n_deaths * bounty_est:,}g de gold cadeau sur la partie"
            )
            advice.append(
                f"Avec {n_deaths} morts, tu as offert une quantité de gold décisive à l'adversaire. "
                "Identifie les patterns où tu meurs — duels perdus, overextend, mauvais timing — "
                "et adopte un style de jeu plus conservateur sur les 5 prochaines minutes critiques."
            )

        # ── Fait 4 : KDA nul avec beaucoup de morts ──────────────────────────
        if n_deaths >= 5 and (kills + assists) <= 2:
            facts.append(
                f"Ratio offensif très faible : {kills} kills et {assists} assists pour {n_deaths} morts — "
                "ton impact net sur la partie est négatif"
            )
            advice.append(
                "Focusse-toi sur des actions à faible risque : farm sécurisé, wards, aides défensives. "
                "Cherche à survivre et à rendre les combats plus équilibrés plutôt qu'à forcer des plays."
            )

        analysis[str(pid)] = {"facts": facts, "advice": advice}

    return analysis


# ─── Section Pro ─────────────────────────────────────────────────────────────

BLG_ROSTER = [
    {"name": "Bin",    "role": "Top",     "game_name": "BLG bin",   "tag": "TOP", "region": "kr"},
    {"name": "XUN",    "role": "Jungle",  "game_name": "BLG Xun",   "tag": "VvV", "region": "kr"},
    {"name": "Knight", "role": "Mid",     "game_name": "BLG knight","tag": "0000","region": "euw"},
    {"name": "Viper",  "role": "ADC",     "game_name": "Viper",     "tag": "BLG", "region": "kr"},
    {"name": "ON",     "role": "Support", "game_name": "ON",        "tag": "BLG", "region": "kr"},
]


@app.get("/pro/roster")
def get_pro_roster():
    return {"team": "Bilibili Gaming", "region": "LPL", "players": BLG_ROSTER}


@app.get("/pro/dataset-stats")
def get_dataset_stats():
    """Retourne les stats moyennes de notre dataset Master+ EUW pour comparaison."""
    try:
        df = pd.read_parquet("data/processed/features.parquet")
        stats = {}
        from src.features.build_features import SNAPSHOT_MINUTES
        for minute in SNAPSHOT_MINUTES:
            snap = df[df["game_time_minutes"] == minute]
            if snap.empty:
                continue
            total_games = len(snap)
            stats[minute] = {
                "gold_per_player": round((snap["gold_diff"].abs().mean() / 2 + snap["gold_diff"].mean() / 2) / 5, 0),
                "cs_per_player": round(snap["cs_diff"].abs().mean() / 10 + 50, 1),
                "wards_total": round(snap["wards_diff"].abs().mean() / 2 + 15, 1),
                "damage_per_player": round(snap["damage_diff"].abs().mean() / 10 + 8000, 0),
                "kills_total": round(snap["kills_diff"].abs().mean() / 2 + 5, 1),
                "n_games": total_games,
            }
        # Compute proper per-team averages from raw data
        # We only have diffs, so estimate absolute values from typical game state
        result_stats = {}
        for minute in SNAPSHOT_MINUTES:
            snap = df[df["game_time_minutes"] == minute]
            if snap.empty:
                continue
            # Gold: typical total for winning team at this minute
            avg_gold_diff = snap["gold_diff"].mean()
            # Rough estimate: average blue gold = base + diff/2
            base_gold_at_min = {10: 4200, 15: 5800, 20: 7500, 25: 9500, 30: 12000}
            base = base_gold_at_min.get(minute, 8000)
            result_stats[minute] = {
                "gold_per_player": round(base + avg_gold_diff / 10, 0),
                "cs_per_player": round(60 + minute * 4.2, 1),  # typical CS curve
                "wards_diff_avg": round(float(snap["wards_diff"].mean()), 2),
                "damage_diff_avg": round(float(snap["damage_diff"].mean()), 0),
                "kills_diff_avg": round(float(snap["kills_diff"].mean()), 2),
                "blue_winrate": round(float(snap["blue_wins"].mean()) * 100, 1),
                "n_games": len(snap),
            }
        return {"master_euw": result_stats}
    except Exception as e:
        raise HTTPException(500, f"Erreur stats dataset: {e}")


@app.get("/pro/player/{game_name}/{tag}")
def get_pro_player(game_name: str, tag: str, region: str = "kr"):
    """Récupère les parties récentes d'un joueur pro (toutes queues, pas seulement ranked solo)."""
    from src.data.fetch_player import (
        get_puuid_region, get_recent_matches_region,
        get_match_info_region, get_match_timeline_region,
    )
    puuid = get_puuid_region(game_name, tag, region)
    if not puuid:
        raise HTTPException(404, f"Joueur {game_name}#{tag} introuvable sur {region}")

    match_ids = get_recent_matches_region(puuid, region=region, count=10)
    games = []
    for mid in match_ids:
        info = get_match_info_region(mid, region)
        if not info:
            continue
        if info["info"].get("gameDuration", 0) < 15 * 60:
            continue
        summary = build_game_summary(info, puuid)
        if summary:
            games.append(summary)

    return {"puuid": puuid, "game_name": game_name, "tag": tag, "region": region, "games": games}


@app.get("/pro/game/{match_id}")
def get_pro_game(match_id: str, player_team: str = "blue", region: str = "kr"):
    """Analyse d'une partie pro avec courbe win% + blame."""
    from src.data.fetch_player import get_match_info_region, get_match_timeline_region
    info = get_match_info_region(match_id, region)
    timeline = get_match_timeline_region(match_id, region)
    if not info or not timeline:
        raise HTTPException(404, "Partie introuvable")

    game_data = extract_full_timeline(timeline, info)
    features_by_min = game_data["features_by_min"]
    key_events = game_data["key_events"]
    blue_won = game_data["blue_won"]

    curve = []
    for snap in features_by_min:
        if snap["minute"] < 3:
            continue
        p_blue = predict_win_probability(snap)
        p_player = p_blue if player_team == "blue" else 1 - p_blue
        curve.append({
            "minute": snap["minute"],
            "blue_win_prob": round(p_blue * 100, 1),
            "player_win_prob": round(p_player * 100, 1),
            "gold_diff": snap["gold_diff"],
            "kills_diff": snap["kills_diff"],
        })

    blame = _compute_blame(timeline, info, blue_won)

    return {
        "match_id": match_id,
        "blue_won": blue_won,
        "duration_min": round(info["info"].get("gameDuration", 0) / 60, 1),
        "curve": curve,
        "key_events": key_events,
        "blame": blame,
        "is_pro": True,
    }


# ─── Tournois lolesports ──────────────────────────────────────────────────────

@app.get("/pro/schedule/{league}")
def get_tournament_schedule(league: str = "lec", count: int = 8):
    """Retourne les derniers matchs terminés d'une league (lec, lck, msi, worlds)."""
    from src.data.fetch_esports import get_recent_matches, get_match_games
    matches = get_recent_matches(league, count)
    for m in matches:
        m["games"] = get_match_games(m["match_id"])
    return {"league": league.upper(), "matches": matches}


@app.get("/pro/esports-game/{esports_game_id}")
def get_esports_game(esports_game_id: str):
    """Analyse une game de tournoi via lolesports live stats + notre modèle."""
    from src.data.fetch_esports import fetch_game_curve
    result = fetch_game_curve(esports_game_id, predict_win_probability)
    if not result:
        raise HTTPException(404, "Game introuvable ou données live stats indisponibles")
    return result


# ─── Synergy / Draft ─────────────────────────────────────────────────────────

_synergy_db: dict | None = None
_roles_db: dict | None = None

ROLE_DISPLAY = {
    "TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
    "BOTTOM": "ADC", "UTILITY": "Support",
}
ROLES_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def _load_synergy():
    global _synergy_db
    if _synergy_db is None:
        p = Path("data/processed/synergy_scores.json")
        _synergy_db = json.load(open(p)) if p.exists() else {}
    return _synergy_db


def _load_roles():
    global _roles_db
    if _roles_db is None:
        p = Path("data/processed/champion_roles.json")
        _roles_db = json.load(open(p)) if p.exists() else {}
    return _roles_db


def _pair_wr(db: dict, a: str, b: str) -> float:
    key = "|".join(sorted([a, b]))
    entry = db.get(key)
    return entry["winrate"] if entry else 0.5


def _meta_ratio(roles_db: dict, champion: str, role: str) -> float:
    """Fraction des parties jouées dans ce rôle (0–1). 0 si aucune donnée."""
    entry = roles_db.get(champion, {})
    total = sum(v["games"] for k, v in entry.items() if not k.startswith("_"))
    if not total:
        return 0.0
    role_games = entry.get(role, {}).get("games", 0)
    return role_games / total


def _off_meta_penalty(ratio: float) -> float:
    """Pénalité en points de WR (0–0.15)."""
    if ratio >= 0.20:
        return 0.0
    if ratio >= 0.05:
        return 0.05   # borderline
    return 0.15       # vraiment off-meta


@app.get("/champions")
def list_champions():
    """Liste tous les champions avec leur rôle principal."""
    roles_db = _load_roles()
    synergy_db = _load_synergy()
    champs_in_synergy: set[str] = set()
    for key in synergy_db:
        a, b = key.split("|")
        champs_in_synergy.add(a); champs_in_synergy.add(b)

    result = {}
    for champ in champs_in_synergy:
        entry = roles_db.get(champ, {})
        primary = entry.get("_primary", "MIDDLE")
        # Rôles viables = meta_ratio >= 5%
        total = sum(v["games"] for k, v in entry.items() if not k.startswith("_"))
        viable = [
            r for r in ROLES_ORDER
            if entry.get(r, {}).get("games", 0) / max(total, 1) >= 0.05
        ]
        result[champ] = {"primary": primary, "viable": viable or [primary]}

    return {"champions": result}


@app.get("/synergy/{champion}")
def get_synergy(champion: str, top: int = 10):
    db = _load_synergy()
    results = []
    for key, v in db.items():
        a, b = key.split("|")
        if a == champion:
            results.append({"champion": b, **v})
        elif b == champion:
            results.append({"champion": a, **v})

    if not results:
        raise HTTPException(404, f"Champion {champion} non trouvé dans le dataset")

    results.sort(key=lambda x: -x["winrate"])
    best  = [r for r in results if r["games"] >= 10][:top]
    worst = sorted([r for r in results if r["games"] >= 10], key=lambda x: x["winrate"])[:top]
    return {"champion": champion, "best": best, "worst": worst}


class SlotInput(BaseModel):
    champion: str
    role: str = ""   # "TOP" | "JUNGLE" | "MIDDLE" | "BOTTOM" | "UTILITY" | ""


class DraftInput(BaseModel):
    blue: list[SlotInput]
    red: list[SlotInput]


_RIOT_ROLE_TO_COUNTER = {
    "TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid",
    "BOTTOM": "adc", "UTILITY": "support",
}


def _compute_matchup_breakdown(blue_slots: list[SlotInput], red_slots: list[SlotInput]) -> dict:
    """Calcule le détail matchup par lane (counter data OP.GG) avec pondération rôle × confiance."""
    blue_by_role = {s.role: s.champion for s in blue_slots if s.champion and s.role}
    red_by_role  = {s.role: s.champion for s in red_slots  if s.champion and s.role}

    lanes = []
    for riot_role, counter_role in _RIOT_ROLE_TO_COUNTER.items():
        b = blue_by_role.get(riot_role)
        r = red_by_role.get(riot_role)
        if not b or not r:
            continue
        data    = get_matchup_data(b, r)
        wr      = data["wr"]      if data else None
        n_games = data["n_games"] if data else None
        rw      = LANE_WEIGHTS.get(counter_role, 0.20)
        conf    = _confidence(n_games) if n_games else 0.0
        delta   = (wr - 50.0) / 100.0 if wr is not None else None
        lanes.append({
            "role":           counter_role,
            "blue":           b,
            "red":            r,
            "wr_blue":        round(wr, 1) if wr is not None else None,
            "n_games":        n_games,
            "role_weight":    rw,
            "confidence":     round(conf, 3),
            "weighted_delta": round(rw * conf * delta, 4) if delta is not None else None,
        })

    resolved = [l for l in lanes if l["wr_blue"] is not None]
    total_w  = sum(l["role_weight"] * l["confidence"] for l in resolved)

    if resolved and total_w > 0:
        raw_delta = sum(
            l["role_weight"] * l["confidence"] * (l["wr_blue"] - 50.0) / 100.0
            for l in resolved
        ) / total_w
        p_matchup = round(max(0.35, min(0.65, 0.5 + raw_delta)), 4)
    else:
        p_matchup = None

    return {"lanes": lanes, "p_matchup": p_matchup, "n_resolved": len(resolved)}


@app.post("/draft/predict")
def draft_predict(draft: DraftInput):
    """
    Prédit le win% de l'équipe bleue.
    Prend en compte : synergie des paires + pénalité off-meta par rôle.
    """
    syn_db   = _load_synergy()
    roles_db = _load_roles()
    from itertools import combinations as _comb

    def team_score(slots: list[SlotInput]) -> tuple[float, list[dict], list[dict]]:
        champs = [s.champion for s in slots if s.champion]
        if not champs:
            return 0.5, [], []

        # Synergie paires
        wrs = [_pair_wr(syn_db, a, b) for a, b in _comb(champs, 2)] if len(champs) >= 2 else [0.5]
        syn = sum(wrs) / len(wrs)

        # Pénalité off-meta
        off_meta_total = 0.0
        off_meta_details = []
        for s in slots:
            if not s.champion or not s.role:
                continue
            ratio = _meta_ratio(roles_db, s.champion, s.role)
            pen   = _off_meta_penalty(ratio)
            off_meta_total += pen
            if pen > 0:
                off_meta_details.append({
                    "champion": s.champion,
                    "role": ROLE_DISPLAY.get(s.role, s.role),
                    "meta_ratio": round(ratio * 100, 1),
                    "penalty": round(pen * 100, 1),
                })

        avg_penalty = off_meta_total / max(len(slots), 1)
        adjusted_syn = syn - avg_penalty

        # Détail paires
        pair_details = []
        for a, b in _comb(champs, 2):
            wr  = _pair_wr(syn_db, a, b)
            key = "|".join(sorted([a, b]))
            games = syn_db.get(key, {}).get("games", 0)
            pair_details.append({"pair": f"{a} + {b}", "winrate": round(wr * 100, 1), "games": games})
        pair_details.sort(key=lambda x: -x["winrate"])

        return adjusted_syn, pair_details, off_meta_details

    blue_syn, blue_pairs, blue_off = team_score(draft.blue)
    red_syn,  red_pairs,  red_off  = team_score(draft.red)

    raw = 0.5 + (blue_syn - red_syn) * 3.0
    blue_prob = max(0.1, min(0.9, raw))

    matchup = _compute_matchup_breakdown(draft.blue, draft.red)

    return {
        "blue_win_probability": round(blue_prob * 100, 1),
        "blue_synergy":         round(blue_syn * 100, 1),
        "red_synergy":          round(red_syn * 100, 1),
        "blue_pairs":           blue_pairs,
        "red_pairs":            red_pairs,
        "blue_off_meta":        blue_off,
        "red_off_meta":         red_off,
        "matchup_breakdown":    matchup,
    }


class SynergyPuzzleRequest(BaseModel):
    team: list[str]   # 4 champions (déjà sur leurs rôles)
    role: str         # rôle Riot du 5e : "TOP" | "JUNGLE" | "MIDDLE" | "BOTTOM" | "UTILITY"


@app.post("/draft/puzzle")
def draft_puzzle(req: SynergyPuzzleRequest):
    """
    Mini-jeu : retourne le classement des meilleurs picks pour le rôle manquant,
    filtré sur les champions méta dans ce rôle (>= 5% de leurs games).
    """
    syn_db   = _load_synergy()
    roles_db = _load_roles()
    champs_in_team = set(req.team)

    all_champs: set[str] = set()
    for key in syn_db:
        a, b = key.split("|")
        all_champs.add(a); all_champs.add(b)

    candidates = []
    for c in all_champs:
        if c in champs_in_team:
            continue
        ratio = _meta_ratio(roles_db, c, req.role)
        if ratio < 0.05:
            continue   # pas méta dans ce rôle
        avg = sum(_pair_wr(syn_db, c, t) for t in req.team) / len(req.team)
        candidates.append({
            "champion": c,
            "avg_synergy": round(avg * 100, 1),
            "meta_ratio": round(ratio * 100, 1),
        })

    if not candidates:
        # Fallback : pas de filtre rôle si aucun candidat
        for c in all_champs:
            if c in champs_in_team:
                continue
            avg = sum(_pair_wr(syn_db, c, t) for t in req.team) / len(req.team)
            candidates.append({"champion": c, "avg_synergy": round(avg * 100, 1), "meta_ratio": 0.0})

    candidates.sort(key=lambda x: -x["avg_synergy"])
    return {
        "team": req.team,
        "role": req.role,
        "role_display": ROLE_DISPLAY.get(req.role, req.role),
        "answer": candidates[0]["champion"],
        "ranking": candidates[:20],
    }
