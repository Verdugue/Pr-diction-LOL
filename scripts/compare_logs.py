"""Compare le log launcher (features extraites) avec le dump raw (events bruts).
Identifie pourquoi la proba ne montait pas alors que l'équipe gagnait."""
import json
import sys
from pathlib import Path


def iter_raw_snapshots(text: str):
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        obj, end = decoder.raw_decode(text, i)
        yield obj
        i = end


def main():
    launcher_path = Path("data/live_games/game_2026-05-25T19-01-57.jsonl")
    raw_path = Path("data/live_games/raw_2026-05-25T19-02-03.jsonl")

    launcher_snaps = [json.loads(l) for l in launcher_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    raw_snaps = list(iter_raw_snapshots(raw_path.read_text(encoding="utf-8", errors="replace")))

    print(f"Launcher snapshots: {len(launcher_snaps)}")
    print(f"Raw snapshots: {len(raw_snaps)}")

    # 1. Dernier état launcher
    last_l = launcher_snaps[-1]
    print(f"\n=== Dernier snapshot launcher ===")
    print(f"  game_time: {last_l['game_time_minutes']:.2f} min")
    print(f"  blue_win_probability: {last_l['blue_win_probability']}")
    feats = last_l["features"]
    print(f"  Features non-nulles:")
    for k, v in feats.items():
        if v != 0 and k != "game_time_minutes":
            print(f"    {k:25s} {v}")

    # 2. Identifier le côté du user dans le raw
    last_raw = raw_snaps[-1]
    active_name = last_raw["data"].get("activePlayer", {}).get("riotIdGameName") or \
                  last_raw["data"].get("activePlayer", {}).get("summonerName")
    players = last_raw["data"].get("allPlayers", [])
    user_team = None
    for p in players:
        name = p.get("riotIdGameName") or p.get("summonerName")
        if name == active_name:
            user_team = p.get("team")
            break
    print(f"\n=== User ===")
    print(f"  Active player: {active_name}")
    print(f"  User team: {user_team} ({'BLUE/ORDER' if user_team == 'ORDER' else 'RED/CHAOS'})")

    # 3. Compter les events par équipe (avec mapping corrigé TOrder/TChaos)
    last_events = (last_raw["data"].get("events") or {}).get("Events", [])
    player_team = {}
    for p in players:
        team = p.get("team")
        for k in ("riotIdGameName", "summonerName", "riotId"):
            v = p.get(k)
            if v and team:
                player_team[v] = team

    def team_from_turret(name):
        if "TOrder" in name: return "CHAOS"  # tour ORDER détruite par CHAOS
        if "TChaos" in name: return "ORDER"  # tour CHAOS détruite par ORDER
        if "T1" in name: return "CHAOS"  # fallback ancien
        if "T2" in name: return "ORDER"
        return None

    counts = {"ORDER": {}, "CHAOS": {}}
    for t in counts:
        counts[t] = {k: 0 for k in ["kills","towers","dragons","heralds","barons","void_grubs","inhibitors"]}

    for e in last_events:
        etype = e.get("EventName")
        killer = e.get("KillerName", "")
        if etype == "ChampionKill":
            t = player_team.get(killer)
            if t: counts[t]["kills"] += 1
        elif etype == "TurretKilled":
            t = team_from_turret(e.get("TurretKilled", ""))
            if t: counts[t]["towers"] += 1
        elif etype == "InhibKilled":
            iname = e.get("InhibKilled", "")
            if "TOrder" in iname or "T1" in iname: t = "CHAOS"
            elif "TChaos" in iname or "T2" in iname: t = "ORDER"
            else: t = None
            if t: counts[t]["inhibitors"] += 1
        elif etype == "DragonKill":
            t = player_team.get(killer)
            if t: counts[t]["dragons"] += 1
        elif etype == "HeraldKill":
            t = player_team.get(killer)
            if t: counts[t]["heralds"] += 1
        elif etype == "BaronKill":
            t = player_team.get(killer)
            if t: counts[t]["barons"] += 1
        elif etype == "HordeKill":
            t = player_team.get(killer)
            if t: counts[t]["void_grubs"] += 1

    # Stats joueurs depuis raw
    blue_kills = sum(p.get("scores",{}).get("kills",0) for p in players if p.get("team")=="ORDER" and not p.get("isBot"))
    red_kills = sum(p.get("scores",{}).get("kills",0) for p in players if p.get("team")=="CHAOS" and not p.get("isBot"))
    blue_cs = sum(p.get("scores",{}).get("creepScore",0) for p in players if p.get("team")=="ORDER" and not p.get("isBot"))
    red_cs = sum(p.get("scores",{}).get("creepScore",0) for p in players if p.get("team")=="CHAOS" and not p.get("isBot"))

    print(f"\n=== État RÉEL de la partie (raw + mapping corrigé) ===")
    print(f"  {'':25s} {'ORDER (blue)':>15s} {'CHAOS (red)':>15s} {'diff (B-R)':>12s}")
    print(f"  {'kills (scores)':25s} {blue_kills:>15d} {red_kills:>15d} {blue_kills - red_kills:>+12d}")
    print(f"  {'kills (events)':25s} {counts['ORDER']['kills']:>15d} {counts['CHAOS']['kills']:>15d} {counts['ORDER']['kills']-counts['CHAOS']['kills']:>+12d}")
    print(f"  {'cs':25s} {blue_cs:>15d} {red_cs:>15d} {blue_cs - red_cs:>+12d}")
    for k in ["towers","dragons","heralds","barons","void_grubs","inhibitors"]:
        b = counts["ORDER"][k]; r = counts["CHAOS"][k]
        print(f"  {k:25s} {b:>15d} {r:>15d} {b-r:>+12d}")

    print(f"\n=== Ce que le launcher a vu (dernier snapshot) ===")
    print(f"  kills_diff:        {feats.get('kills_diff')}")
    print(f"  cs_diff:           {feats.get('cs_diff')}")
    print(f"  towers_diff:       {feats.get('towers_diff')}  ← bug TurretKilled")
    print(f"  dragons_diff:      {feats.get('dragons_diff')}")
    print(f"  heralds_diff:      {feats.get('heralds_diff')}")
    print(f"  barons_diff:       {feats.get('barons_diff')}")
    print(f"  void_grubs_diff:   {feats.get('void_grubs_diff')}")
    print(f"  inhibitors_diff:   {feats.get('inhibitors_diff')}")
    print(f"  first_blood:       {feats.get('first_blood')}")
    print(f"  first_tower:       {feats.get('first_tower')}")

    # 4. Evolution de la proba au fil de la partie
    print(f"\n=== Évolution proba (samples) ===")
    n = len(launcher_snaps)
    for idx in [0, n//4, n//2, 3*n//4, n-1]:
        s = launcher_snaps[idx]
        print(f"  t={s['game_time_minutes']:5.1f} min  proba={s['blue_win_probability']:.3f}  "
              f"kills={s['features'].get('kills_diff')} towers={s['features'].get('towers_diff')} "
              f"dragons={s['features'].get('dragons_diff')}")


if __name__ == "__main__":
    main()
