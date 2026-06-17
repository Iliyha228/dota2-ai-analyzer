import os
import requests
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import urlencode, parse_qs, urlparse
from datetime import datetime

from opendota import get_match_data, parse_match, get_hero_info
from llm import analyze_match, analyze_player, ask_question

app = FastAPI()

SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey123")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ----------------- Ключ Steam API (вставьте свой, если есть) -----------------
STEAM_API_KEY = "352B0B0562B9B80E4EEB8B0FBC18E412"   # ← замените на реальный ключ

# ----------------- Steam OpenID -----------------
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"

def steam_openid_redirect(return_to: str) -> str:
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": return_to.rsplit("/", 1)[0],
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return f"{STEAM_OPENID_URL}?{urlencode(params)}"

def validate_steam_callback(request_url: str) -> str | None:
    parsed = urlparse(request_url)
    query = parse_qs(parsed.query)
    if query.get("openid.mode") != ["id_res"]:
        return None
    verify_params = {}
    for k, v in query.items():
        if k.startswith("openid."):
            verify_params[k] = v[0]
    verify_params["openid.mode"] = "check_authentication"
    try:
        resp = requests.post(STEAM_OPENID_URL, data=verify_params, timeout=10)
        if "is_valid:true" in resp.text:
            claimed_id = query.get("openid.claimed_id", [""])[0]
            steam_id64 = claimed_id.rsplit("/", 1)[-1]
            return steam_id64
    except Exception:
        pass
    return None

def steam_id64_to_32(steam_id64: str) -> int:
    return int(steam_id64) - 76561197960265728

def get_player_profile(account_id: int) -> dict:
    # Сначала пробуем OpenDota
    try:
        resp = requests.get(
            f"https://api.opendota.com/api/players/{account_id}",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            p = data.get("profile", {})
            if p.get("personaname"):
                return {"personaname": p["personaname"], "avatar": p.get("avatar", "")}
    except Exception:
        pass

    # Если не вышло – используем Steam API (если задан ключ)
    if STEAM_API_KEY:
        try:
            steam_id64 = account_id + 76561197960265728
            resp = requests.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
                params={"key": STEAM_API_KEY, "steamids": str(steam_id64)},
                timeout=10
            )
            if resp.status_code == 200:
                players = resp.json().get("response", {}).get("players", [])
                if players:
                    return {
                        "personaname": players[0]["personaname"],
                        "avatar": players[0].get("avatar", "")
                    }
        except Exception:
            pass

    # Заглушка
    return {"personaname": f"User_{account_id}", "avatar": ""}

# ----------------- Роли -----------------
ROLES_MAP = {
    1: "Carry",
    2: "Mid",
    3: "Offlane",
    4: "Soft Support",
    5: "Hard Support"
}

def assign_roles(players):
    radiant = [p for p in players if p["is_radiant"]]
    dire = [p for p in players if not p["is_radiant"]]

    def assign_team(team):
        need_heuristic = []
        for p in team:
            role_id = p.get("lane_role", 0)
            if role_id in ROLES_MAP:
                p["role"] = ROLES_MAP[role_id]
            else:
                need_heuristic.append(p)

        if not need_heuristic:
            return

        carry = max(need_heuristic, key=lambda x: x["last_hits"])
        carry["role"] = "Carry"
        remaining = [p for p in need_heuristic if p is not carry]

        if remaining:
            mid = max(remaining, key=lambda x: x["gold_per_min"])
            mid["role"] = "Mid"
            remaining = [p for p in remaining if p is not mid]

        if remaining:
            offlane = max(remaining, key=lambda x: x["gold_per_min"])
            offlane["role"] = "Offlane"
            remaining = [p for p in remaining if p is not offlane]

        if remaining:
            remaining.sort(key=lambda x: x["gold_per_min"], reverse=True)
            if len(remaining) >= 1:
                remaining[0]["role"] = "Support"
            if len(remaining) >= 2:
                remaining[1]["role"] = "Hard Support"

        for p in team:
            if "role" not in p:
                p["role"] = "Support"

    assign_team(radiant)
    assign_team(dire)

def calculate_mvp(players):
    return max(players, key=lambda p: (
        p["kills"] * 2 +
        p["assists"] +
        p["net_worth"] / 1000
    ))

def get_current_user(request: Request) -> dict | None:
    return request.session.get("user")

