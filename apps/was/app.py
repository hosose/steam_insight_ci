import asyncio
import json
import os
import random
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

def get_user_owned_appids(steam_id: str) -> set:
    """유저가 소유한 게임의 appid 목록을 set 형태로 반환"""
    if not STEAM_API_KEY or not steam_id:
        return set()
    try:
        url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={steam_id}"
        req = urllib.request.urlopen(url, timeout=3)
        res = json.loads(req.read().decode('utf-8'))
        games = res.get("response", {}).get("games", [])
        return {g["appid"] for g in games if "appid" in g}
    except Exception as e:
        print(f"Get owned appids error for {steam_id}: {e}")
        return set()

def fetch_friend_real_stats(friend_steam_id: str, owner_appids: set) -> dict:
    """
    친구 1명의 실제 Steam 지표 수집
    - 최근 2주 플레이 타임 및 대표 게임
    - 총 누적 플레이 시간
    - 함께 보유한 게임 수 (owner_appids와 교집합)
    - 실제 업적 달성률
    """
    stats = {
        "twoWeeks": "0h",
        "twoWeeks_minutes": 0,
        "total": "0h",
        "total_hours": 0,
        "shared": "0개",
        "shared_count": 0,
        "shared_games": [],
        "recent_games_list": [],
        "game": "Steam Game",
        "achievement": "비공개"
    }
    
    if not STEAM_API_KEY or not friend_steam_id:
        return stats

# 1. 최근 2주 플레이 타임 & 대표 게임 (GetRecentlyPlayedGames)
    try:
        recent_url = f"https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&count=5"
        req_rec = urllib.request.urlopen(recent_url, timeout=3)
        res_rec = json.loads(req_rec.read().decode('utf-8'))
        recent_games = res_rec.get("response", {}).get("games", [])
        
        if recent_games:
            total_2w_min = sum(g.get("playtime_2weeks", 0) for g in recent_games)
            stats["twoWeeks_minutes"] = total_2w_min
            stats["twoWeeks"] = f"{round(total_2w_min / 60, 1)}h"
            stats["game"] = recent_games[0].get("name", "Steam Game")
            stats["recent_games_list"] = [
                {
                    "name": g.get("name", "Steam Game"),
                    "hours": f"{round(g.get('playtime_2weeks', 0) / 60, 1)}h"
                }
                for g in recent_games[:3]
            ]
    except Exception as e:
        print(f"Fetch recent games error for {friend_steam_id}: {e}")

    # 2. 총 플레이 시간 & 보유 게임 교집합 (GetOwnedGames)
    try:
        games_url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&include_appinfo=1"
        req_g = urllib.request.urlopen(games_url, timeout=3)
        res_g = json.loads(req_g.read().decode('utf-8'))
        games_list = res_g.get("response", {}).get("games", [])
        
        if games_list:
            total_min = sum(g.get("playtime_forever", 0) for g in games_list)
            tot_hours = round(total_min / 60)
            stats["total_hours"] = tot_hours
            stats["total"] = f"{tot_hours:,}h"
            
            # appid: 게임이름 매핑 딕셔너리 생성
            friend_appids_map = {g["appid"]: g.get("name", "Steam Game") for g in games_list if "appid" in g}
            friend_appids = set(friend_appids_map.keys())
            
            if owner_appids:
                # 두 유저가 공통으로 가지고 있는 appid 교집합 추출
                shared_appids = owner_appids.intersection(friend_appids)
                stats["shared_count"] = len(shared_appids)
                stats["shared"] = f"{len(shared_appids)}개"
                
                # 교집합 게임 중 상위 4개 게임 이름을 리스트로 담기
                stats["shared_games"] = [friend_appids_map[aid] for aid in list(shared_appids)[:4] if aid in friend_appids_map]

            # 3. 실제 업적 달성률 계산 (상위 3개 게임)
            top_games = sorted(games_list, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:3]
            unlocked_cnt = 0
            avail_cnt = 0
            for g in top_games:
                appid = g.get("appid")
                if not appid:
                    continue
                try:
                    ach_url = f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&appid={appid}"
                    req_ach = urllib.request.urlopen(ach_url, timeout=3)
                    res_ach = json.loads(req_ach.read().decode('utf-8'))
                    ach_list = res_ach.get("playerstats", {}).get("achievements", [])
                    if ach_list:
                        avail_cnt += len(ach_list)
                        unlocked_cnt += sum(1 for a in ach_list if a.get("achieved") == 1)
                except Exception:
                    continue
            if avail_cnt > 0:
                stats["achievement"] = f"{round(unlocked_cnt / avail_cnt * 100)}%"

    except Exception as e:
        print(f"Steam API Call failed: {e}")
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

