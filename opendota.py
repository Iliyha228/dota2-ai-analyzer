import requests
import time

_heroes_cache = None

def _load_heroes():
    global _heroes_cache
    if _heroes_cache is None:
        url = "https://api.opendota.com/api/heroes"
        headers = {"User-Agent": "Mozilla/5.0"}
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    heroes = resp.json()
                    _heroes_cache = {}
                    for h in heroes:
                        hero_id = h["id"]
                        localized_name = h["localized_name"]
                        hero_slug = h["name"].replace("npc_dota_hero_", "")
                        _heroes_cache[hero_id] = {
                            "name": localized_name,
                            "slug": hero_slug
                        }
                    return _heroes_cache
                else:
                    time.sleep(1)
            except Exception:
                time.sleep(1)
        _heroes_cache = {}
    return _heroes_cache

def get_hero_info(hero_id: int) -> dict:
    heroes = _load_heroes()
    return heroes.get(hero_id, {"name": f"Hero {hero_id}", "slug": f"hero_{hero_id}"})

def get_match_data(match_id: int, retries=3, timeout=30):
    url = f"https://api.opendota.com/api/matches/{match_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    last_exception = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if "players" in data:
                    return data
                else:
                    raise Exception("Матч не найден или ещё не разобран")
            elif r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            else:
                raise Exception(f"OpenDota вернула статус {r.status_code}")
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < retries - 1:
                time.sleep(1)
    raise Exception(f"Ошибка запроса к OpenDota после {retries} попыток: {last_exception}")

def parse_match(data: dict):
    winner = "Radiant" if data.get("radiant_win") else "Dire"
    duration = round(data.get("duration", 0) / 60, 1)

    players = []
    for p in data["players"]:
        players.append({
            "name": p.get("personaname", "Anonymous"),
            "hero_id": p["hero_id"],
            "level": p.get("level", 0),
            "kills": p["kills"],
            "deaths": p["deaths"],
            "assists": p["assists"],
            "net_worth": p.get("net_worth", 0),
            "hero_damage": p.get("hero_damage", 0),
            "hero_healing": p.get("hero_healing", 0),
            "tower_damage": p.get("tower_damage", 0),
            "gold_per_min": p.get("gold_per_min", 0),
            "last_hits": p.get("last_hits", 0),
            "is_radiant": p["isRadiant"],
            "lane_role": p.get("lane_role", 0),
        })
    return {
        "winner": winner,
        "duration": duration,
        "players": players
    }