# ----------------- Маршруты -----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/analyze", response_class=HTMLResponse)
def analyze_page(request: Request, match_id: int = None):
    user = get_current_user(request)
    if not match_id:
        return templates.TemplateResponse("analyze.html", {"request": request, "user": user, "match_id": None})

    try:
        data = get_match_data(match_id)
    except Exception as e:
        return templates.TemplateResponse("analyze.html", {"request": request, "user": user, "error": str(e), "match_id": match_id})

    summary = parse_match(data)
    for p in summary["players"]:
        info = get_hero_info(p["hero_id"])
        p["hero_name"] = info["name"]
        p["hero_image"] = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{info['slug']}.png"

    assign_roles(summary["players"])
    mvp_player = calculate_mvp(summary["players"])

    radiant_win = summary["winner"] == "Radiant"
    dire_win = not radiant_win

    llm_text = analyze_match(summary)

    return templates.TemplateResponse("analyze.html", {
        "request": request,
        "user": user,
        "match_id": match_id,
        "winner": summary["winner"],
        "duration": summary["duration"],
        "players": summary["players"],
        "mvp": mvp_player["name"],
        "analysis": llm_text,
        "radiant_win": radiant_win,
        "dire_win": dire_win
    })

@app.post("/analyze_player", response_class=HTMLResponse)
def analyze_player_route(
    request: Request,
    match_id: int = Form(...),
    player_name: str = Form(...)
):
    user = get_current_user(request)
    data = get_match_data(match_id)
    summary = parse_match(data)

    for p in summary["players"]:
        info = get_hero_info(p["hero_id"])
        p["hero_name"] = info["name"]
        p["hero_image"] = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{info['slug']}.png"

    assign_roles(summary["players"])

    mvp_player = calculate_mvp(summary["players"])
    radiant_win = summary["winner"] == "Radiant"
    dire_win = not radiant_win
    llm_text = analyze_match(summary)

    player_analysis = analyze_player(summary, player_name)

    return templates.TemplateResponse("analyze.html", {
        "request": request,
        "user": user,
        "match_id": match_id,
        "winner": summary["winner"],
        "duration": summary["duration"],
        "players": summary["players"],
        "mvp": mvp_player["name"],
        "analysis": llm_text,
        "radiant_win": radiant_win,
        "dire_win": dire_win,
        "player_analysis": player_analysis
    })

@app.post("/ask_question", response_class=HTMLResponse)
def ask_question_route(
    request: Request,
    match_id: int = Form(...),
    user_question: str = Form(...)
):
    user = get_current_user(request)
    data = get_match_data(match_id)
    summary = parse_match(data)

    for p in summary["players"]:
        info = get_hero_info(p["hero_id"])
        p["hero_name"] = info["name"]
        p["hero_image"] = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{info['slug']}.png"

    assign_roles(summary["players"])

    mvp_player = calculate_mvp(summary["players"])
    radiant_win = summary["winner"] == "Radiant"
    dire_win = not radiant_win
    llm_text = analyze_match(summary)

    answer = ask_question(summary, user_question)

    return templates.TemplateResponse("analyze.html", {
        "request": request,
        "user": user,
        "match_id": match_id,
        "winner": summary["winner"],
        "duration": summary["duration"],
        "players": summary["players"],
        "mvp": mvp_player["name"],
        "analysis": llm_text,
        "radiant_win": radiant_win,
        "dire_win": dire_win,
        "user_question": user_question,
        "question_answer": answer
    })

# ----------------- Steam авторизация -----------------
@app.get("/auth/login")
def steam_login(request: Request):
    return_to = str(request.base_url).rstrip("/") + "/auth/callback"
    return RedirectResponse(steam_openid_redirect(return_to))

@app.get("/auth/callback")
def steam_callback(request: Request):
    full_url = str(request.url)
    steam_id64 = validate_steam_callback(full_url)
    if not steam_id64:
        raise HTTPException(status_code=400, detail="Auth failed")
    account_id = steam_id64_to_32(steam_id64)
    profile = get_player_profile(account_id)

    request.session["steam_id64"] = steam_id64
    request.session["user"] = {
        "name": profile["personaname"],
        "avatar": profile["avatar"],
        "steam_id64": steam_id64,
        "account_id": account_id
    }
    return RedirectResponse("/profile")

@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

