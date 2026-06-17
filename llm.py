import requests
from items_db import init_db, get_item_name

init_db()

# ---------- Настройки Ollama ----------
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "t-tech/T-lite-it-2.1:q4_K_M"   # ← моя модель

# ---------- Системный промпт ----------
SYSTEM_PROMPT = (
    "Ты — эксперт-аналитик Dota 2. В матче ВСЕГДА ровно 10 игроков. "
    "Ты работаешь ТОЛЬКО с данными, которые перечислены ниже. "
    "Каждая строка начинается с имени игрока (или его героя) и содержит его личные показатели. "
    "Если имя игрока 'Anonymous', используй название героя для идентификации. "
    "Ты НЕ ИМЕЕШЬ ПРАВА называть героев или игроков, которых нет в этих строках. "
    "Ты НЕ ИМЕЕШЬ ПРАВА изменять или придумывать цифры. "
    "Ты не выводишь промежуточные арифметические расчёты, только итоговые значения. "
    "Ты строго соблюдаешь ролевые нормы KDA, данные в подсказке."
)

# ---------- Ролевые ожидания (с KDA‑нормами) ----------
ROLE_EXPECTATIONS = """
Роли и их эталонные KDA (формула: (K+A)/D):
- Carry (1): KDA 3.5–5.0. Главное — фармить и выживать, минимум смертей.
- Mid (2): KDA 3.0–4.5. Много убийств, но допускается больше смертей, чем у керри.
- Offlane (3): KDA 2.2–3.2. Инициатор, много помощи и смертей.
- Support (4): KDA 2.0–2.8. Много помощи, скромный собственный урон.
- Hard Support (5): KDA 1.5–2.2. Самая жертвенная роль, низкий KDA — норма.
"""

# ---------- Вызов модели ----------
def generate(prompt: str, max_tokens: int = 500, temperature: float = 0.1) -> str:
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
    except Exception as e:
        return f"[Ошибка генерации: {e}]"

# ---------- Формирование текста матча (компактная версия) ----------
def build_match_text(summary: dict) -> str:
    players_text = ""
    radiant_players = []
    dire_players = []

    for p in summary["players"]:
        d = p["deaths"]
        kda = (p["kills"] + p["assists"]) / d if d > 0 else p["kills"] + p["assists"]
        # Идентификатор: имя (если Anonymous – только герой)
        player_id = p["name"] if p["name"] != "Anonymous" else p.get("hero_name", "Неизвестный герой")
        line = (
            f"{player_id} ({p.get('hero_name', '?')}, {p.get('role', '?')}) — "
            f"Ур.{p['level']}, KDA {kda:.1f} ({p['kills']}/{p['deaths']}/{p['assists']}), "
            f"GPM {p['gold_per_min']}, урон {p['hero_damage']}, "
            f"башни {p['tower_damage']}, ценность {p['net_worth']}\n"
        )

        players_text += line
        if p["is_radiant"]:
            radiant_players.append(p)
        else:
            dire_players.append(p)

    def team_stats(players):
        return {
            "kills": sum(p["kills"] for p in players),
            "deaths": sum(p["deaths"] for p in players),
            "gpm_avg": round(sum(p["gold_per_min"] for p in players) / 5),
            "hero_dmg": sum(p["hero_damage"] for p in players),
            "tower_dmg": sum(p["tower_damage"] for p in players),
            "net_worth": sum(p["net_worth"] for p in players),
        }

    radiant_stats = team_stats(radiant_players)
    dire_stats = team_stats(dire_players)

    return f"""
Победитель: {summary['winner']} (Radiant – {'Победа' if summary['winner'] == 'Radiant' else 'Поражение'}, Dire – {'Поражение' if summary['winner'] == 'Radiant' else 'Победа'})
Длительность: {summary['duration']} минут

Командная статистика:
Radiant: Убийств {radiant_stats['kills']}, Смертей {radiant_stats['deaths']}, Средний GPM {radiant_stats['gpm_avg']}, Сумм. урон {radiant_stats['hero_dmg']}, Урон по башням {radiant_stats['tower_dmg']}, Общая ценность {radiant_stats['net_worth']}
Dire:   Убийств {dire_stats['kills']}, Смертей {dire_stats['deaths']}, Средний GPM {dire_stats['gpm_avg']}, Сумм. урон {dire_stats['hero_dmg']}, Урон по башням {dire_stats['tower_dmg']}, Общая ценность {dire_stats['net_worth']}

Игроки (всего 10):
{players_text}
"""

