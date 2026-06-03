"""
Scraper OP.GG — récupère les counters de tous les champions LoL.

Usage:
    python src/counters/scrape_opgg.py                       # tous les champions
    python src/counters/scrape_opgg.py --limit 3             # 3 premiers (test)
    python src/counters/scrape_opgg.py --only Syndra Yasuo   # liste explicite

Sortie : data/counters/counters_<patch>.json

Rythme : ~1 req/s pour rester poli envers op.gg (~3 min pour ~170 champs).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT_DIR / "data" / "counters"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPIONS = "https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json"
OPGG_BUILD_URL = "https://op.gg/lol/champions/{slug}/build"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Slugs OP.GG qui diffèrent du simple lowercase+alphanum
SLUG_OVERRIDES = {
    "Nunu & Willump": "nunu",
    "Renata Glasc": "renata",
    "Wukong": "monkeyking",  # op.gg utilise l'ID interne Riot "MonkeyKing"
}


def slugify(champion_name: str) -> str:
    if champion_name in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[champion_name]
    return re.sub(r"[^a-z0-9]", "", champion_name.lower())


def fetch_champion_list() -> tuple[str, list[str]]:
    """Retourne (patch, [display_name, ...]) depuis Data Dragon."""
    versions = requests.get(DDRAGON_VERSIONS, timeout=10).json()
    latest = versions[0]
    data = requests.get(DDRAGON_CHAMPIONS.format(ver=latest), timeout=10).json()
    names = sorted(c["name"] for c in data["data"].values())
    return latest, names


def _parse_counter_list(ul) -> list[dict]:
    """Extrait les entrées <li><a> d'un <ul> de counters."""
    out = []
    for li in ul.find_all("li"):
        a = li.find("a")
        if not a:
            continue
        href = a.get("href", "")
        slug = (parse_qs(urlparse(href).query).get("target_champion") or [""])[0]

        img = a.find("img")
        name = img.get("alt", "").strip() if img else ""

        strong = a.find("strong")
        wr = None
        if strong:
            m = re.search(r"([\d.]+)", strong.get_text(strip=True))
            if m:
                wr = float(m.group(1))

        n_games = None
        for span in a.find_all("span"):
            txt = span.get_text(strip=True)
            if txt and re.fullmatch(r"[\d,]+", txt):
                n_games = int(txt.replace(",", ""))
                break

        if name and wr is not None and slug:
            out.append({
                "champion": name,
                "slug": slug,
                "wr_against": wr,
                "n_games": n_games or 0,
            })
    return out


def parse_counter_section(soup: BeautifulSoup, label: str) -> list[dict]:
    """Trouve la section 'Weak against' / 'Strong against' et parse ses entrées.

    Structure ciblée :
        <div class="border-b ..."><div>Weak against</div></div>   ← container
        <div class="flex ..."><ul><li>...counter...</li></ul></div>  ← sibling direct
    On utilise find_next_sibling pour rester strictement local : 'Weak against' et
    'Strong against' apparaissent plusieurs fois dans la page (autres widgets),
    find_next() global ramène le mauvais <ul>.
    """
    for label_div in soup.find_all("div", string=lambda s: s and s.strip() == label):
        container = label_div.find_parent("div")
        if not container:
            continue
        sibling = container.find_next_sibling("div")
        if not sibling:
            continue
        ul = sibling.find("ul")
        if not ul:
            continue
        items = _parse_counter_list(ul)
        if items:
            return items
    return []


def extract_role(soup: BeautifulSoup) -> str | None:
    """Détecte le role principal via le 1er href '/counters/{role}' trouvé."""
    for a in soup.find_all("a", href=True):
        m = re.search(r"/counters/(\w+)", a["href"])
        if m:
            return m.group(1)
    return None


def scrape_champion(name: str, session: requests.Session) -> dict:
    slug = slugify(name)
    url = OPGG_BUILD_URL.format(slug=slug)
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
    except requests.exceptions.RequestException as e:
        return {"error": f"network: {e}", "url": url, "slug": slug}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "url": url, "slug": slug}

    soup = BeautifulSoup(r.text, "html.parser")
    weak = parse_counter_section(soup, "Weak against")
    strong = parse_counter_section(soup, "Strong against")
    role = extract_role(soup)

    if not weak and not strong:
        return {"error": "no counters section found", "url": url, "slug": slug}

    return {
        "champion": name,
        "slug": slug,
        "role": role,
        "weak_against": weak,
        "strong_against": strong,
    }


def main(limit: int | None, only: list[str] | None, sleep: float) -> None:
    print("→ Récupération de la liste des champions (Data Dragon)…")
    patch, champs = fetch_champion_list()
    print(f"  Patch courant : {patch}")
    print(f"  Champions totaux : {len(champs)}")

    if only:
        only_lower = [o.lower() for o in only]
        champs = [c for c in champs if c.lower() in only_lower]
        print(f"  Filtre --only : {champs}")
    elif limit:
        champs = champs[:limit]
        print(f"  Filtre --limit {limit} : {champs}")

    session = requests.Session()
    results: dict = {}
    errors: dict = {}

    for name in tqdm(champs, desc="Scraping op.gg", unit="champ"):
        data = scrape_champion(name, session)
        if "error" in data:
            errors[name] = data
        else:
            results[name] = data
        time.sleep(sleep)

    patch_slug = patch.rsplit(".", 1)[0].replace(".", "_")  # "16.10.1" → "16_10"
    out_file = OUTPUT_DIR / f"counters_{patch_slug}.json"
    payload = {
        "patch": patch,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "op.gg/build",
        "n_champions": len(results),
        "n_errors": len(errors),
        "champions": results,
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n✅ {len(results)}/{len(champs)} champions OK → {out_file}")
    if errors:
        print(f"⚠️  Échecs ({len(errors)}) :")
        for name, info in errors.items():
            print(f"    - {name} [{info.get('slug')}]: {info.get('error')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape les counters OP.GG")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limite aux N premiers champions (alphabétique)")
    parser.add_argument("--only", nargs="+", default=None,
                        help="Liste explicite (ex: --only Syndra Yasuo Kai'Sa)")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Délai entre requêtes en secondes (défaut 1.0)")
    args = parser.parse_args()
    main(args.limit, args.only, args.sleep)
