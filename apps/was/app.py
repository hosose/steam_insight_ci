import asyncio
import json
import os
import random
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# pyrefly: ignore [missing-import]
import aiomysql
# pyrefly: ignore [missing-import]
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
STEAM_ACCOUNT_ID_RE = re.compile(r"^\d{1,10}$")
STEAM_ID64_BASE = 76561197960265728  # SteamID64 = 이 값 + AccountID32(게임 내 "친구 코드"로 표시되는 값)
STEAM_ACCOUNT_ID_MAX = 2**32

# ---------------------------------------------------------------------------
# 글로벌 트렌드 관련 설정
#
# GetMostPlayedGames/featuredcategories/search-results/tagdata/GetNewsForApp
# 모두 STEAM_API_KEY 없이(키리스) 호출 가능하다 — 이 기능 전체는 Steam API 키가
# 없는 환경에서도 동작해야 한다.
# ---------------------------------------------------------------------------

KST = ZoneInfo("Asia/Seoul")

STEAM_APP_LIST_TTL_SECONDS = 6 * 60 * 60  # appid->이름 메모리 캐시 유효 기간 (6시간)
POPULARITY_CHART_SIZE = 100  # GetMostPlayedGames가 실제로 반환하는 고정 개수 (Steam 쪽 상수, 실측 확인함)

STORE_SEARCH_URL = "https://store.steampowered.com/search/results/"
FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
POPULAR_TAGS_URL = "https://store.steampowered.com/tagdata/populartags/koreana"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
APPDETAILS_CONCURRENCY = 8  # appdetails는 appid 1개당 1번 호출해야 해서(배치 불가, 실측 확인) 동시성만 제한

# search/results 엔드포인트는 count를 25 미만으로 줘도 항상 최소 25개를 돌려준다(실측 확인).
# 그래서 항상 25 이상을 요청하고, 응답 후 우리가 원하는 개수로 슬라이스한다.
STORE_SEARCH_MIN_COUNT = 25
TREND_LIST_MAX_LIMIT = 100

TREND_LIST_CATEGORIES = {
    # category -> search/results 쿼리 파라미터
    "top_sellers": {"filter": "topsellers", "sort_by": "_ASC"},
    "specials": {"specials": "1", "sort_by": "Discount_DESC"},
    "new_releases": {"filter": "popularnew", "sort_by": "Released_DESC"},
}
FEATURED_CATEGORIES_FALLBACK_KEY = {
    "top_sellers": "top_sellers",
    "specials": "specials",
    "new_releases": "new_releases",
}

NEWS_FANOUT_APP_COUNT = 10  # 뉴스·패치 탭에서 병합 대상으로 삼는 인기 상위 게임 수
NEWS_MAX_LIMIT = 50

INTERNAL_JOB_TOKEN_HEADER = "x-internal-job-token"
TREND_SNAPSHOT_HOUR_KST = 4  # 일별 스냅샷 배치를 매일 KST 새벽 4시에 실행

BEDROCK_DEFAULT_REGION = "us-east-1"
BEDROCK_DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Steam GetPlayerSummaries의 personastate 코드 → 표시용 상태 문자열
PERSONA_STATE_LABELS = {
    0: "offline",
    1: "online",
    2: "busy",
    3: "away",
    4: "snooze",
    5: "looking_to_trade",
    6: "looking_to_play",
}


def persona_state_label(summary: dict) -> str:
    return PERSONA_STATE_LABELS.get(summary.get("personastate", 0), "offline")


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

    # 글로벌 트렌드: appid -> 이름 메모리 캐시 (GetMostPlayedGames/search 결과엔 이름이 없음)
    app.state.app_name_cache = {"map": {}, "fetched_at": 0.0}
    app.state.app_name_cache_lock = asyncio.Lock()

    # 글로벌 트렌드: 일별 스냅샷/캐시 배치를 매일 KST 새벽에 갱신하는 in-process 스케줄러.
    # DB 환경변수가 없어도(로컬 무설정 실행 등) 죽지 않고 그냥 매 사이클 스킵한다.
    app.state.trend_refresh_task = asyncio.create_task(trend_refresh_scheduler_loop(app))

    yield

    app.state.trend_refresh_task.cancel()
    try:
        await app.state.trend_refresh_task
    except asyncio.CancelledError:
        pass
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
    """SteamID64, AccountID(친구 코드), 커스텀 URL, 프로필 URL을 모두 받아 SteamID64로 정규화한다.

    s.team 공유 단축 링크(`https://s.team/p/...`)는 로그인 세션이 있어야 최종
    프로필로 리다이렉트되는 Steam 자체의 제약 때문에 API 키만으로는 여기서
    풀 수 없다 — 그런 값은 뒤의 ResolveVanityURL 단계에서 "찾을 수 없음"으로
    처리된다.
    """
    candidate = identifier.strip().rstrip("/").rsplit("/", 1)[-1]
    if STEAM_ID64_RE.match(candidate):
        return candidate

    if STEAM_ACCOUNT_ID_RE.match(candidate) and int(candidate) < STEAM_ACCOUNT_ID_MAX:
        # 게임 내에서 "친구 코드"/AccountID로 표시되는 9~10자리 숫자를 SteamID64로 변환해
        # 실제 존재하는 계정인지 확인한다. 존재하지 않으면 바니티 URL 해석으로 넘어간다.
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
        return response.json().get("friendslist", {}).get("friends", [])
    except (httpx.HTTPError, ValueError):
        # 친구 목록이 비공개인 프로필은 403을 반환한다. 타임아웃/연결 오류 등
        # 다른 네트워크·파싱 실패도 friends_task가 analyze_user의 보호되지 않은
        # 두 번째 asyncio.gather에서 그대로 전파돼 500을 유발하지 않도록 빈 목록으로 취급한다.
        return []