def get_user_owned_appids(steam_id: str) -> set:
    """유저가 소유한 게임의 appid 목록을 set 형태로 반환"""
    if not STEAM_API_KEY or not steam_id:
        return set()
    try:
        url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={steam_id}"
        req = urllib.request.urlopen(url, timeout=3)
        res = json.loads(req.read().decode('utf-8'))
        games = res.get("response", {}).get("games", [])
        return {g["appid"] for g in games if "appid" in g}
    except Exception as e:
        print(f"Get owned appids error for {steam_id}: {e}")
        return set()

def fetch_friend_real_stats(friend_steam_id: str, owner_appids: set) -> dict:
    """
    친구 1명의 실제 Steam 지표 수집
    - 최근 2주 플레이 타임 및 대표 게임
    - 총 누적 플레이 시간
    - 함께 보유한 게임 수 (owner_appids와 교집합)
    - 실제 업적 달성률
    """
    stats = {
        "twoWeeks": "0h",
        "twoWeeks_minutes": 0,
        "total": "0h",
        "total_hours": 0,
        "shared": "0개",
        "shared_count": 0,
        "shared_games": [],
        "recent_games_list": [],
        "game": "Steam Game",
        "achievement": "비공개"
    }
    
    if not STEAM_API_KEY or not friend_steam_id:
        return stats

