from starlette import staticfiles
import asyncio
import json
import math
import os
import random
import re
import socket
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta

import aiomysql
import httpx
from fastapi import FastAPI, HTTPException, Request

from db import db_connection, init_db_tables
from steam_api import fetch_app_news, clean_news_summary
from collector import chart_collection_loop

KST_OFFSET = timedelta(hours=9)  # 한국은 DST 없이 UTC+9 고정이라 오프셋 상수로 충분함

# ---------------------------------------------------------------------------
# Mock 데이터 (Steam / Bedrock API 키가 없는 환경 — 로컬 무설정 실행, CI 헬스체크 등 — 의 폴백)
# ---------------------------------------------------------------------------

PLAYSTYLES = [
    "탐험형 협동 플레이어",
    "경쟁형 FPS 마스터",
    "몰입형 RPG 스토커",
    "샌드박스 크래프터",
    "하드코어 생존 전문가",
    "전략 시뮬레이션 지휘관",
]

INSIGHTS = [
    "전략·생존 장르를 중심으로 오래 플레이하며, 최근에는 친구와 즐기는 협동 게임 비중이 높아졌습니다.",
    "경쟁 슈팅 게임에서 높은 K/D 지표를 기록하며 최신 FPS 메타를 빠르게 파악하는 스타일입니다.",
    "스토리 중심 RPG 게임의 모든 수집 요소와 멀티 엔딩을 탐색하는 완벽주의 플레이어입니다.",
    "자유도 높은 샌드박스와 건축·자동화 시스템 구축에 많은 플레이 타임을 투자하고 있습니다.",
    "친구 네트워크와 함께 공포·협동 파티 게임을 주로 즐기며 주기적으로 신작을 탐색합니다.",
]

FRIEND_TRAITS = [
    "경쟁 FPS와 협동 공포를 오가는 하이브리드 플레이어",
    "새로운 슈팅 게임의 메타를 빠르게 탐색하는 정밀 플레이어",
    "경쟁 게임과 장기 몰입형 RPG를 함께 즐기는 집중형 플레이어",
    "커뮤니티와 함께 신작을 찾아가는 트렌드 탐색가",
    "생존 장르와 자유도 높은 샌드박스에 오래 머무는 탐험가",
]

FRIEND_POOL = [
    {"name": "Anomaly", "code": "AN", "country": "Sweden", "game": "Counter-Strike 2", "trait": FRIEND_TRAITS[0]},
    {"name": "shroud", "code": "SH", "country": "Canada", "game": "Escape from Tarkov", "trait": FRIEND_TRAITS[1]},
    {"name": "S1mple", "code": "S1", "country": "Ukraine", "game": "Dota 2", "trait": FRIEND_TRAITS[2]},
    {"name": "Ninja", "code": "NI", "country": "United States", "game": "Helldivers 2", "trait": FRIEND_TRAITS[3]},
    {"name": "Pokelawls", "code": "PL", "country": "Canada", "game": "Rust", "trait": FRIEND_TRAITS[4]},
    {"name": "Tarik", "code": "TK", "country": "United States", "game": "VALORANT", "trait": "팀 플레이와 사운드 플레이를 중시하는 전술가"},
    {"name": "LIRIK", "code": "LK", "country": "United States", "game": "DayZ", "trait": "다양한 신작 인디 게임과 오픈월드 생존을 다각도로 탐험"},
]

# ---------------------------------------------------------------------------
# 환경 설정
# ---------------------------------------------------------------------------