# ----------------- Личный кабинет (с вкладками и честной статистикой) -----------------
@app.get("/profile", response_class=HTMLResponse)
# ----------------- Личный кабинет (с вкладками и честной статистикой) -----------------
@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")

    account_id = user["account_id"]

    # ---------- История матчей (последние 100) ----------
    matches = []
    try:
        resp_matches = requests.get(
            f"https://api.opendota.com/api/players/{account_id}/matches",
            params={"limit": 100}
        )
        if resp_matches.status_code == 200:
            raw_matches = resp_matches.json()
            for m in raw_matches:
                hero_info = get_hero_info(m["hero_id"])
                hero_name = hero_info["name"]
                hero_image = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{hero_info['slug']}.png"

                player_win = m.get("win")
                if player_win is None:
                    player_slot = m.get("player_slot", 0)
                    is_radiant = player_slot < 5
                    radiant_win = m.get("radiant_win", False)
                    player_win = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)

                duration_sec = m.get("duration", 0)
                mins = duration_sec // 60
                secs = duration_sec % 60
                duration_str = f"{mins}:{secs:02d}"

                start_time = m.get("start_time", 0)
                match_date = datetime.utcfromtimestamp(start_time).strftime("%d.%m.%Y %H:%M")

                game_mode = m.get("game_mode", 0)
                game_mode_names = {
                    1: "All Pick", 2: "Captain's Mode", 3: "Random Draft", 4: "Single Draft",
                    5: "All Random", 7: "The Diretide", 16: "Ability Draft", 18: "Turbo",
                    22: "Ranked All Pick", 23: "Ranked Random Draft",
                }
                game_type = game_mode_names.get(game_mode, f"Mode {game_mode}")

                matches.append({
                    "match_id": m["match_id"],
                    "hero_name": hero_name,
                    "hero_image": hero_image,
                    "kills": m["kills"],
                    "deaths": m["deaths"],
                    "assists": m["assists"],
                    "win": player_win,
                    "duration": duration_str,
                    "start_time_str": match_date,
                    "game_type": game_type,
                    "hero_id": m["hero_id"]
                })
    except Exception:
        matches = []

    # ---------- Полная статистика профиля (из OpenDota) ----------
    profile_total = None
    try:
        resp_profile = requests.get(
            f"https://api.opendota.com/api/players/{account_id}",
            timeout=15
        )
        if resp_profile.status_code == 200:
            data = resp_profile.json()
            profile = data.get("profile", {})
            total_games = data.get("total_games", 0)
            win_count = data.get("win_count", 0)
            loss_count = total_games - win_count
            winrate = round(win_count / total_games * 100, 1) if total_games > 0 else 0
            mmr_estimate = profile.get("mmr_estimate", {}).get("estimate")
            profile_total = {
                "total_games": total_games,
                "winrate": winrate,
                "win_count": win_count,
                "loss_count": loss_count,
                "mmr_estimate": mmr_estimate
            }
    except Exception:
        profile_total = None

    # ---------- Статистика за последние 100 игр (на основе matches) ----------
    last100 = None
    if matches:
        total_100 = len(matches)
        win_100 = sum(1 for m in matches if m["win"])
        loss_100 = total_100 - win_100
        wr_100 = round(win_100 / total_100 * 100, 1) if total_100 > 0 else 0

        # Герои за эти 100 игр
        hero_stats = {}
        for m in matches:
            hid = m["hero_id"]
            if hid not in hero_stats:
                hero_stats[hid] = {"games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0}
            hero_stats[hid]["games"] += 1
            if m["win"]:
                hero_stats[hid]["wins"] += 1
            hero_stats[hid]["kills"] += m["kills"]
            hero_stats[hid]["deaths"] += m["deaths"]
            hero_stats[hid]["assists"] += m["assists"]

        last100_heroes = []
        for hid, stats in hero_stats.items():
            hero_info = get_hero_info(hid)
            games = stats["games"]
            wins = stats["wins"]
            wr = round(wins / games * 100, 1) if games > 0 else 0
            kills = stats["kills"]
            deaths = stats["deaths"]
            assists = stats["assists"]
            kda_val = round((kills + assists) / max(deaths, 1), 1)
            last100_heroes.append({
                "name": hero_info["name"],
                "image": f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{hero_info['slug']}.png",
                "games": games,
                "winrate": wr,
                "kda": kda_val,
            })
        last100_heroes.sort(key=lambda x: x["games"], reverse=True)

        last100 = {
            "total_games": total_100,
            "winrate": wr_100,
            "win_count": win_100,
            "loss_count": loss_100,
            "heroes": last100_heroes
        }

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "matches": matches,
        "profile_total": profile_total,
        "last100": last100
    })