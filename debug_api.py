import os, requests, certifi
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("RIOT_API_KEY")
print("Clé:", key[:12] if key else "MANQUANTE")
r = requests.get("https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/AZNVR/1966", headers={"X-Riot-Token": key}, verify=False)
print("Status:", r.status_code)
print("Body:", r.text[:300])
