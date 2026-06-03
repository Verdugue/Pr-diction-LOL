"""
Wrapper pour la Live Client Data API de League of Legends.
URL locale : https://127.0.0.1:2999/liveclientdata/allgamedata

La Live Client API utilise un certificat auto-signé → verify=False requis.
"""

from typing import Optional

import requests
import urllib3

from src.features.build_features import FEATURE_COLS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_CLIENT_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

BLUE_TEAM = "ORDER"
RED_TEAM = "CHAOS"

# Mapping DragonType (interne Riot) → nom usuel
DRAGON_TYPE_MAP = {
    "Fire": "infernal",
    "Water": "ocean",
    "Air": "cloud",
    "Earth": "mountain",
    "Hextech": "hextech",
    "Chemtech": "chemtech",
    "Elder": "elder",
}


def fetch_live_state() -> Optional[dict]:
    """Retourne le JSON brut de la partie en cours, ou None si hors partie."""
    try:
        resp = requests.get(LIVE_CLIENT_URL, verify=False, timeout=3)
        if resp.status_code == 200:
            return resp.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        pass
    return None


def _build_player_index(players: list[dict]) -> dict[str, str]:
    """
    Map riotIdGameName + summonerName → team (ORDER/CHAOS).
    Les events de la Live Client API utilisent ces champs comme KillerName.
    """
    index = {}
    for p in players:
        team = p.get("team")
        if not team:
            continue
        for key in ("riotIdGameName", "summonerName", "riotId"):
            v = p.get(key)
            if v:
                index[v] = team
    return index


def _aggregate_team_stats(players: list[dict], team: str) -> dict:
    kills = deaths = cs = ward_score = level = items_value = 0
    n_players = 0
    for p in players:
        if p.get("team") != team or p.get("isBot"):
            continue
        n_players += 1
        s = p.get("scores", {}) or {}
        kills += s.get("kills", 0)
        deaths += s.get("deaths", 0)
        cs += s.get("creepScore", 0)
        ward_score += s.get("wardScore", 0) or 0
        level += p.get("level", 1)

        items = p.get("items", []) or []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    items_value += it.get("price", 0) * it.get("count", 1)

    return {
        "kills": kills,
        "deaths": deaths,
        "cs": cs,
        "ward_score": ward_score,
        "level_sum": level,
        "n_players": n_players,
        "items_value": items_value,
    }


def _team_from_turret_name(turret_name: str) -> Optional[str]:
    """Renvoie l'équipe qui a détruit la tour (= adversaire du propriétaire).
    Naming Riot actuel : Turret_TOrder_... / Turret_TChaos_...
    Ancien naming (compat) : Turret_T1_... / Turret_T2_..."""
    if "TOrder" in turret_name:
        return RED_TEAM   # tour ORDER → détruite par CHAOS
    if "TChaos" in turret_name:
        return BLUE_TEAM  # tour CHAOS → détruite par ORDER
    if "T1" in turret_name:
        return RED_TEAM
    if "T2" in turret_name:
        return BLUE_TEAM
    return None


def _team_from_inhib_name(inhib_name: str) -> Optional[str]:
    """Renvoie l'équipe qui a détruit l'inhib (= adversaire du propriétaire).
    Naming Riot actuel : Barracks_TOrder_... / Barracks_TChaos_...
    Ancien naming (compat) : Barracks_T1_... / Barracks_T2_..."""
    if "TOrder" in inhib_name:
        return RED_TEAM
    if "TChaos" in inhib_name:
        return BLUE_TEAM
    if "T1" in inhib_name:
        return RED_TEAM
    if "T2" in inhib_name:
        return BLUE_TEAM
    return None


