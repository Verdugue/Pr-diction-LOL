"""
Lookup runtime des counters depuis le JSON scrapé par scrape_opgg.py.

Le JSON le plus récent dans data/counters/ est chargé une seule fois en mémoire
(cache module-level). Utilisé par l'app live pour calculer un matchup_score
par lane lors du début de partie.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
COUNTERS_DIR = ROOT_DIR / "data" / "counters"

# Cache module-level : {champion_name → {weak_against: [...], strong_against: [...]}}
_CACHE: dict | None = None
_CACHE_PATH: Path | None = None


def _norm(name: str) -> str:
    """Normalise un nom de champion pour comparaison robuste (lowercase + alphanum)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_latest_counters_file() -> Path | None:
    if not COUNTERS_DIR.exists():
        return None
    files = sorted(COUNTERS_DIR.glob("counters_*.json"), reverse=True)
    return files[0] if files else None


def load_counters(path: Path | None = None, force_reload: bool = False) -> dict:
    """Charge le JSON counters le plus récent (ou path explicite).

    Retourne le dict 'champions' du JSON, indexé par nom normalisé pour lookup rapide.
    """
    global _CACHE, _CACHE_PATH

    if path is None:
        path = _find_latest_counters_file()
    if path is None:
        return {}

    if _CACHE is not None and _CACHE_PATH == path and not force_reload:
        return _CACHE

    raw = json.loads(path.read_text(encoding="utf-8"))
    champions = raw.get("champions", {})
    # Re-index par nom normalisé pour absorber les variations (Kai'Sa vs KaiSa vs kaisa)
    indexed = {_norm(name): data for name, data in champions.items()}
    _CACHE = indexed
    _CACHE_PATH = path
    return _CACHE


def get_matchup_score(champion_a: str, champion_b: str) -> float | None:
    """Retourne le WR de A contre B (entre 0 et 100), ou None si pas de data.

    Cherche dans :
      1. weak_against de A (A perd contre B)
      2. strong_against de A (A gagne contre B)
      3. À défaut, inverse : strong_against / weak_against de B → 100 - wr
    """
    db = load_counters()
    if not db:
        return None

    a_key = _norm(champion_a)
    b_key = _norm(champion_b)

    a_data = db.get(a_key)
    if a_data:
        for entry in a_data.get("weak_against", []) + a_data.get("strong_against", []):
            if _norm(entry["champion"]) == b_key:
                return float(entry["wr_against"])

    # Inverse : si B a A dans ses counters, on inverse le WR
    b_data = db.get(b_key)
    if b_data:
        for entry in b_data.get("weak_against", []) + b_data.get("strong_against", []):
            if _norm(entry["champion"]) == a_key:
                return 100.0 - float(entry["wr_against"])

    return None


def get_matchup_data(champion_a: str, champion_b: str) -> dict | None:
    """Retourne {'wr': float, 'n_games': int} du WR de A contre B, ou None si inconnu.

    Même logique de fallback que get_matchup_score : cherche dans A puis dans B (inversé).
    """
    db = load_counters()
    if not db:
        return None

    a_key, b_key = _norm(champion_a), _norm(champion_b)

    a_data = db.get(a_key)
    if a_data:
        for entry in a_data.get("weak_against", []) + a_data.get("strong_against", []):
            if _norm(entry["champion"]) == b_key:
                return {"wr": float(entry["wr_against"]), "n_games": int(entry["n_games"])}

    b_data = db.get(b_key)
    if b_data:
        for entry in b_data.get("weak_against", []) + b_data.get("strong_against", []):
            if _norm(entry["champion"]) == a_key:
                return {"wr": 100.0 - float(entry["wr_against"]), "n_games": int(entry["n_games"])}

    return None


def get_champion_data(champion: str) -> dict | None:
    """Retourne le dict complet d'un champion (role, weak_against, strong_against) ou None."""
    db = load_counters()
    return db.get(_norm(champion))


LANE_ORDER = ["top", "jungle", "mid", "adc", "support"]

# Poids par rôle : reflect l'impact moyen sur l'issue de la partie (5v5).
# Jungle et mid ont un impact global (roaming, vision, dragon control).
# Support est fort en engage mais peu de carry solo.
LANE_WEIGHTS: dict[str, float] = {
    "jungle":  0.28,
    "mid":     0.26,
    "top":     0.20,
    "adc":     0.16,
    "support": 0.10,
}

_CONF_REF = 5_000  # n_games à partir duquel la confiance est considérée saturée


def _confidence(n_games: int) -> float:
    """Score de confiance logarithmique dans [0, 1].
    Un matchup à 500 games → ~0.65, à 5000 games → 1.0.
    Évite qu'un matchup à 80 games pèse autant qu'un à 10 000.
    """
    return min(1.0, math.log1p(n_games) / math.log1p(_CONF_REF))


def _role_of(champion: str) -> str:
    """Retourne le rôle principal d'un champ depuis le counters JSON, ou 'unknown'."""
    db = load_counters()
    data = db.get(_norm(champion))
    return (data.get("role") if data else None) or "unknown"


