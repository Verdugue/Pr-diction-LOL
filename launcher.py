"""
Launcher — daemon de fond qui surveille la Live Client API de LoL
et lance / arrête automatiquement l'app Streamlit.

Usage :
    python launcher.py
    (ou double-clic sur start_predictor.bat)

Comportement :
    - Poll 127.0.0.1:2999 toutes les 5 secondes
    - Quand une partie est détectée → lance `streamlit run app/main.py`,
      ouvre http://localhost:8501 dans le navigateur par défaut
    - Quand la partie est terminée (30s sans réponse) → tue le process Streamlit
    - Reste en attente de la prochaine partie
    - Ctrl+C pour quitter
"""

import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import requests
import urllib3

# Force UTF-8 sur stdout pour supporter les emojis dans les logs (Windows cp1252 par défaut)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
POLL_INTERVAL = 5          # secondes entre chaque check
ENDED_GRACE_SECONDS = 30   # délai sans réponse avant de considérer la partie finie
STREAMLIT_PORT = 8501

ROOT_DIR = Path(__file__).parent.resolve()
STREAMLIT_APP = ROOT_DIR / "app" / "main.py"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def is_game_running() -> bool:
    try:
        r = requests.get(LIVE_URL, verify=False, timeout=2)
        return r.status_code == 200
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False
    except requests.exceptions.RequestException:
        return False


def spawn_streamlit() -> subprocess.Popen:
    """Lance streamlit dans une nouvelle console (Windows) ou en arrière-plan."""
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(STREAMLIT_APP),
        f"--server.port={STREAMLIT_PORT}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    if os.name == "nt":
        # Windows : ouvre dans une nouvelle fenêtre console qu'on peut suivre
        return subprocess.Popen(
            cmd, cwd=str(ROOT_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    return subprocess.Popen(cmd, cwd=str(ROOT_DIR))


def kill_streamlit(proc: subprocess.Popen) -> None:
    """Termine proprement le subprocess Streamlit."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def main() -> None:
    streamlit_proc: subprocess.Popen | None = None
    last_seen = 0.0

    log("LoL Win Predictor — Launcher")
    log("En attente d'une partie de League of Legends…")
    log("(Ctrl+C pour quitter)")

    while True:
        running = is_game_running()

        if running:
            last_seen = time.time()
            # Spawn si pas encore lancé OU si le process a crash
            if streamlit_proc is None or streamlit_proc.poll() is not None:
                log("🎮 Partie détectée → lancement de Streamlit")
                streamlit_proc = spawn_streamlit()
                # Petit délai pour laisser Streamlit binder le port
                time.sleep(4)
                url = f"http://localhost:{STREAMLIT_PORT}"
                log(f"🌐 Ouverture du navigateur : {url}")
                webbrowser.open(url)
        else:
            # Pas de partie : si on a un streamlit actif et qu'on a dépassé la grâce → kill
            if streamlit_proc and streamlit_proc.poll() is None:
                elapsed = time.time() - last_seen
                if elapsed > ENDED_GRACE_SECONDS:
                    log(f"🛑 Partie terminée ({elapsed:.0f}s sans réponse) → arrêt de Streamlit")
                    kill_streamlit(streamlit_proc)
                    streamlit_proc = None
                    log("⏳ En attente de la prochaine partie…")
            elif streamlit_proc and streamlit_proc.poll() is not None:
                # Streamlit s'est arrêté tout seul (crash, ou user a fermé la fenêtre)
                log("⚠️  Streamlit s'est arrêté de lui-même → reset")
                streamlit_proc = None

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêt du launcher.")