def _aggregate_events(events: list[dict], player_team: dict[str, str], game_time_sec: float) -> dict:
    """Parcourt les events une seule fois et accumule tous les compteurs par équipe."""
    counts = {
        BLUE_TEAM: {
            "towers": 0, "inhibitors": 0, "barons": 0, "heralds": 0, "void_grubs": 0,
            "kills_recent": 0,
            "dragons_total": 0,
            "infernal": 0, "ocean": 0, "cloud": 0, "mountain": 0,
            "hextech": 0, "chemtech": 0, "elder": 0,
            "last_elder_time": -9999,
        },
        RED_TEAM: {
            "towers": 0, "inhibitors": 0, "barons": 0, "heralds": 0, "void_grubs": 0,
            "kills_recent": 0,
            "dragons_total": 0,
            "infernal": 0, "ocean": 0, "cloud": 0, "mountain": 0,
            "hextech": 0, "chemtech": 0, "elder": 0,
            "last_elder_time": -9999,
        },
    }

    first_blood_team: Optional[str] = None
    first_tower_team: Optional[str] = None

    recent_threshold = game_time_sec - 180  # 3 dernières minutes

    for ev in events:
        etype = ev.get("EventName", "")
        ev_time = ev.get("EventTime", 0)
        killer_name = ev.get("KillerName", "") or ""

        if etype == "TurretKilled":
            turret = ev.get("TurretKilled", "") or ""
            team = _team_from_turret_name(turret)
            if team:
                counts[team]["towers"] += 1
                if first_tower_team is None:
                    first_tower_team = team
            continue

        if etype == "InhibKilled":
            inhib = ev.get("InhibKilled", "") or ""
            team = _team_from_inhib_name(inhib)
            if team:
                counts[team]["inhibitors"] += 1
            continue

        if etype == "ChampionKill":
            team = player_team.get(killer_name)
            if team:
                if first_blood_team is None:
                    first_blood_team = team
                if ev_time >= recent_threshold:
                    counts[team]["kills_recent"] += 1
            continue

        if etype == "DragonKill":
            team = player_team.get(killer_name)
            if not team:
                continue
            dragon_type_raw = ev.get("DragonType", "")
            mapped = DRAGON_TYPE_MAP.get(dragon_type_raw)
            if mapped == "elder":
                counts[team]["elder"] += 1
                counts[team]["last_elder_time"] = ev_time
            elif mapped:
                counts[team][mapped] += 1
                counts[team]["dragons_total"] += 1
            else:
                counts[team]["dragons_total"] += 1
            continue

        if etype == "BaronKill":
            team = player_team.get(killer_name)
            if team:
                counts[team]["barons"] += 1
            continue

        if etype == "HeraldKill":
            team = player_team.get(killer_name)
            if team:
                counts[team]["heralds"] += 1
            continue

        if etype == "HordeKill":
            team = player_team.get(killer_name)
            if team:
                counts[team]["void_grubs"] += 1
            continue

    return {
        "counts": counts,
        "first_blood_team": first_blood_team,
        "first_tower_team": first_tower_team,
    }


def _estimate_team_gold(stats: dict, game_time_min: float) -> int:
    """
    Estimation grossière du gold total d'une équipe.
    La Live Client API n'expose le gold qu'au joueur actif (currentGold), pas aux autres.
    Sources :
      - Gold passif : 500 starting + 20.4 g/sec
      - CS gold : ~21 g par CS
      - Kill gold : ~300 par kill
      - Items équipés : ~20% du prix (proxy du gold dépensé non re-équipé)
    """
    n = max(stats["n_players"], 1)
    passive_gold = (500 + 20.4 * game_time_min * 60 / 60) * n
    cs_gold = stats["cs"] * 21
    kill_gold = stats["kills"] * 300
    item_value = stats["items_value"]
    return int(passive_gold + cs_gold + kill_gold + item_value * 0.2)