# ---------- Анализ матча ----------
def analyze_match(summary: dict) -> str:
    match_text = build_match_text(summary)
    prompt = f"""Ниже приведены ТОЛЬКО реальные данные матча. В матче ровно 10 игроков – по 5 в каждой команде.
У каждого игрока своя строка с показателями. Не выдумывай дополнительных героев.
Если имя игрока Anonymous, идентифицируй его по герою (указан в скобках).

{match_text}

{ROLE_EXPECTATIONS}

Жёсткие правила ответа:
- Не выводи промежуточные вычисления KDA (формулы). Указывай только итоговое число.
- MVP обязан быть из команды-победителя, с KDA не ниже нижней границы для его роли, и с высоким GPM.
- Не называй игроков или героев, которых нет в списке выше.

Ответь строго по пунктам на русском языке:
1. Почему победила команда {summary['winner']} (сравни суммарные показатели Radiant и Dire).
2. Кто MVP (конкретный игрок/герой из списка, с точными цифрами KDA, GPM, урона).
3. Основные ошибки проигравших (кто именно из списка не дотянул до KDA своей роли, у кого низкий GPM или урон)."""
    return generate(prompt)

# ---------- Анализ игрока ----------
def analyze_player(summary: dict, player_name: str) -> str:
    player = next((p for p in summary["players"] if p["name"] == player_name), None)
    if not player:
        return "Игрок не найден"

    match_text = build_match_text(summary)

    same_role = [p for p in summary["players"] if p.get("role") == player.get("role") and p["name"] != player_name]
    comparison = ""
    if same_role:
        comparison = "Сравнение с другими игроками той же роли:\n"
        for p in same_role:
            comparison += f"{p['name']} ({p.get('hero_name','?')}) – GPM {p['gold_per_min']}, KDA {p['kills']}/{p['deaths']}/{p['assists']}, урон {p['hero_damage']}, башни {p['tower_damage']}, ценность {p['net_worth']}\n"
    else:
        comparison = "Нет других игроков этой роли."

    prompt = f"""Ниже реальные данные матча. В матче ровно 10 игроков.
Если имя игрока Anonymous, используй название героя для идентификации.
Оцени игру КОНКРЕТНОГО игрока из списка, используя только его строку.

{match_text}

{ROLE_EXPECTATIONS}

Игрок для анализа (только его показатели):
Имя: {player['name']}
Герой: {player.get('hero_name', 'Неизвестно')}
Роль: {player.get('role', '?')}
Уровень: {player['level']}
KDA: {player['kills']}/{player['deaths']}/{player['assists']}
GPM: {player['gold_per_min']}
Урон: {player['hero_damage']}
Хил: {player['hero_healing']}
Башни: {player['tower_damage']}
Last hits: {player['last_hits']}
Ценность: {player['net_worth']}

{comparison}

Требования к ответу:
- Не выводи промежуточные вычисления KDA.
- Сравнивай с нормами роли, указанными выше.

Ответь по пунктам на русском языке:
1. Оценка выполнения роли (отлично/хорошо/средне/плохо) с учётом норм KDA.
2. Что хорошо.
3. Ошибки.
4. Влияние на исход матча.
5. Советы."""
    return generate(prompt)

# ---------- Ответ на вопрос ----------
def ask_question(summary: dict, question: str) -> str:
    match_text = build_match_text(summary)
    prompt = f"""Ниже реальные данные матча. В матче ровно 10 игроков.
Если имя Anonymous, идентифицируй по герою.
Используй ТОЛЬКО эти данные для ответа.

{match_text}

{ROLE_EXPECTATIONS}

Требования:
- Не выводи промежуточные вычисления KDA.
- Не изменяй цифры, не путай игроков.

Вопрос: {question}

Дай развёрнутый ответ на русском языке."""
    return generate(prompt)