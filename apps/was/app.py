import asyncio
import json
import os
import random
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiomysql
import httpx
from fastapi import FastAPI, HTTPException, Request

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

DB_REQUIRED_ENV_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")

REQUEST_COUNTER_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS request_counter (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        pod_name VARCHAR(255) NOT NULL,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_ID64_RE = re.compile(r"^\d{17}$")

BEDROCK_DEFAULT_REGION = "us-east-1"
BEDROCK_DEFAULT_MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# ---------------------------------------------------------------------------
# DB 커넥션 풀 (지연 생성 — DB 환경 변수가 없어도 앱은 정상 기동해야 함)
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
    yield
    if app.state.db_pool is not None:
        app.state.db_pool.close()
        await app.state.db_pool.wait_closed()
    if app.state.http_client is not None:
        await app.state.http_client.aclose()


app = FastAPI(title="Steam Insight EKS WAS", version="5.0.0-steam-bedrock", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Steam Web API 클라이언트
# ---------------------------------------------------------------------------


class SteamUserNotFoundError(Exception):
    pass


async def resolve_steam_id(client: httpx.AsyncClient, api_key: str, identifier: str) -> str:
    """SteamID64, 커스텀 URL, 프로필 URL을 모두 받아 SteamID64로 정규화한다."""
    candidate = identifier.strip().rstrip("/").rsplit("/", 1)[-1]
    if STEAM_ID64_RE.match(candidate):
        return candidate

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
        params={
            "key": api_key,
            "steamid": steamid,
            "include_appinfo": 1,
            "include_played_free_games": 1,
        },
    )
    response.raise_for_status()
    return response.json().get("response", {}).get("games", [])


async def fetch_recently_played(client: httpx.AsyncClient, api_key: str, steamid: str) -> list[dict]:
    response = await client.get(
        f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/",
        params={"key": api_key, "steamid": steamid},
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
    except httpx.HTTPStatusError:
        # 친구 목록이 비공개인 프로필은 403을 반환한다 — 빈 목록으로 취급한다.
        return []
    return response.json().get("friendslist", {}).get("friends", [])


async def estimate_achievement_rate(
    client: httpx.AsyncClient, api_key: str, steamid: str, top_games: list[dict]
) -> int | None:
    """가장 많이 플레이한 게임 상위 5개의 업적 달성률 평균으로 전체 달성률을 근사한다.

    Steam Web API는 계정 전체를 아우르는 단일 업적 달성률을 제공하지 않는다.
    업적이 없거나 비공개인 게임은 평균 계산에서 제외한다.
    """

    async def game_ratio(appid: int) -> float | None:
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
        return achieved / len(achievements)

    ratios = await asyncio.gather(*(game_ratio(g["appid"]) for g in top_games[:5]))
    valid_ratios = [r for r in ratios if r is not None]
    if not valid_ratios:
        return None
    return round(sum(valid_ratios) / len(valid_ratios) * 100)


def minutes_to_hours_label(minutes: float) -> str:
    return f"{round(minutes / 60):,}h"


def top_games_by_playtime(games: list[dict], limit: int = 5) -> list[dict]:
    ranked = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
    return [
        {"name": g.get("name", "Unknown"), "hours": round(g.get("playtime_forever", 0) / 60, 1)}
        for g in ranked[:limit]
    ]


# ---------------------------------------------------------------------------
# AWS Bedrock (Bedrock API 키 기반 Bearer 인증) — 실 데이터 기반 인사이트 생성
# ---------------------------------------------------------------------------


async def call_bedrock(client: httpx.AsyncClient, api_key: str, region: str, model_id: str, prompt: str) -> str:
    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["content"][0]["text"]


def extract_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Bedrock 응답에서 JSON 객체를 찾을 수 없습니다.")
    return json.loads(match.group(0))


def bedrock_config() -> tuple[str, str, str] | None:
    api_key = os.getenv("BEDROCK_API_KEY")
    if not api_key:
        return None
    region = os.getenv("BEDROCK_REGION", BEDROCK_DEFAULT_REGION)
    model_id = os.getenv("BEDROCK_MODEL_ID", BEDROCK_DEFAULT_MODEL_ID)
    return api_key, region, model_id


async def generate_user_insight(
    client: httpx.AsyncClient, display_name: str, top_games: list[dict]
) -> tuple[str, str] | None:
    config = bedrock_config()
    if config is None or not top_games:
        return None

    api_key, region, model_id = config
    games_summary = ", ".join(f"{g['name']} {g['hours']}h" for g in top_games)
    prompt = (
        f"Steam 유저 '{display_name}'의 보유 게임별 누적 플레이 시간(높은 순): {games_summary}.\n"
        "이 데이터만 근거로 플레이스타일을 분석해서 아래 JSON 형식으로만 답하라. "
        "다른 설명, 인사말, 코드블록 표시는 절대 추가하지 마라.\n"
        '{"playstyle": "8자 내외의 한글 플레이스타일 명칭", '
        '"insight": "실제 게임 이름과 시간을 근거로 든 한글 2문장 이내 인사이트"}'
    )
    try:
        text = await call_bedrock(client, api_key, region, model_id, prompt)
        data = extract_json_object(text)
        playstyle = data.get("playstyle")
        insight = data.get("insight")
    except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
        return None

    if not playstyle or not insight:
        return None
    return playstyle, insight


async def generate_friend_trait(client: httpx.AsyncClient, friend_name: str, games_summary: str) -> str | None:
    config = bedrock_config()
    if config is None:
        return None

    api_key, region, model_id = config
    prompt = (
        f"Steam 친구 '{friend_name}'의 보유 게임별 누적 플레이 시간(높은 순): {games_summary or '데이터 없음'}.\n"
        '다음 JSON 형식으로만 답하라. 다른 텍스트는 추가하지 마라: '
        '{"trait": "이 친구의 플레이 성향을 나타내는 한글 한 문장"}'
    )
    try:
        text = await call_bedrock(client, api_key, region, model_id, prompt)
        return extract_json_object(text).get("trait")
    except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# 라우트
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


def _mock_user_response(username: str) -> dict:
    games_count = random.randint(150, 500)
    play_hours = random.randint(1000, 5000)
    achievement_rate = random.randint(50, 98)
    friends_count = random.randint(40, 300)

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{games_count}",
            "hours": f"{play_hours:,}h",
            "achievements": f"{achievement_rate}%",
            "friends": f"{friends_count}명",
        },
        "playstyle": random.choice(PLAYSTYLES),
        "insight": random.choice(INSIGHTS),
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{username}' 분석 데이터를 생성했습니다. (STEAM_API_KEY 미설정 — Mock 데이터)",
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
    ranked_games = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
    top_games = top_games_by_playtime(games)
    total_minutes = sum(g.get("playtime_forever", 0) for g in games)

    friends_task = fetch_friend_list(client, steam_api_key, steamid)
    achievement_task = estimate_achievement_rate(client, steam_api_key, steamid, ranked_games)
    insight_task = generate_user_insight(client, display_name, top_games)

    friends, achievement_rate, insight_result = await asyncio.gather(
        friends_task, achievement_task, insight_task
    )

    if insight_result is not None:
        playstyle, insight = insight_result
    else:
        playstyle, insight = random.choice(PLAYSTYLES), random.choice(INSIGHTS)

    return {
        "status": "ok",
        "username": display_name,
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{len(games)}",
            "hours": minutes_to_hours_label(total_minutes),
            "achievements": f"{achievement_rate}%" if achievement_rate is not None else "N/A",
            "friends": f"{len(friends)}명",
        },
        "playstyle": playstyle,
        "insight": insight,
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{display_name}' 분석 데이터를 생성했습니다.",
    }


