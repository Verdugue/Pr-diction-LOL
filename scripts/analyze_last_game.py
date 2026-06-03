"""Analyse rapide d'un game log JSONL. Usage: python scripts/analyze_last_game.py [path]"""
import json
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "data" / "live_games"

if len(sys.argv) > 1:
    paths = [Path(sys.argv[1])]
else:
    paths = sorted(LOG_DIR.glob("game_*.jsonl"), key=lambda p: p.stat().st_mtime)[-2:]


def load(path: Path):
    snaps = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))
    return snaps


def fmt_pct(p):
    return f"{p*100:5.1f}%"


for p in paths:
    snaps = load(p)
    if not snaps:
        print(f"\n=== {p.name} (VIDE) ===")
        continue

    first, last = snaps[0], snaps[-1]
    dur = last["game_time_minutes"] - first["game_time_minutes"]
    probas = [s["blue_win_probability"] for s in snaps]
    times = [s["game_time_minutes"] for s in snaps]
    f_first = first["features"]
    f_last = last["features"]

    print(f"\n{'='*70}")
    print(f"FICHIER : {p.name}")
    print(f"{'='*70}")
    print(f"Snapshots          : {len(snaps)}")
    print(f"Game time          : {first['game_time_minutes']:.1f}min -> {last['game_time_minutes']:.1f}min (delta {dur:.1f}min)")
    print(f"Proba BLUE init    : {fmt_pct(probas[0])}")
    print(f"Proba BLUE finale  : {fmt_pct(probas[-1])}")
    print(f"Proba min / max    : {fmt_pct(min(probas))} / {fmt_pct(max(probas))}")
    print()
    print("FEATURES FINALES (diff BLUE - RED):")
    keys = [
        "kills_diff", "deaths_diff", "cs_diff", "gold_diff", "level_diff",
        "towers_diff", "dragons_diff", "barons_diff", "heralds_diff",
        "void_grubs_diff", "dragon_soul_blue", "dragon_soul_red",
        "elder_active_blue", "elder_active_red",
        "first_blood_blue", "first_tower_blue",
    ]
    for k in keys:
        if k in f_last:
            v = f_last[k]
            print(f"  {k:25s} {v:+.2f}" if isinstance(v, float) else f"  {k:25s} {v:+d}")

    print()
    print("EVOLUTION PROBA (1 ligne / 2min):")
    last_t = -10
    for t, pr in zip(times, probas):
        if t - last_t >= 2.0:
            bar_len = int(pr * 40)
            bar = "#" * bar_len + "-" * (40 - bar_len)
            print(f"  t={t:5.1f}min  [{bar}] {fmt_pct(pr)}")
            last_t = t
    # toujours print le dernier
    if times and times[-1] - last_t > 0.1:
        pr = probas[-1]
        bar_len = int(pr * 40)
        bar = "#" * bar_len + "-" * (40 - bar_len)
        print(f"  t={times[-1]:5.1f}min  [{bar}] {fmt_pct(pr)}  (FIN)")

    # Events clefs : grosses variations entre snapshots consecutifs
    print()
    print("PLUS GROS SAUTS DE PROBA (>= 3pp):")
    jumps = []
    for i in range(1, len(snaps)):
        d = probas[i] - probas[i-1]
        if abs(d) >= 0.03:
            jumps.append((times[i], d, snaps[i-1]["features"], snaps[i]["features"]))
    jumps.sort(key=lambda x: -abs(x[1]))
    for t, d, fa, fb in jumps[:8]:
        # diff des features intéressantes
        deltas = []
        for k in ["kills_diff", "towers_diff", "dragons_diff", "barons_diff", "heralds_diff", "void_grubs_diff"]:
            if k in fa and k in fb and fa[k] != fb[k]:
                deltas.append(f"{k}:{fa[k]:+d}->{fb[k]:+d}")
        delta_str = ", ".join(deltas) if deltas else "(pas de changement event majeur)"
        sign = "+" if d > 0 else ""
        print(f"  t={t:5.1f}min  {sign}{d*100:+.1f}pp  ({delta_str})")
