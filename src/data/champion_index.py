"""
Mapping championName (display, ex 'Ahri') → championId numérique Riot (ex 103).

Source : Riot Data Dragon, pas besoin de clé API.
Fetch une seule fois par session (cache module-level).
"""

from __future__ import annotations

import requests

_DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
_DDRAGON_CHAMPS = "https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json"

# Cache module-level
_NAME_TO_ID: dict[str, int] | None = None
_PATCH: str | None = None


def _build_index() -> tuple[str, dict[str, int]]:
    versions = requests.get(_DDRAGON_VERSIONS, timeout=10).json()
    latest = versions[0]
    data = requests.get(_DDRAGON_CHAMPS.format(ver=latest), timeout=10).json()
    name_to_id: dict[str, int] = {}
    for entry in data["data"].values():
        name = entry.get("name", "")
        key = entry.get("key", "")
        if name and key.isdigit():
            name_to_id[name] = int(key)
    return latest, name_to_id


def _ensure_loaded() -> None:
    global _NAME_TO_ID, _PATCH
    if _NAME_TO_ID is None:
        _PATCH, _NAME_TO_ID = _build_index()


def get_champion_id(champion_name: str) -> int | None:
    """Retourne l'id numérique Riot d'un champion depuis son display name."""
    _ensure_loaded()
    if not champion_name:
        return None
    return _NAME_TO_ID.get(champion_name)


def get_patch() -> str | None:
    _ensure_loaded()
    return _PATCH


def all_champions() -> dict[str, int]:
    _ensure_loaded()
    return dict(_NAME_TO_ID)


if __name__ == "__main__":
    _ensure_loaded()
    print(f"Patch: {_PATCH}")
    print(f"Champions indexés: {len(_NAME_TO_ID)}")
    for name in ["Ahri", "Kai'Sa", "Wukong", "K'Sante", "Lee Sin"]:
        print(f"  {name:12s} → id={get_champion_id(name)}")
