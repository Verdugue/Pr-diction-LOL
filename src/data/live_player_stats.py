"""
Orchestrateur : à partir d'un état Live Client API, fetch en parallèle
la mastery de chaque joueur sur le champion qu'il joue.

Resultats utilisés par app/main.py pour :
  - afficher un panel sidebar mastery par lane
  - calculer un mastery_offset injecté dans le prior pré-game
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from src.data.champion_index import get_champion_id
from src.data.fetch_player import get_champion_mastery, get_puuid_region

load_dotenv()

DEFAULT_REGION = os.getenv("REGION", "euw1")          # platform endpoint (mastery)
DEFAULT_ACCOUNT_REGION = "euw"                          # routing pour get_puuid_region


def fetch_player_mastery(player: dict, account_region: str = DEFAULT_ACCOUNT_REGION,
                          platform_region: str = DEFAULT_REGION) -> dict:
    """Récupère la mastery du joueur sur son champion actuel.

    Retourne un dict avec au minimum {team, champion} et soit :
      - une mastery valide {mastery_points, mastery_level}
      - une mastery à 0 si jamais joué le champ
      - un champ 'error' explicatif sinon

    Robuste aux erreurs (pas de raise) — un échec sur 1 joueur ne casse
    pas les 9 autres.
    """
    team = player.get("team", "")
    champ = player.get("championName", "")
    game_name = player.get("riotIdGameName", "")
    tag = player.get("riotIdTagLine", "")

    result: dict = {
        "team": team,
        "champion": champ,
        "game_name": game_name,
        "tag": tag,
        "mastery_points": 0,
        "mastery_level": 0,
        "error": None,
    }

    if not (game_name and tag):
        result["error"] = "missing riotIdGameName/tagLine"
        return result

    puuid = get_puuid_region(game_name, tag, region=account_region)
    if not puuid:
        result["error"] = "puuid not found"
        return result
    result["puuid"] = puuid

    champ_id = get_champion_id(champ)
    if not champ_id:
        result["error"] = f"champion id unknown for '{champ}'"
        return result

    mastery = get_champion_mastery(puuid, champ_id, region=platform_region)
    if mastery is None:
        # Jamais joué ce champ → on garde 0 (signal valide, pas une erreur)
        result["mastery_points"] = 0
        result["mastery_level"] = 0
        return result

    result["mastery_points"] = mastery.get("championPoints", 0)
    result["mastery_level"] = mastery.get("championLevel", 0)
    return result


def fetch_all_player_stats(state: dict, max_workers: int = 10) -> list[dict]:
    """Fetch parallèle des mastery pour les 10 joueurs d'un game state.

    Garde l'ordre d'origine des joueurs (BLUE puis RED, role par role tel que
    fourni par la Live Client API).
    """
    players = state.get("allPlayers") or []
    if not players:
        return []

    results: list[dict] = [None] * len(players)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_idx = {
            ex.submit(fetch_player_mastery, p): i
            for i, p in enumerate(players)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                results[idx] = {
                    "team": players[idx].get("team", ""),
                    "champion": players[idx].get("championName", ""),
                    "mastery_points": 0,
                    "mastery_level": 0,
                    "error": f"exception: {e}",
                }
    return results


def summarize_mastery(stats: list[dict]) -> dict:
    """Agrège les mastery par équipe et par rôle (alignement lane-by-lane).

    Utilise le rôle du counters JSON (via compute_pregame_matchup en amont
    si on veut être strict, sinon ici on regroupe simplement par team et
    on garde l'ordre d'apparition pour le pairing).

    Retour :
      {
        'blue_total': int,
        'red_total':  int,
        'total_delta': int,             # blue_total - red_total
        'mastery_offset': float,        # signal normalisé dans [-0.25, 0.25]
        'lanes_by_role': dict[str, dict],
        'per_player': list (same as input),
        'n_resolved': int (nb joueurs avec données valides),
      }
    """
    blue = [s for s in stats if s.get("team") == "ORDER"]
    red = [s for s in stats if s.get("team") == "CHAOS"]

    blue_total = sum(s.get("mastery_points", 0) for s in blue)
    red_total = sum(s.get("mastery_points", 0) for s in red)
    total_delta = blue_total - red_total

    # Normalisation : 500k de delta global ≈ 0.25 d'offset (capping)
    # On vise [-0.25, 0.25] pour combiner naturellement avec le counter signal
    raw = total_delta / 1_000_000.0
    mastery_offset = max(-0.25, min(0.25, raw))

    n_resolved = sum(1 for s in stats if s.get("error") is None)

    return {
        "blue_total": blue_total,
        "red_total": red_total,
        "total_delta": total_delta,
        "mastery_offset": mastery_offset,
        "per_player": stats,
        "n_resolved": n_resolved,
        "n_total": len(stats),
    }
