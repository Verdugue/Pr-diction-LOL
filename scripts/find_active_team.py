"""Trouve l'equipe du joueur actif dans le dernier raw log."""
import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    sorted((Path(__file__).parent.parent / "data" / "live_games").glob("raw_*.jsonl"),
           key=lambda p: p.stat().st_mtime)[-1]

print(f"Lecture : {raw_path.name}")
with open(raw_path, "r", encoding="utf-8") as f:
    first_line = f.readline()

state = json.loads(first_line)
active_name = state["activePlayer"].get("riotIdGameName") or state["activePlayer"].get("summonerName")
print(f"Joueur actif : {active_name}")

all_players = state.get("allPlayers", [])
print(f"\n{len(all_players)} joueurs dans la partie :")
my_team = None
for p in all_players:
    name = p.get("riotIdGameName") or p.get("summonerName")
    team = p.get("team")
    champ = p.get("championName")
    role = p.get("position", "?")
    marker = "  >>> TOI <<<" if name == active_name else ""
    print(f"  [{team:5s}] {champ:15s} ({role:8s}) {name}{marker}")
    if name == active_name:
        my_team = team

print(f"\n==> TU ES DANS L'EQUIPE : {my_team}")
if my_team == "ORDER":
    print("    (= BLUE = cote bas-gauche de la carte = celle dont la proba est affichee)")
elif my_team == "CHAOS":
    print("    (= RED = cote haut-droit de la carte)")
    print("    Donc TA proba de victoire = 100% - proba_affichee")