async def fetch_game_achievements(
    client: httpx.AsyncClient, api_key: str, steamid: str, appid: int | None
) -> dict | None:
    """특정 게임의 업적 달성 개수(achieved/total)를 가져온다.

    업적이 없는 게임이거나, 업적 통계가 비공개인 계정이면 None을 반환한다.
    (Steam Web API는 계정 전체를 아우르는 단일 업적 지표를 제공하지 않으므로,
    표시 중인 게임별로만 개수를 조회한다.)
    """
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
    """플레이 시간 상위 게임을 이름/시간과 함께 Steam 스토어 이미지·링크까지 포함해 반환한다.

    이미지/스토어 URL은 appid만 있으면 예측 가능한 Steam CDN/스토어 URL 패턴이라
    별도 API 호출 없이 구성한다.
    """
    ranked = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
    entries = []
    for g in ranked[:limit]:
        appid = g.get("appid")
        entries.append(
            {
                "appid": appid,
                "name": g.get("name", "Unknown"),
                "hours": round(g.get("playtime_forever", 0) / 60, 1),
                "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg" if appid else None,
                "storeUrl": f"https://store.steampowered.com/app/{appid}" if appid else None,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# 글로벌 트렌드 — 인기(동접자 순위) 데이터
# ---------------------------------------------------------------------------


async def fetch_single_app_name(client: httpx.AsyncClient, appid: int, semaphore: asyncio.Semaphore) -> str | None:
    """appdetails는 (실측 확인) appid를 콤마로 여러 개 넘기면 배치로 안 되고 null이 온다 —
    appid 1개당 1번 호출해야 한다. 그래서 세마포어로 동시 호출 수를 제한한다."""
    async with semaphore:
        try:
            response = await client.get(
                APPDETAILS_URL,
                params={"appids": appid, "filters": "basic", "cc": "kr", "l": "korean"},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json().get(str(appid)) or {}
        except (httpx.HTTPError, ValueError):
            return None
        if not payload.get("success"):
            return None
        return payload.get("data", {}).get("name")


async def get_app_name_map(app: FastAPI, appids: list[int]) -> dict[int, str]:
    """주어진 appid들의 이름을 프로세스 메모리에 캐시하며 채운다.

    GetMostPlayedGames/search/results 결과에는 이름이 없고, Steam은 더 이상
    ISteamApps/GetAppList를 제공하지 않는다(실측 확인: 404). appdetails는
    appid 1개당 1번 호출해야 해서, 캐시에 없는 appid만 골라 동시성을 제한해 채운다.
    캐시는 STEAM_APP_LIST_TTL_SECONDS(6시간)마다 통째로 리셋한다.
    """
    cache = app.state.app_name_cache
    loop_now = asyncio.get_event_loop().time()
    if (loop_now - cache["fetched_at"]) > STEAM_APP_LIST_TTL_SECONDS:
        cache["map"] = {}
        cache["fetched_at"] = loop_now

    missing = [a for a in dict.fromkeys(appids) if a and a not in cache["map"]]
    if not missing:
        return cache["map"]

    async with app.state.app_name_cache_lock:
        missing = [a for a in missing if a not in cache["map"]]  # 락 대기 중 다른 요청이 이미 채웠을 수 있음
        if missing:
            client = await get_http_client(app)
            semaphore = asyncio.Semaphore(APPDETAILS_CONCURRENCY)
            names = await asyncio.gather(*(fetch_single_app_name(client, a, semaphore) for a in missing))
            for appid, name in zip(missing, names):
                cache["map"][appid] = name or f"App {appid}"
    return cache["map"]


async def fetch_most_played_games(client: httpx.AsyncClient) -> dict:
    response = await client.get(f"{STEAM_API_BASE}/ISteamChartsService/GetMostPlayedGames/v1/", timeout=15.0)
    response.raise_for_status()
    return response.json().get("response", {})


def compute_week_rank_change(entry: dict) -> dict:
    """DB에 어제 스냅샷이 없을 때 쓰는 정직한 폴백.

    Steam이 주는 건 지난주 대비뿐이라, "어제 대비"인 척 하지 않고
    basis를 "week"로 명시한다 (없으면 "none").
    """
    rank = entry.get("rank")
    last_week_rank = entry.get("last_week_rank")
    if not rank or not last_week_rank:
        return {"value": None, "direction": "none", "basis": "none"}
    diff = last_week_rank - rank
    direction = "up" if diff > 0 else ("down" if diff < 0 else "same")
    return {"value": abs(diff), "direction": direction, "basis": "week"}


def compute_day_rank_change(current_rank: int, yesterday_rank: int | None) -> dict | None:
    if yesterday_rank is None:
        return None
    diff = yesterday_rank - current_rank
    direction = "up" if diff > 0 else ("down" if diff < 0 else "same")
    return {"value": abs(diff), "direction": direction, "basis": "day"}


def build_popularity_items(
    ranks: list[dict],
    name_map: dict[int, str],
    limit: int,
    rank_changes: dict[int, dict] | None = None,
) -> list[dict]:
    items = []
    for entry in ranks[:limit]:
        appid = entry.get("appid")
        rank_change = (rank_changes or {}).get(appid) or compute_week_rank_change(entry)
        items.append(
            {
                "rank": entry.get("rank"),
                "appid": appid,
                "name": name_map.get(appid, f"App {appid}") if appid else "Unknown",
                "peak_in_game": entry.get("peak_in_game"),
                "last_week_rank": entry.get("last_week_rank"),
                "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg" if appid else None,
                "storeUrl": f"https://store.steampowered.com/app/{appid}" if appid else None,
                "rank_change": rank_change,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 글로벌 트렌드 — 누적 판매순 / 할인 제품 / 최신작
#
# Steam 공식 API는 이 세 목록을 50~100개 단위로 주는 방법이 없다
# (featuredcategories는 10~30개뿐). store.steampowered.com이 자체 검색/차트
# 페이지의 무한스크롤에 쓰는 비공식 search/results 엔드포인트를 대신 쓴다.
# 응답이 HTML 프래그먼트라 서버에서 파싱해야 하며, Steam이 마크업을 바꾸면
# 깨질 수 있다 — 그래서 tag_id=0(전체 장르)에 한해 featuredcategories로 폴백한다.
# ---------------------------------------------------------------------------

_STORE_ITEM_RE = re.compile(
    r'<a\s+href="([^"]+)"\s+data-ds-appid="(\d+)"[^>]*?(?:data-ds-tagids="(\[[^\]]*\])")?[^>]*>'
)


def parse_store_search_html(html: str) -> list[dict]:
    """search/results의 results_html 프래그먼트에서 게임 목록을 뽑아낸다.

    항목 하나를 파싱하다 실패해도 나머지 항목은 계속 처리한다 (부분 실패 허용) —
    비공식 마크업이라 필드 하나가 사라져도 전체 목록이 깨지면 안 된다.
    """
    anchors = list(_STORE_ITEM_RE.finditer(html))
    items = []
    for i, m in enumerate(anchors):
        block_start = m.end()
        block_end = anchors[i + 1].start() if i + 1 < len(anchors) else len(html)
        block = html[block_start:block_end]

        try:
            appid = int(m.group(2))
        except (TypeError, ValueError):
            continue

        try:
            tag_ids = json.loads(m.group(3)) if m.group(3) else []
        except json.JSONDecodeError:
            tag_ids = []

        title_match = re.search(r'<span class="title">([^<]*)</span>', block)
        if not title_match:
            continue
        name = title_match.group(1).strip()
        if not name:
            continue

        released_match = re.search(
            r'<div class="search_released responsive_secondrow">\s*([^<]*?)\s*</div>', block
        )
        release_date = (released_match.group(1).strip() or None) if released_match else None

        discount_match = re.search(r'data-discount="(\d+)"', block)
        discount_percent = int(discount_match.group(1)) if discount_match else 0

        price_match = re.search(r'data-price-final="(\d+)"', block)
        final_price = int(price_match.group(1)) if price_match else None

        original_price = None
        original_price_match = re.search(r'<div class="discount_original_price">[^\d]*([\d,]+)', block)
        if original_price_match:
            try:
                original_price = int(original_price_match.group(1).replace(",", "")) * 100
            except ValueError:
                original_price = None
        elif discount_percent == 0:
            original_price = final_price

        items.append(
            {
                "appid": appid,
                "name": name,
                "genre_tag_ids": tag_ids,
                "release_date": release_date,
                "discount_percent": discount_percent,
                "final_price_cents": final_price,
                "original_price_cents": original_price,
                "storeUrl": f"https://store.steampowered.com/app/{appid}",
                "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
            }
        )
    return items


async def fetch_trend_list_live(
    client: httpx.AsyncClient, category: str, tag_id: int, offset: int, limit: int
) -> dict:
    """search/results를 실시간 호출해 누적판매순/할인제품/최신작 목록을 만든다.

    이 엔드포인트는 count를 25 미만으로 줘도 항상 25개 이상을 돌려준다(실측 확인) —
    그래서 항상 STORE_SEARCH_MIN_COUNT 이상을 요청하고, 우리가 원하는 개수로 슬라이스한다.
    """
    params = dict(TREND_LIST_CATEGORIES[category])
    params.update(
        {
            "query": "",
            "start": offset,
            "count": max(limit, STORE_SEARCH_MIN_COUNT),
            "supportedlang": "korean",
            "infinite": 1,
            "cc": "kr",
            "l": "korean",
        }
    )
    if tag_id:
        params["tags"] = tag_id

    response = await client.get(STORE_SEARCH_URL, params=params, timeout=15.0)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise ValueError("search/results 응답이 실패(success != 1)를 반환했습니다.")

    items = parse_store_search_html(payload.get("results_html", ""))[:limit]
    for i, item in enumerate(items):
        item["rank_position"] = offset + i + 1
    return {"items": items, "total_available": payload.get("total_count", len(items))}


async def fetch_featured_categories_fallback(client: httpx.AsyncClient, category: str) -> dict:
    """search/results 스크래핑이 실패했을 때 쓰는 훨씬 얕은(10~30개) 폴백.

    장르 필터를 지원하지 않으므로, 호출부에서 tag_id=0일 때만 사용해야 한다.
    """
    response = await client.get(FEATURED_CATEGORIES_URL, params={"cc": "kr", "l": "korean"}, timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    key = FEATURED_CATEGORIES_FALLBACK_KEY[category]
    raw_items = payload.get(key, {}).get("items", [])

    items = []
    for i, raw in enumerate(raw_items):
        appid = raw.get("id")
        if not appid:
            continue
        items.append(
            {
                "appid": appid,
                "name": raw.get("name", "Unknown"),
                "genre_tag_ids": [],
                "release_date": None,
                "discount_percent": raw.get("discount_percent", 0),
                "final_price_cents": raw.get("final_price"),
                "original_price_cents": raw.get("original_price"),
                "storeUrl": f"https://store.steampowered.com/app/{appid}",
                "image": raw.get("header_image")
                or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                "rank_position": i + 1,
            }
        )
    return {"items": items, "total_available": len(items)}


async def fetch_genre_tags_live(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(POPULAR_TAGS_URL, timeout=10.0)
    response.raise_for_status()
    raw = response.json()
    return [{"tag_id": t["tagid"], "name": t["name"]} for t in raw if t.get("tagid") and t.get("name")]


# ---------------------------------------------------------------------------
# 글로벌 트렌드 — 뉴스 · 패치
#
# Steam은 게임별 뉴스(ISteamNews)만 제공하고 "전체 게임 뉴스 피드"가 없다.
# 인기 상위 게임들의 뉴스를 모아 발행일 기준으로 병합해서 흉내낸다.
# ---------------------------------------------------------------------------


async def fetch_app_news(client: httpx.AsyncClient, appid: int, count: int = 3) -> list[dict]:
    try:
        response = await client.get(
            f"{STEAM_API_BASE}/ISteamNews/GetNewsForApp/v2/",
            params={"appid": appid, "count": count, "maxlength": 300},
            timeout=10.0,
        )
        response.raise_for_status()
        news_items = response.json().get("appnews", {}).get("newsitems", [])
    except (httpx.HTTPError, ValueError):
        return []

    return [
        {
            "appid": appid,
            "gid": item.get("gid"),
            "title": item.get("title"),
            "url": item.get("url"),
            "contents_snippet": (item.get("contents") or "")[:300],
            "published_at": item.get("date"),
            "feed_label": item.get("feedlabel"),
        }
        for item in news_items
        if item.get("gid") and item.get("title")
    ]


async def fetch_merged_news_live(client: httpx.AsyncClient, app: FastAPI, limit: int) -> list[dict]:
    chart = await fetch_most_played_games(client)
    top_appids = [e["appid"] for e in chart.get("ranks", [])[:NEWS_FANOUT_APP_COUNT] if e.get("appid")]
    name_map = await get_app_name_map(app, top_appids)

    per_app_news = await asyncio.gather(*(fetch_app_news(client, appid) for appid in top_appids))
    merged = [item for group in per_app_news for item in group]
    for item in merged:
        item["game_name"] = name_map.get(item["appid"], f"App {item['appid']}")
    merged.sort(key=lambda x: x.get("published_at") or 0, reverse=True)
    return merged[:limit]


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


def normalize_bedrock_api_key(raw_key: str) -> str:
    """AWS 콘솔에서 Bedrock API 키를 복사하면 'BedrockAPIKey-<id>,<실제 토큰>' 형태로
    Key ID와 실제 토큰이 콤마로 함께 붙어오는 경우가 있다. 실제 인증에 쓰이는 값은
    'ABSK'로 시작하는 뒷부분뿐이므로, 콤마가 있으면 'ABSK'로 시작하는 조각만 취한다.
    """
    raw_key = raw_key.strip()
    if "," in raw_key:
        for part in raw_key.split(","):
            part = part.strip()
            if part.startswith("ABSK"):
                return part
    return raw_key


def bedrock_config() -> tuple[str, str, str] | None:
    api_key = os.getenv("BEDROCK_API_KEY")
    if not api_key:
        return None
    region = os.getenv("BEDROCK_REGION", BEDROCK_DEFAULT_REGION)
    model_id = os.getenv("BEDROCK_MODEL_ID", BEDROCK_DEFAULT_MODEL_ID)
    return normalize_bedrock_api_key(api_key), region, model_id


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
# 글로벌 트렌드 — DB 캐시 / 일별 스냅샷 배치
#
# 이 레이어 전체는 선택 사항이다: DB_HOST가 없으면(로컬 무설정 실행 등)
# 모든 함수가 조용히 스킵/폴백하고, 위의 실시간 조회 경로가 그대로 응답한다.
# ---------------------------------------------------------------------------

TREND_DAILY_SNAPSHOT_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS trend_daily_snapshot (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        snapshot_date DATE NOT NULL,
        appid INT NOT NULL,
        `rank` INT NOT NULL,
        peak_in_game INT NULL,
        last_week_rank INT NULL,
        UNIQUE KEY uq_snapshot_appid (snapshot_date, appid),
        KEY idx_snapshot_date (snapshot_date)
    )
"""

GAME_METADATA_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS game_metadata (
        appid INT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        genre_tags JSON NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
"""

TREND_STORE_CACHE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS trend_store_cache (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        category VARCHAR(32) NOT NULL,
        tag_id INT NOT NULL DEFAULT 0,
        rank_position INT NOT NULL,
        appid INT NOT NULL,
        name VARCHAR(255) NOT NULL,
        release_date VARCHAR(64) NULL,
        discount_percent INT NOT NULL DEFAULT 0,
        original_price_cents INT NULL,
        final_price_cents INT NULL,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_cache_row (category, tag_id, rank_position)
    )
"""

GENRE_TAG_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS genre_tag (
        tag_id INT PRIMARY KEY,
        name VARCHAR(64) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
"""

TREND_NEWS_CACHE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS trend_news_cache (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        appid INT NOT NULL,
        gid VARCHAR(64) NOT NULL,
        title VARCHAR(512) NOT NULL,
        url VARCHAR(512) NULL,
        contents_snippet VARCHAR(500) NULL,
        published_at TIMESTAMP NOT NULL,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_news (appid, gid),
        KEY idx_published (published_at)
    )
"""

JOB_LOCK_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS job_lock (
        job_name VARCHAR(64) PRIMARY KEY,
        locked_until TIMESTAMP NOT NULL,
        locked_by VARCHAR(255) NULL
    )
"""

TREND_TABLE_SQLS = [
    TREND_DAILY_SNAPSHOT_TABLE_SQL,
    GAME_METADATA_TABLE_SQL,
    TREND_STORE_CACHE_TABLE_SQL,
    GENRE_TAG_TABLE_SQL,
    TREND_NEWS_CACHE_TABLE_SQL,
    JOB_LOCK_TABLE_SQL,
]


def trend_db_configured() -> bool:
    return os.getenv("DB_HOST") is not None


async def ensure_trend_tables(pool: aiomysql.Pool) -> None:
    async with pool.acquire() as connection, connection.cursor() as cursor:
        for sql in TREND_TABLE_SQLS:
            await cursor.execute(sql)


async def try_acquire_job_lock(pool: aiomysql.Pool, job_name: str, hold_minutes: int = 10) -> bool:
    """job_lock 행을 원자적으로 선점한다.

    같은 순간(또는 같은 파드가 잠금 유효 시간 내에 다시) 배치를 돌려도 실제로는
    한 번만 통과하도록 하는 방어 장치다 (upsert 자체는 멱등이라 중복 실행이
    안전하긴 하지만, 낭비되는 Steam 호출/스크래핑 요청 수를 줄이기 위해 둔다).

    lock 소유자 이름(pod_name)으로 "내가 지금 획득했는지"를 판별하면, 같은 파드가
    잠금이 아직 유효한 상태에서 다시 시도할 때 "예전의 내 소유"와 "방금 획득"을
    구분하지 못하는 문제가 있어(직접 재현·확인함) — 대신 MySQL의
    INSERT ... ON DUPLICATE KEY UPDATE affected-rows 값(신규 삽입=1, 실제 변경=2,
    조건이 거짓이라 아무 것도 안 바뀐 갱신=0)으로 판별한다. 0이면 "이미 누군가
    (자기 자신의 이전 실행 포함) 유효한 잠금을 들고 있다"는 뜻이라 실패 처리한다.
    """
    async with pool.acquire() as connection, connection.cursor() as cursor:
        affected = await cursor.execute(
            "INSERT INTO job_lock (job_name, locked_until, locked_by) "
            "VALUES (%s, NOW() + INTERVAL %s MINUTE, %s) AS new_lock "
            "ON DUPLICATE KEY UPDATE "
            "locked_until = IF(job_lock.locked_until < NOW(), new_lock.locked_until, job_lock.locked_until), "
            "locked_by = IF(job_lock.locked_until < NOW(), new_lock.locked_by, job_lock.locked_by)",
            (job_name, hold_minutes, socket.gethostname()),
        )
    return affected in (1, 2)


async def get_daily_rank_changes(app: FastAPI, today: date) -> dict[int, int]:
    """DB에 어제 스냅샷이 있으면 {appid: 어제_순위} 맵을 돌려주고,
    없으면(DB 미연동/배치 아직 안 돌았음) 빈 dict를 돌려준다.

    호출부는 빈 dict일 때 Steam의 last_week_rank 기반 폴백을 쓴다.
    """
    if not trend_db_configured():
        return {}
    try:
        pool = await get_db_pool(app)
        await ensure_trend_tables(pool)
        yesterday = today - timedelta(days=1)
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT appid, `rank` FROM trend_daily_snapshot WHERE snapshot_date = %s",
                (yesterday,),
            )
            rows = await cursor.fetchall()
    except Exception:
        return {}
    return {row["appid"]: row["rank"] for row in rows}


async def get_cached_trend_list(app: FastAPI, category: str, tag_id: int, offset: int, limit: int) -> dict | None:
    """캐시에 해당 category/tag_id 데이터가 있으면 페이지를 잘라 돌려주고, 없으면 None.

    tag_id != 0(장르 필터)은 refresh_trend_snapshot이 채우지 않으므로 항상 None —
    호출부가 실시간 스크래핑으로 자연히 폴백한다.
    """
    if not trend_db_configured():
        return None
    try:
        pool = await get_db_pool(app)
        await ensure_trend_tables(pool)
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) AS cnt FROM trend_store_cache WHERE category = %s AND tag_id = %s",
                (category, tag_id),
            )
            meta = await cursor.fetchone()
            if not meta or not meta["cnt"]:
                return None
            await cursor.execute(
                "SELECT rank_position, appid, name, release_date, discount_percent, "
                "original_price_cents, final_price_cents FROM trend_store_cache "
                "WHERE category = %s AND tag_id = %s ORDER BY rank_position ASC LIMIT %s OFFSET %s",
                (category, tag_id, limit, offset),
            )
            rows = await cursor.fetchall()
    except Exception:
        return None

    if not rows:
        return None

    items = [
        {
            **row,
            "storeUrl": f"https://store.steampowered.com/app/{row['appid']}",
            "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{row['appid']}/header.jpg",
        }
        for row in rows
    ]
    return {"items": items, "total_available": meta["cnt"]}


async def refresh_trend_snapshot(app: FastAPI) -> dict:
    """일별 배치의 실제 작업 — 순수 함수로 분리해 스케줄러/수동 트리거 양쪽에서 재사용한다."""
    pool = await get_db_pool(app)
    await ensure_trend_tables(pool)

    if not await try_acquire_job_lock(pool, "refresh_trends"):
        return {"status": "skipped", "reason": "다른 파드가 이미 배치를 실행 중입니다."}

    client = await get_http_client(app)
    today = datetime.now(KST).date()

    chart, genre_tags = await asyncio.gather(
        fetch_most_played_games(client),
        fetch_genre_tags_live(client),
    )
    ranks = chart.get("ranks", [])
    name_map = await get_app_name_map(app, [e["appid"] for e in ranks if e.get("appid")])

    snapshot_rows = [
        (today, e["appid"], e["rank"], e.get("peak_in_game"), e.get("last_week_rank"))
        for e in ranks
        if e.get("appid") and e.get("rank")
    ]
    metadata_rows = [(e["appid"], name_map.get(e["appid"], f"App {e['appid']}")) for e in ranks if e.get("appid")]
    genre_rows = [(t["tag_id"], t["name"]) for t in genre_tags]

    store_cache_rows: list[tuple] = []
    for category in TREND_LIST_CATEGORIES:
        try:
            result = await fetch_trend_list_live(client, category, tag_id=0, offset=0, limit=TREND_LIST_MAX_LIMIT)
        except (httpx.HTTPError, ValueError):
            continue
        for item in result["items"]:
            store_cache_rows.append(
                (
                    category,
                    0,
                    item["rank_position"],
                    item["appid"],
                    item["name"],
                    item.get("release_date"),
                    item.get("discount_percent", 0),
                    item.get("original_price_cents"),
                    item.get("final_price_cents"),
                )
            )
            metadata_rows.append((item["appid"], item["name"]))

    try:
        news_items = await fetch_merged_news_live(client, app, limit=NEWS_MAX_LIMIT)
    except (httpx.HTTPError, ValueError):
        news_items = []

    async with pool.acquire() as connection, connection.cursor() as cursor:
        if snapshot_rows:
            await cursor.executemany(
                "INSERT INTO trend_daily_snapshot (snapshot_date, appid, `rank`, peak_in_game, last_week_rank) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE `rank`=VALUES(`rank`), peak_in_game=VALUES(peak_in_game), "
                "last_week_rank=VALUES(last_week_rank)",
                snapshot_rows,
            )
        if metadata_rows:
            dedup_metadata = list({row[0]: row for row in metadata_rows}.values())
            await cursor.executemany(
                "INSERT INTO game_metadata (appid, name) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name)",
                dedup_metadata,
            )
        if genre_rows:
            await cursor.executemany(
                "INSERT INTO genre_tag (tag_id, name) VALUES (%s, %s) ON DUPLICATE KEY UPDATE name=VALUES(name)",
                genre_rows,
            )
        if store_cache_rows:
            await cursor.executemany(
                "INSERT INTO trend_store_cache "
                "(category, tag_id, rank_position, appid, name, release_date, discount_percent, "
                "original_price_cents, final_price_cents) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE appid=VALUES(appid), name=VALUES(name), "
                "release_date=VALUES(release_date), discount_percent=VALUES(discount_percent), "
                "original_price_cents=VALUES(original_price_cents), final_price_cents=VALUES(final_price_cents)",
                store_cache_rows,
            )
        if news_items:
            news_rows = [
                (
                    n["appid"],
                    n["gid"],
                    n["title"][:512],
                    n.get("url"),
                    n.get("contents_snippet"),
                    datetime.fromtimestamp(n["published_at"], tz=KST) if n.get("published_at") else datetime.now(KST),
                )
                for n in news_items
                if n.get("gid")
            ]
            await cursor.executemany(
                "INSERT INTO trend_news_cache (appid, gid, title, url, contents_snippet, published_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title), url=VALUES(url), "
                "contents_snippet=VALUES(contents_snippet), published_at=VALUES(published_at)",
                news_rows,
            )

    return {
        "status": "ok",
        "snapshot_rows": len(snapshot_rows),
        "store_cache_rows": len(store_cache_rows),
        "news_rows": len(news_items),
        "genre_rows": len(genre_rows),
    }


async def trend_refresh_scheduler_loop(app: FastAPI) -> None:
    """매일 KST TREND_SNAPSHOT_HOUR_KST시에 refresh_trend_snapshot을 실행하는 루프.

    DB_HOST가 없으면(로컬 무설정 실행 등) 매 사이클 조용히 스킵한다 — 이 루프
    때문에 앱이 죽거나 무설정 부팅이 깨지면 안 된다. 이 로직은 나중에
    steam_insight_cd에 k8s CronJob이 생기면 그대로 대체될 수 있도록,
    POST /internal/jobs/refresh-trends가 호출하는 것과 같은 refresh_trend_snapshot()을
    그대로 재사용한다.
    """
    while True:
        now = datetime.now(KST)
        next_run = now.replace(hour=TREND_SNAPSHOT_HOUR_KST, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())

        if not trend_db_configured():
            continue  # DB 미연동 환경 — 배치는 건너뛴다 (라이브 폴백 경로가 항상 동작함)

        try:
            await refresh_trend_snapshot(app)
        except Exception as exc:  # noqa: BLE001 - 배치 실패로 앱 전체가 죽으면 안 됨
            print(f"[trend_refresh_scheduler_loop] 배치 실패: {exc}")


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


MOCK_REGIONS = ["KR", "US", "JP", "DE", "BR", "GB", "CA"]


def _mock_user_response(username: str) -> dict:
    games_count = random.randint(150, 500)
    play_hours = random.randint(1000, 5000)
    achievement_count = random.randint(5, 80)
    friends_count = random.randint(40, 300)

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
            "games": f"{games_count}",
            "hours": f"{play_hours:,}h",
            "achievements": f"{achievement_count}개",
            "friends": f"{friends_count}명",
            "region": random.choice(MOCK_REGIONS),
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
    # 프로필 카드에는 가장 많이 플레이한 5개 게임만 보여준다 (플레이 시간 + 게임별 업적).
    display_games = build_game_entries(games, limit=5)
    total_minutes = sum(g.get("playtime_forever", 0) for g in games)

    friends_task = fetch_friend_list(client, steam_api_key, steamid)
    achievements_task = asyncio.gather(
        *(fetch_game_achievements(client, steam_api_key, steamid, g["appid"]) for g in display_games)
    )
    insight_task = generate_user_insight(client, display_name, display_games)

    friends, per_game_achievements, insight_result = await asyncio.gather(
        friends_task, achievements_task, insight_task
    )

    for game, achievements in zip(display_games, per_game_achievements):
        game["achievements"] = achievements

    # 계정 전체 업적 달성률을 제공하는 API가 없어, 화면에 보여주는 상위 5개 게임의
    # 달성 개수를 그대로 합산해 표시한다 (근사치가 아니라 실제 개수).
    achievement_total = sum(a["achieved"] for a in per_game_achievements if a is not None)

    # 실 데이터 경로에서는 플레이스타일/인사이트를 Bedrock 생성 결과로만 채운다.
    # BEDROCK_API_KEY 미설정이나 호출 실패 시 random 하드코딩 값으로 대체하지 않고
    # null로 남겨, 프론트엔드가 "생성 실패/미설정" 상태를 있는 그대로 알 수 있게 한다.
    playstyle, insight = insight_result if insight_result is not None else (None, None)

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
        "playstyle": playstyle,
        "insight": insight,
        "top_games": display_games,
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{display_name}' 분석 데이터를 생성했습니다.",
    }


def _mock_friends_response(username: str) -> dict:
    sampled = [dict(friend) for friend in random.sample(FRIEND_POOL, 5)]
    for friend in sampled:
        friend["twoWeeks"] = f"{random.randint(10, 60)}.{random.randint(0, 9)}h"
        friend["total"] = f"{random.randint(1000, 8000):,}h"
        friend["shared"] = f"{random.randint(5, 30)}개"
        friend["profile_url"] = None
        friend["persona_state"] = random.choice(["online", "offline"])

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
    top_games = build_game_entries(games)
    display_name = summary.get("personaname", "Unknown")

    return {
        "name": display_name,
        "code": display_name[:2].upper(),
        "country": summary.get("loccountrycode", "—"),
        "profile_url": summary.get("profileurl"),
        "persona_state": persona_state_label(summary),
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
        # trait도 Bedrock 생성 결과로만 채운다 — 실패/미설정 시 random 하드코딩
        # 문구 대신 null로 남긴다 (analyze_user의 playstyle/insight와 동일한 원칙).
        games_summary = ", ".join(f"{g['name']} {g['hours']}h" for g in friend.pop("_top_games"))
        friend["trait"] = await generate_friend_trait(client, friend["name"], games_summary)
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


# ---------------------------------------------------------------------------
# 글로벌 트렌드 라우트
#
# 전부 키리스 Steam 엔드포인트만 쓰므로 STEAM_API_KEY 없이도 동작하고, DB가
# 없어도(day 대신 week/none 비교 기준, 캐시 대신 실시간 조회) 동작한다.
# ---------------------------------------------------------------------------


@app.get("/api/trends/popularity")
async def trends_popularity(request: Request, limit: int = 10) -> dict:
    """인기 / 인기순 탭 — Steam 동시 접속자 기준 TOP 100 차트."""
    limit = max(1, min(limit, POPULARITY_CHART_SIZE))
    client = await get_http_client(request.app)
    try:
        chart = await fetch_most_played_games(client)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Steam 인기 차트 조회 실패: {exc}") from exc

    ranks = chart.get("ranks", [])
    name_map = await get_app_name_map(request.app, [e["appid"] for e in ranks[:limit] if e.get("appid")])
    today = datetime.now(KST).date()
    yesterday_ranks = await get_daily_rank_changes(request.app, today)

    rank_changes: dict[int, dict] = {}
    for entry in ranks:
        appid, rank = entry.get("appid"), entry.get("rank")
        if appid is None or rank is None or appid not in yesterday_ranks:
            continue
        change = compute_day_rank_change(rank, yesterday_ranks[appid])
        if change is not None:
            rank_changes[appid] = change

    items = build_popularity_items(ranks, name_map, limit, rank_changes)
    return {
        "status": "ok",
        "data_source": "STEAM_API",
        "rollup_date": chart.get("rollup_date"),
        "comparison_basis": "day" if rank_changes else "week",
        "total_available": len(ranks),
        "items": items,
    }


@app.get("/api/trends/popularity/search")
async def trends_popularity_search(request: Request, q: str) -> dict:
    """인기 탭 검색 — TOP 100 목록 안에서만 찾는다.

    Steam은 TOP 100 밖 게임의 순위를 계산할 방법을 제공하지 않으므로(전체
    앱을 폴링하는 건 비현실적), 목록에 없으면 "찾을 수 없음"만 정직하게
    돌려주고 다른 대체 정보(단일 동접자 수 등)로 채우지 않는다.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="검색어를 입력해 주세요.")

    client = await get_http_client(request.app)
    try:
        chart = await fetch_most_played_games(client)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Steam 인기 차트 조회 실패: {exc}") from exc

    ranks = chart.get("ranks", [])
    name_map = await get_app_name_map(request.app, [e["appid"] for e in ranks if e.get("appid")])
    items = build_popularity_items(ranks, name_map, limit=len(ranks))

    match = None
    if q.isdigit():
        target_appid = int(q)
        match = next((it for it in items if it["appid"] == target_appid), None)
    if match is None:
        q_lower = q.lower()
        match = next((it for it in items if q_lower in it["name"].lower()), None)

    if match is None:
        return {
            "status": "not_found",
            "query": q,
            "total_available": len(ranks),
            "message": "TOP 100 밖의 게임입니다 — 현재 Steam 동시 접속자 상위 100위 안에서만 검색할 수 있어요.",
        }
    return {"status": "ok", "query": q, "item": match, "total_available": len(ranks)}


@app.get("/api/trends/genres")
async def trends_genres(request: Request) -> dict:
    """장르 필터 드롭다운 — 누적판매순/할인제품/최신작 3개 탭에서만 쓸 수 있다.

    (인기/인기순은 GetMostPlayedGames 자체에 장르 정보가 없어 필터를 지원하지 않는다.)
    """
    if trend_db_configured():
        try:
            pool = await get_db_pool(request.app)
            await ensure_trend_tables(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("SELECT tag_id, name FROM genre_tag ORDER BY name ASC")
                rows = await cursor.fetchall()
            if rows:
                return {"status": "ok", "data_source": "CACHE", "genres": rows}
        except Exception:
            pass

    client = await get_http_client(request.app)
    try:
        genres = await fetch_genre_tags_live(client)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"장르 목록 조회 실패: {exc}") from exc
    genres.sort(key=lambda g: g["name"])
    return {"status": "ok", "data_source": "STEAM_API", "genres": genres}


@app.get("/api/trends/list")
async def trends_list(request: Request, category: str, tag_id: int = 0, offset: int = 0, limit: int = 20) -> dict:
    """누적 판매순 / 할인 제품 / 최신작 탭 — 더보기+로 최대 100개까지 페이지네이션."""
    if category not in TREND_LIST_CATEGORIES:
        raise HTTPException(
            status_code=400, detail=f"알 수 없는 category입니다: {category} (허용: {list(TREND_LIST_CATEGORIES)})"
        )
    offset = max(0, offset)
    limit = max(1, min(limit, TREND_LIST_MAX_LIMIT))
    if offset >= TREND_LIST_MAX_LIMIT:
        return {
            "status": "ok",
            "data_source": "NONE",
            "category": category,
            "tag_id": tag_id,
            "items": [],
            "total_available": TREND_LIST_MAX_LIMIT,
        }
    limit = min(limit, TREND_LIST_MAX_LIMIT - offset)

    cached = await get_cached_trend_list(request.app, category, tag_id, offset, limit)
    if cached is not None:
        return {"status": "ok", "data_source": "CACHE", "category": category, "tag_id": tag_id, **cached}

    client = await get_http_client(request.app)
    try:
        live = await fetch_trend_list_live(client, category, tag_id, offset, limit)
        return {"status": "ok", "data_source": "LIVE_SCRAPE", "category": category, "tag_id": tag_id, **live}
    except (httpx.HTTPError, ValueError):
        if tag_id:
            # 장르 필터가 걸린 목록은 featuredcategories가 장르를 지원하지 않아 대체할 수 없다 —
            # 가짜로 채우는 대신 실패를 있는 그대로 알린다.
            return {
                "status": "degraded",
                "data_source": "NONE",
                "category": category,
                "tag_id": tag_id,
                "items": [],
                "total_available": 0,
                "message": "이 장르 필터 목록을 지금 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.",
            }
        try:
            fallback = await fetch_featured_categories_fallback(client, category)
            return {
                "status": "ok",
                "data_source": "FEATURED_FALLBACK",
                "category": category,
                "tag_id": 0,
                **fallback,
            }
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"Steam 스토어 목록 조회 실패: {exc}") from exc


@app.get("/api/trends/news")
async def trends_news(request: Request, limit: int = 20) -> dict:
    """뉴스 · 패치 탭 — 인기 상위 게임들의 뉴스를 발행일순으로 병합."""
    limit = max(1, min(limit, NEWS_MAX_LIMIT))

    if trend_db_configured():
        try:
            pool = await get_db_pool(request.app)
            await ensure_trend_tables(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT n.appid, n.gid, n.title, n.url, n.contents_snippet, n.published_at, "
                    "COALESCE(m.name, CONCAT('App ', n.appid)) AS game_name "
                    "FROM trend_news_cache n LEFT JOIN game_metadata m ON m.appid = n.appid "
                    "ORDER BY n.published_at DESC LIMIT %s",
                    (limit,),
                )
                rows = await cursor.fetchall()
            if rows:
                for row in rows:
                    row["published_at"] = int(row["published_at"].timestamp())
                return {"status": "ok", "data_source": "CACHE", "items": rows}
        except Exception:
            pass

    client = await get_http_client(request.app)
    try:
        items = await fetch_merged_news_live(client, request.app, limit)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"뉴스 조회 실패: {exc}") from exc
    return {"status": "ok", "data_source": "STEAM_API", "items": items}


@app.post("/internal/jobs/refresh-trends")
async def internal_refresh_trends(request: Request) -> dict:
    """일별 스냅샷/캐시 배치 수동·외부 트리거용.

    nginx.conf의 /api/ 프록시 규칙 밖이라 웹 퍼블릭 경로로는 도달하지 않지만,
    방어적으로 토큰도 검증한다. steam_insight_cd에 CronJob이 생기면 이 엔드포인트를
    그대로 호출하도록 이관하면 된다 (로직 재작성 불필요).
    """
    expected_token = os.getenv("INTERNAL_JOB_TOKEN")
    if not expected_token:
        raise HTTPException(status_code=503, detail="INTERNAL_JOB_TOKEN이 설정되지 않았습니다.")
    if request.headers.get(INTERNAL_JOB_TOKEN_HEADER) != expected_token:
        raise HTTPException(status_code=401, detail="유효하지 않은 내부 작업 토큰입니다.")
    if not trend_db_configured():
        raise HTTPException(status_code=503, detail="DB가 연동되어 있지 않아 배치를 실행할 수 없습니다.")

    try:
        return await refresh_trend_snapshot(request.app)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"배치 실행 실패: {exc}") from exc