def compute_pregame_matchup(state: dict) -> dict | None:
    """Calcule les matchups par lane à partir d'un état Live Client API.

    Stratégie : on lit le rôle principal de chaque champ depuis le counters JSON
    (qui vient du /build d'OP.GG = role le plus joué), on groupe BLUE et RED par
    rôle, puis on pair lane par lane dans l'ordre canonique top/jungle/mid/adc/support.

    Approche pairwise 5×5 abandonnée : OP.GG /build ne donne que les counters
    intra-lane, donc les paires inter-rôles (top vs adc, etc) sont quasi toujours
    None. Lane-by-lane = beaucoup plus de matchups résolus.

    Limites connues : si un joueur joue un perso hors-rôle (Lulu top alors que
    OP.GG la classe sup), la lane sera mal pairée. Acceptable pour v1.

    Retour :
        {
          'blue_champs': [str, ...],
          'red_champs':  [str, ...],
          'lanes': [
              {'role': 'top', 'blue': 'Irelia', 'red': 'Darius', 'wr_blue': 47.2},
              ...
          ],
          'avg_wr_blue': float | None,     # moyenne des wr_blue non-None
          'n_resolved':  int,
          'n_lanes':     int,              # nombre de paires formées
        }
        ou None si pas de joueurs.
    """
    players = state.get("allPlayers") or []
    blue = [p.get("championName", "") for p in players
            if p.get("team") == "ORDER" and p.get("championName")]
    red = [p.get("championName", "") for p in players
           if p.get("team") == "CHAOS" and p.get("championName")]

    if not blue or not red:
        return None

    def group_by_role(champs: list[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for c in champs:
            groups.setdefault(_role_of(c), []).append(c)
        return groups

    blue_g = group_by_role(blue)
    red_g = group_by_role(red)

    lanes: list[dict] = []
    for role in LANE_ORDER:
        bs = blue_g.get(role, [])
        rs = red_g.get(role, [])
        for b, r in zip(bs, rs):
            data        = get_matchup_data(b, r)
            wr          = data["wr"]      if data else None
            n_games     = data["n_games"] if data else None
            role_weight = LANE_WEIGHTS.get(role, 0.20)
            conf        = _confidence(n_games) if n_games else 0.0
            delta       = (wr - 50.0) / 100.0 if wr is not None else None
            lanes.append({
                "role":           role,
                "blue":           b,
                "red":            r,
                "wr_blue":        wr,
                "n_games":        n_games,
                "role_weight":    role_weight,
                "confidence":     round(conf, 3),
                # contribution nette au score final (positif = avantage blue)
                "weighted_delta": round(role_weight * conf * delta, 4) if delta is not None else None,
            })

    # ── Moyenne pondérée rôle × confiance ──────────────────────────────────────
    resolved   = [l for l in lanes if l["wr_blue"] is not None]
    total_w    = sum(l["role_weight"] * l["confidence"] for l in resolved)

    if resolved and total_w > 0:
        raw_delta    = sum(l["role_weight"] * l["confidence"] * (l["wr_blue"] - 50.0) / 100.0
                           for l in resolved) / total_w
        # Clampage : le champ-select ne peut pas dépasser ±15 pts de WR
        p_blue       = max(0.35, min(0.65, 0.5 + raw_delta))
        avg_wr_blue  = round(p_blue * 100, 2)   # rétrocompat (en %)
    else:
        raw_delta   = None
        p_blue      = None
        avg_wr_blue = None

    return {
        "blue_champs":  blue,
        "red_champs":   red,
        "lanes":        lanes,
        "avg_wr_blue":  avg_wr_blue,   # rétrocompat — maintenant pondéré
        "p_blue_champ": p_blue,        # probabilité directement utilisable [0,1]
        "n_resolved":   len(resolved),
        "n_lanes":      len(lanes),
    }


def matchup_score_team(blue_champions: list[str], red_champions: list[str]) -> dict:
    """Aggrège les matchups par lane si on a 5v5 alignés (BLUE[i] vs RED[i]).

    Retourne {
      'per_lane': [{'blue': X, 'red': Y, 'wr_blue': 52.3, 'n_games': 1024}, ...],
      'avg_wr_blue': moyenne des WR blue (None si aucun matchup trouvé),
      'n_resolved': nombre de matchups où on a trouvé une donnée,
    }

    Note : pour que ça ait du sens il faut que les listes soient alignées par lane.
    En entrée live on n'a pas toujours le rôle exact — voir caller pour l'alignement.
    """
    per_lane = []
    wrs = []
    for blue, red in zip(blue_champions, red_champions):
        wr = get_matchup_score(blue, red)
        per_lane.append({
            "blue": blue,
            "red": red,
            "wr_blue": wr,
        })
        if wr is not None:
            wrs.append(wr)

    return {
        "per_lane": per_lane,
        "avg_wr_blue": sum(wrs) / len(wrs) if wrs else None,
        "n_resolved": len(wrs),
    }


if __name__ == "__main__":
    # Petit smoke test CLI
    import sys

    if len(sys.argv) == 3:
        a, b = sys.argv[1], sys.argv[2]
        score = get_matchup_score(a, b)
        if score is None:
            print(f"Aucune donnée matchup {a} vs {b}")
        else:
            print(f"{a} vs {b} → WR de {a} = {score:.2f}%")
    else:
        db = load_counters()
        print(f"Champions chargés : {len(db)}")
        if db:
            sample = list(db.keys())[:5]
            print(f"Exemples : {sample}")
            print(f"\nUsage : python {sys.argv[0]} <champion_a> <champion_b>")
            print(f"  ex : python {sys.argv[0]} Syndra Fizz")