REQUEST_COUNTER_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS request_counter (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        pod_name VARCHAR(255) NOT NULL,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

STEAM_USER_FRIENDS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS steam_user_friends (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        owner_steam_id VARCHAR(64) NOT NULL,
        friend_steam_id VARCHAR(64) NOT NULL,
        friend_name VARCHAR(255) NOT NULL,
        avatar_url TEXT,
        country VARCHAR(10) DEFAULT 'UN',
        most_played_game VARCHAR(255),
        playtime_2weeks_minutes INT DEFAULT 0,
        total_playtime_hours INT DEFAULT 0,
        shared_games_count INT DEFAULT 0,
        trait VARCHAR(255),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_owner_friend (owner_steam_id, friend_steam_id),
        INDEX idx_owner_2weeks (owner_steam_id, playtime_2weeks_minutes DESC)
    )
"""

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_ID64_RE = re.compile(r"^\d{17}$")
STEAM_ACCOUNT_ID_RE = re.compile(r"^\d{1,10}$")
STEAM_ID64_BASE = 76561197960265728
STEAM_ACCOUNT_ID_MAX = 2**32

BEDROCK_DEFAULT_REGION = "us-east-1"
BEDROCK_DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

PERSONA_STATE_LABELS = {
    0: "offline", 1: "online", 2: "busy", 3: "away", 4: "snooze", 5: "looking_to_trade", 6: "looking_to_play"
}

def persona_state_label(summary: dict) -> str:
    return PERSONA_STATE_LABELS.get(summary.get("personastate", 0), "offline")

def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def seeded_random(seed_str: str):
    h = 0
    for ch in seed_str:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    state = {"h": h}

    def _next() -> float:
        state["h"] = (state["h"] * 1664525 + 1013904223) & 0xFFFFFFFF
        return state["h"] / 4294967296

    return _next


def estimate_concurrent(base: float, hour: int, seed: str) -> int:
    # 저녁에 오르고 새벽에 내리는 하루 주기 패턴 + 재현 가능한 약간의 노이즈.
    rand = seeded_random(seed)
    wave = 0.6 + 0.4 * math.sin(((hour - 6) / 24) * 2 * math.pi - math.pi / 2)
    noise = 0.9 + rand() * 0.2
    return max(0, round(base * wave * noise))

# ---------------------------------------------------------------------------
# DB / HTTP 클라이언트 관리
# ---------------------------------------------------------------------------

async def create_db_pool() -> aiomysql.Pool:
    return await aiomysql.create_pool(
        host=required_env("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=required_env("DB_USER"),
        password=required_env("DB_PASSWORD"),
        db=required_env("DB_NAME"),
        connect_timeout=5,
        autocommit=True,
        cursorclass=aiomysql.cursors.DictCursor,
        minsize=1,
        maxsize=10,
    )

async def get_db_pool(app: FastAPI) -> aiomysql.Pool:
    if app.state.db_pool is None:
        async with app.state.db_pool_lock:
            if app.state.db_pool is None:
                app.state.db_pool = await create_db_pool()
    return app.state.db_pool

async def get_http_client(app: FastAPI) -> httpx.AsyncClient:
    if app.state.http_client is None:
        async with app.state.http_client_lock:
            if app.state.http_client is None:
                app.state.http_client = httpx.AsyncClient(timeout=10.0)
    return app.state.http_client

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.db_pool = None
    app.state.db_pool_lock = asyncio.Lock()
    app.state.http_client = None
    app.state.http_client_lock = asyncio.Lock()

    # 게임 차트/장르 수집기(동기 pymysql 기반, db.py+collector.py)는 위 aiomysql 풀과는
    # 별개 커넥션을 쓴다 — 15분 주기 배치라 굳이 커넥션 풀을 공유할 필요가 없어 단순하게 유지.
    #
    # K8s에서는 WAS 파드가 DB 파드/RDS보다 먼저 뜨는 경우가 있어(콜드스타트 레이스),
    # 최초 시도가 실패하면 그대로 넘어가 game_chart_rankings 등 테이블이 영영 생성되지
    # 않는 문제가 있었다 — 짧게 재시도해서 이 창을 흡수한다.
    if os.getenv("DB_HOST"):
        DB_INIT_RETRIES = 5
        DB_INIT_RETRY_DELAY_SECONDS = 3
        for attempt in range(1, DB_INIT_RETRIES + 1):
            try:
                with closing(db_connection()) as connection:
                    init_db_tables(connection)
                    print("Collector DB tables (game_chart_rankings, game_info, genres, ...) initialized.")
                break
            except Exception as e:
                print(f"Startup collector DB init warning (attempt {attempt}/{DB_INIT_RETRIES}): {e}")
                if attempt < DB_INIT_RETRIES:
                    await asyncio.sleep(DB_INIT_RETRY_DELAY_SECONDS)
    asyncio.create_task(chart_collection_loop())

    yield
    if app.state.db_pool is not None:
        app.state.db_pool.close()
        await app.state.db_pool.wait_closed()
    if app.state.http_client is not None:
        await app.state.http_client.aclose()

app = FastAPI(title="Steam Insight EKS WAS", version="5.0.0-steam-bedrock", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Steam API / Bedrock 헬퍼
# ---------------------------------------------------------------------------

class SteamUserNotFoundError(Exception):
    pass

async def resolve_steam_id(client: httpx.AsyncClient, api_key: str, identifier: str) -> str:
    candidate = identifier.strip().rstrip("/").rsplit("/", 1)[-1]
    if STEAM_ID64_RE.match(candidate):
        return candidate

    if STEAM_ACCOUNT_ID_RE.match(candidate) and int(candidate) < STEAM_ACCOUNT_ID_MAX:
        account_id_candidate = str(STEAM_ID64_BASE + int(candidate))
        try:
            await fetch_player_summary(client, api_key, account_id_candidate)
            return account_id_candidate
        except (SteamUserNotFoundError, httpx.HTTPError):
            pass

    response = await client.get(
        f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
        params={"key": api_key, "vanityurl": candidate},
    )
    response.raise_for_status()
    payload = response.json().get("response", {})
    if payload.get("success") != 1:
        raise SteamUserNotFoundError(f"Steam 프로필을 찾을 수 없습니다: {identifier}")
    return payload["steamid"]

async def fetch_player_summary(client: httpx.AsyncClient, api_key: str, steamid: str) -> dict:
    response = await client.get(
        f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
        params={"key": api_key, "steamids": steamid},
    )
    response.raise_for_status()
    players = response.json().get("response", {}).get("players", [])
    if not players:
        raise SteamUserNotFoundError(f"Steam 프로필 정보를 가져올 수 없습니다: {steamid}")
    return players[0]

async def fetch_owned_games(client: httpx.AsyncClient, api_key: str, steamid: str) -> list[dict]:
    response = await client.get(
        f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
        params={"key": api_key, "steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1},
    )
    response.raise_for_status()
    return response.json().get("response", {}).get("games", [])

async def fetch_friend_list(client: httpx.AsyncClient, api_key: str, steamid: str) -> list[dict]:
    try:
        response = await client.get(
            f"{STEAM_API_BASE}/ISteamUser/GetFriendList/v1/",
            params={"key": api_key, "steamid": steamid, "relationship": "friend"},
        )
        response.raise_for_status()
        return response.json().get("friendslist", {}).get("friends", [])
    except (httpx.HTTPError, ValueError):
        return []

async def fetch_game_achievements(
    client: httpx.AsyncClient, api_key: str, steamid: str, appid: int | None
) -> dict | None:
    if not appid:
        return None
    try:
        response = await client.get(
            f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/",
            params={"key": api_key, "steamid": steamid, "appid": appid},
        )
        data = response.json().get("playerstats", {})
    except (httpx.HTTPError, ValueError):
        return None
    if not data.get("success"):
        return None
    achievements = data.get("achievements", [])
    if not achievements:
        return None
    achieved = sum(1 for item in achievements if item.get("achieved"))
    return {"achieved": achieved, "total": len(achievements)}

def minutes_to_hours_label(minutes: float) -> str:
    return f"{round(minutes / 60):,}h"

def build_game_entries(games: list[dict], limit: int = 5) -> list[dict]:
    ranked = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
    entries = []
    for g in ranked[:limit]:
        appid = g.get("appid")
        entries.append({
            "appid": appid,
            "name": g.get("name", "Unknown"),
            "hours": round(g.get("playtime_forever", 0) / 60, 1),
            "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg" if appid else None,
            "storeUrl": f"https://store.steampowered.com/app/{appid}" if appid else None,
        })
    return entries

# ---------------------------------------------------------------------------
# 실측 친구 데이터 수집 헬퍼
# ---------------------------------------------------------------------------

async def get_user_owned_appids(client: httpx.AsyncClient, steam_api_key: str, steam_id: str) -> set:
    if not steam_api_key or not steam_id:
        return set()
    try:
        url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
        res = await client.get(url, params={"key": steam_api_key, "steamid": steam_id}, timeout=4.0)
        games = res.json().get("response", {}).get("games", [])
        return {g["appid"] for g in games if "appid" in g}
    except Exception:
        return set()

async def fetch_friend_real_stats(client: httpx.AsyncClient, steam_api_key: str, friend_steam_id: str, owner_appids: set, sem: asyncio.Semaphore) -> dict:
    stats = {
        "twoWeeks": "0h", "twoWeeks_minutes": 0,
        "total": "0h", "total_hours": 0,
        "shared": "0개", "shared_count": 0, "shared_games": [],
        "recent_games_list": [], "game": "Steam Game", "achievement": "비공개"
    }
    if not steam_api_key or not friend_steam_id:
        return stats

    async with sem:
        recent_games = []
        for attempt in range(2):
            try:
                recent_url = f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/?key={steam_api_key}&steamid={friend_steam_id}&count=5"
                res_rec = await client.get(recent_url, timeout=4.0)
                if res_rec.status_code == 200:
                    recent_games = res_rec.json().get("response", {}).get("games", [])
                    break
            except Exception:
                await asyncio.sleep(0.1)

        if recent_games:
            total_2w_min = sum(g.get("playtime_2weeks", 0) for g in recent_games)
            stats["twoWeeks_minutes"] = total_2w_min
            stats["twoWeeks"] = f"{round(total_2w_min / 60, 1)}h"
            stats["game"] = recent_games[0].get("name", "Steam Game")
            stats["recent_games_list"] = [
                {"name": g.get("name", "Steam Game"), "hours": f"{round(g.get('playtime_2weeks', 0) / 60, 1)}h"}
                for g in recent_games[:3]
            ]

        # 2. 보유 게임 및 누적 플레이 타임 수집 (최대 2회 재시도)
        games_list = []
        for attempt in range(2):
            try:
                games_url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/?key={steam_api_key}&steamid={friend_steam_id}&include_appinfo=1"
                res_g = (await client.get(games_url, timeout=5.0)).json()
                games_list = res_g.get("response", {}).get("games", [])
                if games_list:
                    break
            except Exception:
                await asyncio.sleep(0.1)

        if games_list:
            total_min = sum(g.get("playtime_forever", 0) for g in games_list)
            tot_hours = round(total_min / 60)
            stats["total_hours"] = tot_hours
            stats["total"] = f"{tot_hours:,}h"

            friend_appids_map = {g["appid"]: g.get("name", "Steam Game") for g in games_list if "appid" in g}
            friend_appids = set(friend_appids_map.keys())

            if owner_appids:
                shared_appids = owner_appids.intersection(friend_appids)
                stats["shared_count"] = len(shared_appids)
                stats["shared"] = f"{len(shared_appids)}개"
                stats["shared_games"] = [friend_appids_map[aid] for aid in list(shared_appids)[:4] if aid in friend_appids_map]

            # 3. 상위 3개 게임 업적 달성률 계산 (포함 완료)
            top_games = sorted(games_list, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:3]
            unlocked_cnt = 0
            avail_cnt = 0

            for g in top_games:
                appid = g.get("appid")
                if not appid:
                    continue
                try:
                    ach_url = f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/?key={steam_api_key}&steamid={friend_steam_id}&appid={appid}"
                    res_ach = (await client.get(ach_url, timeout=2.5)).json()
                    ach_list = res_ach.get("playerstats", {}).get("achievements", [])
                    if ach_list:
                        avail_cnt += len(ach_list)
                        unlocked_cnt += sum(1 for a in ach_list if a.get("achieved") == 1)
                except Exception:
                    continue

            if avail_cnt > 0:
                stats["achievement"] = f"{round(unlocked_cnt / avail_cnt * 100)}%"
            else:
                stats["achievement"] = "비공개"

    return stats

# ---------------------------------------------------------------------------
# API 엔드포인트
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/api/info")
async def info() -> dict[str, str]:
    return {
        "message": "WEB Pod에서 WAS Service로 정상 연결되었습니다.",
        "was_pod": socket.gethostname(),
        "version": "v5-eks-steam-bedrock",
    }

MOCK_REGIONS = ["KR", "US", "JP", "DE", "BR", "GB", "CA"]

def _mock_user_response(username: str) -> dict:
    return {
        "status": "ok",
        "username": username,
        "steam_id": None,
        "avatar_url": "",
        "profile_url": None,
        "persona_state": random.choice(["online", "offline"]),
        "data_source": "MOCK",
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{random.randint(150, 500)}",
            "hours": f"{random.randint(1000, 5000):,}h",
            "achievements": f"{random.randint(5, 80)}개",
            "friends": f"{random.randint(40, 300)}명",
            "region": random.choice(MOCK_REGIONS),
        },
        "playstyle": random.choice(PLAYSTYLES),
        "insight": random.choice(INSIGHTS),
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{username}' 분석 데이터를 생성했습니다. (STEAM_API_KEY 미설정)",
    }

@app.get("/api/user/{username}")
async def analyze_user(username: str, request: Request) -> dict:
    steam_api_key = os.getenv("STEAM_API_KEY")
    if not steam_api_key:
        return _mock_user_response(username)

    client = await get_http_client(request.app)
    try:
        steamid = await resolve_steam_id(client, steam_api_key, username)
        summary, games = await asyncio.gather(
            fetch_player_summary(client, steam_api_key, steamid),
            fetch_owned_games(client, steam_api_key, steamid),
        )
    except SteamUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Steam API 조회 실패: {exc}") from exc

    display_name = summary.get("personaname", username)
    display_games = build_game_entries(games, limit=5)
    total_minutes = sum(g.get("playtime_forever", 0) for g in games)

    friends_task = fetch_friend_list(client, steam_api_key, steamid)
    achievements_task = asyncio.gather(
        *(fetch_game_achievements(client, steam_api_key, steamid, g["appid"]) for g in display_games)
    )

    friends, per_game_achievements = await asyncio.gather(friends_task, achievements_task)

    for game, achievements in zip(display_games, per_game_achievements):
        game["achievements"] = achievements

    achievement_total = sum(a["achieved"] for a in per_game_achievements if a is not None)

    return {
        "status": "ok",
        "username": display_name,
        "steam_id": steamid,
        "avatar_url": summary.get("avatarfull", ""),
        "profile_url": summary.get("profileurl"),
        "persona_state": persona_state_label(summary),
        "data_source": "STEAM_API",
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{len(games)}",
            "hours": minutes_to_hours_label(total_minutes),
            "achievements": f"{achievement_total}개",
            "friends": f"{len(friends)}명",
            "region": summary.get("loccountrycode") or "-",
        },
        "playstyle": random.choice(PLAYSTYLES),
        "insight": random.choice(INSIGHTS),
        "top_games": display_games,
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{display_name}' 분석 데이터를 생성했습니다.",
    }

@app.get("/api/friends/{username}")
async def get_user_friends(username: str, request: Request) -> dict:
    steam_api_key = os.getenv("STEAM_API_KEY")
    if not steam_api_key:
        return {"status": "ok", "username": username, "was_pod": socket.gethostname(), "friends": []}

    client = await get_http_client(request.app)
    try:
        owner_steam_id = await resolve_steam_id(client, steam_api_key, username)
    except Exception:
        owner_steam_id = username if (username.isdigit() and len(username) == 17) else None

    real_friends = []
    owner_appids = await get_user_owned_appids(client, steam_api_key, owner_steam_id) if owner_steam_id else set()
    
    if owner_steam_id:
        try:
            friends_list = await fetch_friend_list(client, steam_api_key, owner_steam_id)
            if friends_list:
                friend_ids = [f["steamid"] for f in friends_list[:100]]
                ids_str = ",".join(friend_ids)

                summaries_url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/?key={steam_api_key}&steamids={ids_str}"
                res_s = (await client.get(summaries_url)).json()
                players = res_s.get("response", {}).get("players", [])

                sem = asyncio.Semaphore(4)
                
                async def process_player(p):
                    f_steam_id = p.get("steamid", "")
                    f_stats = await fetch_friend_real_stats(client, steam_api_key, f_steam_id, owner_appids, sem)
                    return {
                        "steam_id": f_steam_id,
                        "name": p.get("personaname", "Steam Friend"),
                        "code": p.get("personaname", "SF")[:2].upper(),
                        "avatar_url": p.get("avatarfull") or p.get("avatarmedium") or "",
                        "country": p.get("loccountrycode", "UN"),
                        "game": f_stats["game"],
                        "twoWeeks": f_stats["twoWeeks"],
                        "twoWeeks_minutes": f_stats["twoWeeks_minutes"],
                        "total": f_stats["total"],
                        "total_hours": f_stats["total_hours"],
                        "shared": f_stats["shared"],
                        "shared_count": f_stats["shared_count"],
                        "shared_games": f_stats["shared_games"],
                        "recent_games_list": f_stats["recent_games_list"],
                        "achievement": f_stats["achievement"],
                        "trait": "Steam 친구"
                    }

                if players:
                    real_friends = await asyncio.gather(*(process_player(p) for p in players))
        except Exception as e:
            print(f"Real Steam Friends API fetch failed: {e}")

    # 비동기 aiomysql DB 저장
    if os.getenv("DB_HOST") and owner_steam_id and real_friends:
        try:
            pool = await get_db_pool(request.app)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(STEAM_USER_FRIENDS_TABLE_SQL)
                for f in real_friends:
                    await cursor.execute(
                        """
                        INSERT INTO steam_user_friends
                        (owner_steam_id, friend_steam_id, friend_name, avatar_url, country, most_played_game, playtime_2weeks_minutes, total_playtime_hours, shared_games_count, trait)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        friend_name=VALUES(friend_name), avatar_url=VALUES(avatar_url), country=VALUES(country), most_played_game=VALUES(most_played_game),
                        playtime_2weeks_minutes=VALUES(playtime_2weeks_minutes), total_playtime_hours=VALUES(total_playtime_hours),
                        shared_games_count=VALUES(shared_games_count), trait=VALUES(trait)
                        """,
                        (
                            owner_steam_id,
                            f["steam_id"],
                            f["name"],
                            f.get("avatar_url", ""),
                            f["country"],
                            f["game"],
                            f.get("twoWeeks_minutes", 0),
                            f.get("total_hours", 0),
                            f.get("shared_count", 0),
                            f["trait"]
                        )
                    )
        except Exception as e:
            print(f"Friends DB Save Error: {e}")

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "friends": real_friends
    }


@app.get("/api/trends")
def get_trend_games(tab: str = "overview", genre: str | None = None, limit: int = 4) -> dict:
    try:
        with closing(db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT MAX(collected_at) AS latest FROM game_chart_rankings")
                latest_ts = (cursor.fetchone() or {}).get("latest")

                if not latest_ts:
                    return {"status": "ok", "tab": tab, "trends": []}

                cursor.execute(
                    "SELECT MAX(collected_at) AS previous FROM game_chart_rankings WHERE collected_at < %s",
                    (latest_ts,),
                )
                previous_ts = (cursor.fetchone() or {}).get("previous")

                cursor.execute(
                    """
                    SELECT r.appid, r.ranking, r.concurrent_in_game,
                           g.name, g.header_image, g.genres, g.discount_percent
                    FROM game_chart_rankings r
                    LEFT JOIN game_info g ON g.appid = r.appid
                    WHERE r.collected_at = %s
                    ORDER BY r.ranking ASC
                    """,
                    (latest_ts,),
                )
                latest_rows = cursor.fetchall()

                previous_map = {}
                if previous_ts:
                    cursor.execute(
                        "SELECT appid, concurrent_in_game FROM game_chart_rankings WHERE collected_at = %s",
                        (previous_ts,),
                    )
                    previous_map = {row["appid"]: row["concurrent_in_game"] for row in cursor.fetchall()}

                genre_appids = None
                if genre:
                    cursor.execute(
                        "SELECT appid FROM game_genres WHERE genre_name = %s",
                        (genre,),
                    )
                    genre_appids = {row["appid"] for row in cursor.fetchall()}

        trends = []
        for row in latest_rows:
            if not row.get("name"):
                # game_info가 없는 appid(스토어 페이지가 깨졌거나 삭제된 앱 등) - 이름을 알 수 없으니 노출하지 않는다.
                continue

            current = row["concurrent_in_game"] or 0
            prev = previous_map.get(row["appid"])
            change_pct = round((current - prev) / prev * 100, 1) if prev else None

            genres = row.get("genres") or ""
            genre_label = genres.split(",")[0].strip().upper() if genres else "-"
            discount_percent = row.get("discount_percent") or 0

            trends.append({
                "appid": row["appid"],
                "name": row["name"],
                "genre": genre_label,
                "header_image": row.get("header_image") or "",
                "active": f"{current:,}",
                "active_raw": current,
                "change": f"{change_pct:+.1f}%" if change_pct is not None else "—",
                "change_pct": change_pct,
                "isUp": change_pct is None or change_pct >= 0,
                "discount": f"-{discount_percent}%" if discount_percent > 0 else "—",
                "discount_percent": discount_percent,
            })

        if genre_appids is not None:
            trends = [t for t in trends if t["appid"] in genre_appids]

        if tab == "discount":
            trends = [t for t in trends if t["discount_percent"] > 0]
            trends.sort(key=lambda t: t["discount_percent"], reverse=True)
        elif tab == "rising":
            trends = [t for t in trends if t["change_pct"] is not None]
            trends.sort(key=lambda t: t["change_pct"], reverse=True)
        elif tab == "popular":
            trends.sort(key=lambda t: t["active_raw"], reverse=True)
        elif tab == "news":
            trends = []  # 뉴스·패치는 아직 데이터 없음
        # overview: game_chart_rankings.ranking 순서 그대로 유지

        return {"status": "ok", "tab": tab, "trends": trends[:limit]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trend data fetch failed: {exc}") from exc


@app.get("/api/trends/timeseries")
def get_trend_timeseries(appid: int, hours: int = 24) -> dict:
    try:
        with closing(db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name FROM game_info WHERE appid = %s", (appid,))
                info_row = cursor.fetchone()
                name = (info_row or {}).get("name") or f"App {appid}"

                cursor.execute(
                    """
                    SELECT collected_at, concurrent_in_game
                    FROM game_chart_rankings
                    WHERE appid = %s
                    ORDER BY collected_at ASC
                    """,
                    (appid,),
                )
                all_rows = cursor.fetchall()

        # DB에는 UTC로 저장돼 있으므로(datetime.utcnow()), 버킷 경계/시간 라벨만 KST(UTC+9)로 변환한다.
        # 그래야 "저녁에 오르고 새벽에 내리는" 패턴이 실제 한국 시간과 맞게 표시/추정된다.
        now_utc = datetime.utcnow()
        now_kst = now_utc + KST_OFFSET
        window_start_utc = now_utc - timedelta(hours=hours)
        real_rows = [r for r in all_rows if r["collected_at"] >= window_start_utc]

        # 추정치의 기준값: 이 시간창 안의 가장 최근 실데이터, 없으면 전체 이력 중 가장 최근 값.
        base_row = real_rows[-1] if real_rows else (all_rows[-1] if all_rows else None)
        base_value = base_row["concurrent_in_game"] if base_row else 0

        points = []
        for i in range(hours - 1, -1, -1):
            bucket_start_kst = (now_kst - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
            bucket_start_utc = bucket_start_kst - KST_OFFSET
            bucket_end_utc = bucket_start_utc + timedelta(hours=1)

            bucket_values = [
                r["concurrent_in_game"] for r in real_rows
                if bucket_start_utc <= r["collected_at"] < bucket_end_utc
            ]
            if bucket_values:
                value = round(sum(bucket_values) / len(bucket_values))
                estimated = False
            else:
                value = estimate_concurrent(base_value, bucket_start_kst.hour, f"{appid}-{bucket_start_kst.hour}")
                estimated = True

            points.append({
                "label": bucket_start_kst.strftime("%H:%M"),
                "value": value,
                "estimated": estimated,
            })

        estimated_count = sum(1 for p in points if p["estimated"])
        if estimated_count == 0:
            source = "real"
        elif estimated_count == len(points):
            source = "estimated"
        else:
            source = "mixed"

        return {"status": "ok", "appid": appid, "name": name, "source": source, "points": points}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Timeseries fetch failed: {exc}") from exc


@app.get("/api/genres")
def get_genres() -> dict:
    try:
        with closing(db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name FROM genres ORDER BY name ASC")
                genres = [row["name"] for row in cursor.fetchall()]
        return {"status": "ok", "genres": genres}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Genres fetch failed: {exc}") from exc


@app.get("/api/news")
def get_news(limit: int = 8) -> dict:
    try:
        with closing(db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT MAX(collected_at) AS latest FROM game_chart_rankings")
                latest_ts = (cursor.fetchone() or {}).get("latest")
                if not latest_ts:
                    return {"status": "ok", "news": []}

                cursor.execute(
                    """
                    SELECT r.appid, g.name, g.header_image
                    FROM game_chart_rankings r
                    LEFT JOIN game_info g ON g.appid = r.appid
                    WHERE r.collected_at = %s
                    ORDER BY r.ranking ASC
                    LIMIT %s
                    """,
                    (latest_ts, limit),
                )
                games = cursor.fetchall()

        news_list = []
        for game in games:
            if not game.get("name"):
                # game_info가 없는 appid(스토어 페이지가 깨졌거나 삭제된 앱 등) - 이름을 알 수 없으니 노출하지 않는다.
                continue
            try:
                items = fetch_app_news(game["appid"], count=1, maxlength=280)
            except Exception as e:
                print(f"News fetch warning (appid={game['appid']}): {e}")
                continue
            if not items:
                continue
            item = items[0]
            news_list.append({
                "appid": game["appid"],
                "game_name": game["name"],
                "header_image": game.get("header_image") or "",
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": clean_news_summary(item.get("contents", "")),
                "date_unix": item.get("date"),
                "feed_label": item.get("feedlabel"),
            })

        return {"status": "ok", "news": news_list}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"News fetch failed: {exc}") from exc


@app.get("/api/db")
async def db_test(request: Request) -> dict:
    try:
        pool = await get_db_pool(request.app)
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(REQUEST_COUNTER_TABLE_SQL)
            await cursor.execute(
                "INSERT INTO request_counter (pod_name) VALUES (%s)",
                (socket.gethostname(),),
            )
            await cursor.execute(
                "SELECT COUNT(*) AS total_requests, NOW() AS database_time FROM request_counter"
            )
            result = await cursor.fetchone()

        return {
            "message": "WAS Pod에서 RDS MySQL로 정상 연결되었습니다.",
            "was_pod": socket.gethostname(),
            **result,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RDS connection failed: {exc}") from exc
