import requests

HEROES = {}

def load_heroes():
    global HEROES
    data = requests.get("https://api.opendota.com/api/heroes").json()

    for h in data:
        HEROES[h["id"]] = h["name"].replace("npc_dota_hero_", "")


def get_hero_image(hero_id):
    name = HEROES.get(hero_id, "unknown")
    return f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{name}.png"