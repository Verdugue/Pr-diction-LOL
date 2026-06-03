"""Parse le fichier raw_*.jsonl qui contient des objets JSON concaténés
(les newlines dans le JSON brut de la Live API ont cassé le format JSONL strict).
Usage: python scripts/parse_raw_dump.py <file>
"""
import json
import sys
from pathlib import Path


def iter_snapshots(text: str):
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        obj, end = decoder.raw_decode(text, i)
        yield obj
        i = end


def main():
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8", errors="replace")
    snapshots = list(iter_snapshots(text))
    print(f"Total snapshots: {len(snapshots)}")
    if not snapshots:
        return

    first = snapshots[0]
    last = snapshots[-1]
    print(f"First: ts={first['ts']} gameTime={first['data'].get('gameData', {}).get('gameTime')}")
    print(f"Last : ts={last['ts']}  gameTime={last['data'].get('gameData', {}).get('gameTime')}")

    # Events accumulés (le dernier snapshot contient TOUS les events depuis le début)
    last_events = (last["data"].get("events") or {}).get("Events", [])
    print(f"\nTotal events in last snapshot: {len(last_events)}")

    from collections import Counter
    event_types = Counter(e.get("EventName", "?") for e in last_events)
    print("\nEvent type counts:")
    for et, c in event_types.most_common():
        print(f"  {et:25s} {c}")

    # Focus tours
    print("\n=== TurretKilled events ===")
    turret_events = [e for e in last_events if e.get("EventName") == "TurretKilled"]
    for e in turret_events:
        print(json.dumps(e, indent=2, ensure_ascii=False))

    print("\n=== InhibKilled events ===")
    for e in last_events:
        if e.get("EventName") == "InhibKilled":
            print(json.dumps(e, indent=2, ensure_ascii=False))

    # Sample autres events
    print("\n=== ChampionKill (1er) ===")
    for e in last_events:
        if e.get("EventName") == "ChampionKill":
            print(json.dumps(e, indent=2, ensure_ascii=False))
            break

    print("\n=== DragonKill (1er) ===")
    for e in last_events:
        if e.get("EventName") == "DragonKill":
            print(json.dumps(e, indent=2, ensure_ascii=False))
            break


if __name__ == "__main__":
    main()