# 1. 최근 2주 플레이 타임 & 대표 게임 (GetRecentlyPlayedGames)
    try:
        recent_url = f"https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&count=5"
        req_rec = urllib.request.urlopen(recent_url, timeout=3)
        res_rec = json.loads(req_rec.read().decode('utf-8'))
        recent_games = res_rec.get("response", {}).get("games", [])
        
        if recent_games:
            total_2w_min = sum(g.get("playtime_2weeks", 0) for g in recent_games)
            stats["twoWeeks_minutes"] = total_2w_min
            stats["twoWeeks"] = f"{round(total_2w_min / 60, 1)}h"
            stats["game"] = recent_games[0].get("name", "Steam Game")
            stats["recent_games_list"] = [
                {
                    "name": g.get("name", "Steam Game"),
                    "hours": f"{round(g.get('playtime_2weeks', 0) / 60, 1)}h"
                }
                for g in recent_games[:3]
            ]
    except Exception as e:
        print(f"Fetch recent games error for {friend_steam_id}: {e}")

    # 2. 총 플레이 시간 & 보유 게임 교집합 (GetOwnedGames)
    try:
        games_url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&include_appinfo=1"
        req_g = urllib.request.urlopen(games_url, timeout=3)
        res_g = json.loads(req_g.read().decode('utf-8'))
        games_list = res_g.get("response", {}).get("games", [])
        
        if games_list:
            total_min = sum(g.get("playtime_forever", 0) for g in games_list)
            tot_hours = round(total_min / 60)
            stats["total_hours"] = tot_hours
            stats["total"] = f"{tot_hours:,}h"
            
            # appid: 게임이름 매핑 딕셔너리 생성
            friend_appids_map = {g["appid"]: g.get("name", "Steam Game") for g in games_list if "appid" in g}
            friend_appids = set(friend_appids_map.keys())
            
            if owner_appids:
                # 두 유저가 공통으로 가지고 있는 appid 교집합 추출
                shared_appids = owner_appids.intersection(friend_appids)
                stats["shared_count"] = len(shared_appids)
                stats["shared"] = f"{len(shared_appids)}개"
                
                # 교집합 게임 중 상위 4개 게임 이름을 리스트로 담기
                stats["shared_games"] = [friend_appids_map[aid] for aid in list(shared_appids)[:4] if aid in friend_appids_map]

            # 3. 실제 업적 달성률 계산 (상위 3개 게임)
            top_games = sorted(games_list, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:3]
            unlocked_cnt = 0
            avail_cnt = 0
            for g in top_games:
                appid = g.get("appid")
                if not appid:
                    continue
                try:
                    ach_url = f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/?key={STEAM_API_KEY}&steamid={friend_steam_id}&appid={appid}"
                    req_ach = urllib.request.urlopen(ach_url, timeout=3)
                    res_ach = json.loads(req_ach.read().decode('utf-8'))
                    ach_list = res_ach.get("playerstats", {}).get("achievements", [])
                    if ach_list:
                        avail_cnt += len(ach_list)
                        unlocked_cnt += sum(1 for a in ach_list if a.get("achieved") == 1)
                except Exception:
                    continue
            if avail_cnt > 0:
                stats["achievement"] = f"{round(unlocked_cnt / avail_cnt * 100)}%"

    except Exception as e:
        print(f"Fetch owned games error for {friend_steam_id}: {e}")

    return stats

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
def get_user_friends(username: str) -> dict:
    real_friends = []
    owner_steam_id = None

    # 1. 유저의 Steam ID64 조회
    if username.isdigit() and len(username) == 17:
        owner_steam_id = username
    elif STEAM_API_KEY:
        try:
            url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/?key={STEAM_API_KEY}&vanityurl={urllib.parse.quote(username)}"
            req = urllib.request.urlopen(url, timeout=4)
            res = json.loads(req.read().decode('utf-8'))
            if res.get("response", {}).get("success") == 1:
                owner_steam_id = res["response"]["steamid"]
        except Exception as e:
            print(f"Vanity URL resolve failed: {e}")

    # 2. 검색 유저(Owner)의 보유 게임 목록 계산 (함께할 게임 수 비교용)
    owner_appids = set()
    if owner_steam_id:
        owner_appids = get_user_owned_appids(owner_steam_id)

    # 3. 친구 목록 수집 및 실측 데이터 매핑
    if STEAM_API_KEY and owner_steam_id:
        try:
            friends_url = f"https://api.steampowered.com/ISteamUser/GetFriendList/v1/?key={STEAM_API_KEY}&steamid={owner_steam_id}&relationship=friend"
            req_f = urllib.request.urlopen(friends_url, timeout=4)
            res_f = json.loads(req_f.read().decode('utf-8'))
            friends_list = res_f.get("friendslist", {}).get("friends", [])[:100]

            if friends_list:
                friend_ids = [f["steamid"] for f in friends_list]
                ids_str = ",".join(friend_ids)
                
                summaries_url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={ids_str}"
                req_s = urllib.request.urlopen(summaries_url, timeout=4)
                res_s = json.loads(req_s.read().decode('utf-8'))
                players = res_s.get("response", {}).get("players", [])

            def process_player(p):
                    f_steam_id = p.get("steamid", "")
                    f_stats = fetch_friend_real_stats(f_steam_id, owner_appids)
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

            with ThreadPoolExecutor(max_workers=20) as executor:
                real_friends = list(executor.map(process_player, players))
        except Exception as e:
            print(f"Real Steam Friends API fetch failed: {e}")

    # 4. DB 저장
    if os.getenv("DB_HOST") and owner_steam_id and real_friends:
        try:
            with closing(db_connection()) as connection:
                with connection.cursor() as cursor:
                    for f in real_friends:
                        cursor.execute(
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
  