def _mock_friends_response(username: str) -> dict:
    sampled = [dict(friend) for friend in random.sample(FRIEND_POOL, 5)]
    for friend in sampled:
        friend["twoWeeks"] = f"{random.randint(10, 60)}.{random.randint(0, 9)}h"
        friend["total"] = f"{random.randint(1000, 8000):,}h"
        friend["shared"] = f"{random.randint(5, 30)}개"

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "friends": sampled,
    }


async def build_real_friend_entry(
    client: httpx.AsyncClient, api_key: str, steamid: str, my_game_appids: set[int]
) -> dict | None:
    try:
        summary, games, recent = await asyncio.gather(
            fetch_player_summary(client, api_key, steamid),
            fetch_owned_games(client, api_key, steamid),
            fetch_recently_played(client, api_key, steamid),
        )
    except (httpx.HTTPError, SteamUserNotFoundError):
        return None

    total_minutes = sum(g.get("playtime_forever", 0) for g in games)
    two_weeks_minutes = sum(g.get("playtime_2weeks", 0) for g in recent)
    shared_count = len(my_game_appids & {g.get("appid") for g in games})
    top_games = top_games_by_playtime(games)
    display_name = summary.get("personaname", "Unknown")

    return {
        "name": display_name,
        "code": display_name[:2].upper(),
        "country": summary.get("loccountrycode", "—"),
        "game": top_games[0]["name"] if top_games else "—",
        "twoWeeks": f"{two_weeks_minutes / 60:.1f}h",
        "total": minutes_to_hours_label(total_minutes),
        "shared": f"{shared_count}개",
        "_top_games": top_games,
    }


@app.get("/api/friends/{username}")
async def get_user_friends(username: str, request: Request) -> dict:
    steam_api_key = os.getenv("STEAM_API_KEY")
    if not steam_api_key:
        return _mock_friends_response(username)

    client = await get_http_client(request.app)
    try:
        steamid = await resolve_steam_id(client, steam_api_key, username)
        my_games, friend_refs = await asyncio.gather(
            fetch_owned_games(client, steam_api_key, steamid),
            fetch_friend_list(client, steam_api_key, steamid),
        )
    except SteamUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Steam API 조회 실패: {exc}") from exc

    my_game_appids = {g.get("appid") for g in my_games}
    sampled_refs = friend_refs[:5]

    entries = await asyncio.gather(
        *(
            build_real_friend_entry(client, steam_api_key, ref["steamid"], my_game_appids)
            for ref in sampled_refs
        )
    )
    friends = [entry for entry in entries if entry is not None]

    async def with_trait(friend: dict) -> dict:
        games_summary = ", ".join(f"{g['name']} {g['hours']}h" for g in friend.pop("_top_games"))
        trait = await generate_friend_trait(client, friend["name"], games_summary)
        friend["trait"] = trait or random.choice(FRIEND_TRAITS)
        return friend

    friends = await asyncio.gather(*(with_trait(friend) for friend in friends))

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "friends": friends,
    }


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