def extract_features(state: dict) -> Optional[dict]:
    """
    Transforme le JSON de la Live Client API en dict de 30 features.
    Retourne None si les données sont insuffisantes.

    Features non disponibles dans la Live Client API (forcées à 0 ou approximées) :
      - damage_diff, cc_diff, current_gold_diff, plates_diff : non exposés
      - powerspike_diff : approximé via valeur des items
      - xp_diff : approximé via level_diff × 1000
    """
    players = state.get("allPlayers", []) or []
    events_raw = (state.get("events") or {}).get("Events", []) or []
    game_data = state.get("gameData", {}) or {}

    if not players:
        return None

    game_time_sec = game_data.get("gameTime", 0)
    game_time_min = game_time_sec / 60.0

    player_team = _build_player_index(players)

    blue = _aggregate_team_stats(players, BLUE_TEAM)
    red = _aggregate_team_stats(players, RED_TEAM)

    n_blue = max(blue["n_players"], 1)
    n_red = max(red["n_players"], 1)

    agg = _aggregate_events(events_raw, player_team, game_time_sec)
    cb = agg["counts"][BLUE_TEAM]
    cr = agg["counts"][RED_TEAM]

    blue_gold = _estimate_team_gold(blue, game_time_min)
    red_gold = _estimate_team_gold(red, game_time_min)

    level_diff = (blue["level_sum"] / n_blue) - (red["level_sum"] / n_red)

    blue_elem = cb["infernal"] + cb["ocean"] + cb["cloud"] + cb["mountain"] + cb["hextech"] + cb["chemtech"]
    red_elem = cr["infernal"] + cr["ocean"] + cr["cloud"] + cr["mountain"] + cr["hextech"] + cr["chemtech"]
    dragon_soul = 0
    if blue_elem >= 4 and blue_elem > red_elem:
        dragon_soul = 1
    elif red_elem >= 4 and red_elem > blue_elem:
        dragon_soul = -1

    elder_active = 0
    if game_time_sec - cb["last_elder_time"] < 180:
        elder_active = 1
    elif game_time_sec - cr["last_elder_time"] < 180:
        elder_active = -1

    fb_team = agg["first_blood_team"]
    first_blood = 1 if fb_team == BLUE_TEAM else (-1 if fb_team == RED_TEAM else 0)
    ft_team = agg["first_tower_team"]
    first_tower = 1 if ft_team == BLUE_TEAM else (-1 if ft_team == RED_TEAM else 0)

    powerspike_diff = (blue["items_value"] - red["items_value"]) / 3000.0
    xp_diff = level_diff * 1000.0

    features = {
        # v1
        "kills_diff": blue["kills"] - red["kills"],
        "deaths_diff": blue["deaths"] - red["deaths"],
        "cs_diff": blue["cs"] - red["cs"],
        "gold_diff": blue_gold - red_gold,
        "level_diff": level_diff,
        "towers_diff": cb["towers"] - cr["towers"],
        "dragons_diff": (cb["dragons_total"] + cb["elder"]) - (cr["dragons_total"] + cr["elder"]),
        "heralds_diff": cb["heralds"] - cr["heralds"],
        "barons_diff": cb["barons"] - cr["barons"],
        "kills_last_3min": cb["kills_recent"] - cr["kills_recent"],
        "game_time_minutes": game_time_min,
        # v2
        "wards_diff": blue["ward_score"] - red["ward_score"],
        "inhibitors_diff": cb["inhibitors"] - cr["inhibitors"],
        "damage_diff": 0.0,
        "first_blood": first_blood,
        # v3
        "xp_diff": xp_diff,
        "plates_diff": 0.0,
        "current_gold_diff": 0.0,
        "dragon_soul": dragon_soul,
        "cc_diff": 0.0,
        # v4
        "void_grubs_diff": cb["void_grubs"] - cr["void_grubs"],
        "first_tower": first_tower,
        "infernal_diff": cb["infernal"] - cr["infernal"],
        "ocean_diff": cb["ocean"] - cr["ocean"],
        "elder_active": elder_active,
        "powerspike_diff": powerspike_diff,
        # v5
        "mountain_diff": cb["mountain"] - cr["mountain"],
        "cloud_diff": cb["cloud"] - cr["cloud"],
        "chemtech_diff": cb["chemtech"] - cr["chemtech"],
        "hextech_diff": cb["hextech"] - cr["hextech"],
    }

    # Garantit la présence de toutes les colonnes FEATURE_COLS (sécurité si build_features.py évolue)
    for col in FEATURE_COLS:
        features.setdefault(col, 0.0)

    return features


def get_current_features() -> Optional[dict]:
    """API publique : retourne les features live ou None si hors partie."""
    state = fetch_live_state()
    if state is None:
        return None
    return extract_features(state)


# Features qui ne doivent PAS changer de signe quand on bascule la perspective.
_NON_FLIPPABLE = {"game_time_minutes"}


def detect_active_team(state: dict) -> Optional[str]:
    """Retourne 'ORDER' (BLUE) ou 'CHAOS' (RED) pour le joueur local, None si introuvable."""
    active = state.get("activePlayer") or {}
    active_name = (
        active.get("riotIdGameName")
        or active.get("summonerName")
        or active.get("riotId")
    )
    if not active_name:
        return None
    for p in state.get("allPlayers", []) or []:
        for key in ("riotIdGameName", "summonerName", "riotId"):
            if p.get(key) == active_name:
                return p.get("team")
    return None


def flip_features_perspective(features: dict) -> dict:
    """Renvoie une copie de `features` du point de vue de l'équipe adverse :
    tous les `*_diff` et polarités (first_blood, dragon_soul, etc.) changent de signe."""
    flipped = {}
    for k, v in features.items():
        if k in _NON_FLIPPABLE or not isinstance(v, (int, float)):
            flipped[k] = v
        else:
            flipped[k] = -v
    return flipped
