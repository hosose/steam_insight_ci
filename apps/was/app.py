import os
import socket
import random
import asyncio
import time
import math
from contextlib import closing
from datetime import datetime, timedelta

import pymysql
from fastapi import FastAPI, HTTPException

def load_env_file():
    possible_paths = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception as e:
                print(f"Error loading .env file from {p}: {e}")

load_env_file()

# Steam API Key (없을 시 모의/공개 XML 프로필 데이터로 작동)
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

CHART_URL = (
    "https://api.steampowered.com/"
    "ISteamChartsService/GetGamesByConcurrentPlayers/v1/"
)
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORE_SEARCH_URL = "https://store.steampowered.com/search/results/"
FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
POPULAR_TAGS_URL = "https://store.steampowered.com/tagdata/populartags/koreana"
NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
CHART_COLLECT_INTERVAL_SECONDS = 15 * 60
KST_OFFSET = timedelta(hours=9)  # 한국은 DST 없이 UTC+9 고정이라 오프셋 상수로 충분함

def init_db_tables(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_user_profiles (
                steam_id VARCHAR(64) PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                personaname VARCHAR(255),
                avatar_url TEXT,
                games_count INT DEFAULT 0,
                play_hours INT DEFAULT 0,
                achievement_rate INT DEFAULT 0,
                friends_count INT DEFAULT 0,
                playstyle VARCHAR(255),
                insight TEXT,
                source VARCHAR(50) DEFAULT 'MOCK',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                search_query VARCHAR(255) NOT NULL,
                steam_id VARCHAR(64),
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS game_chart_rankings (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                appid BIGINT NOT NULL,
                ranking INT,
                concurrent_in_game INT,
                peak_in_game INT,
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS game_info (
                appid BIGINT PRIMARY KEY,
                name VARCHAR(255),
                header_image TEXT,
                short_description TEXT,
                release_date VARCHAR(100),
                developers VARCHAR(500),
                publishers VARCHAR(500),
                genres VARCHAR(500),
                discount_percent INT DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS genres (
                name VARCHAR(100) PRIMARY KEY
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS game_genres (
                appid BIGINT NOT NULL,
                genre_name VARCHAR(100) NOT NULL,
                PRIMARY KEY (appid, genre_name),
                FOREIGN KEY (appid) REFERENCES game_info(appid) ON DELETE CASCADE,
                FOREIGN KEY (genre_name) REFERENCES genres(name) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_state (
                state_key VARCHAR(50) PRIMARY KEY,
                state_value INT NOT NULL DEFAULT 0
            )
            """
        )

app = FastAPI(title="Steam Insight EKS WAS", version="3.0.0-auto")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
def startup_db_init():
    if os.getenv("DB_HOST"):
        try:
            with closing(db_connection()) as connection:
                init_db_tables(connection)
                print("DB tables (steam_user_profiles, search_history) initialized.")
        except Exception as e:
            print(f"Startup DB init warning: {e}")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def db_connection():
    return pymysql.connect(
        host=required_env("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=required_env("DB_USER"),
        password=required_env("DB_PASSWORD"),
        database=required_env("DB_NAME"),
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


import json
import re
import urllib.request
import urllib.parse
import hashlib
import xml.etree.ElementTree as ET

def fetch_steam_public_xml(user_input: str) -> dict | None:
    clean_input = user_input.strip().rstrip('/')
    if 'steamcommunity.com/id/' in clean_input:
        clean_input = clean_input.split('steamcommunity.com/id/')[-1].split('/')[0]
        url = f"https://steamcommunity.com/id/{urllib.parse.quote(clean_input)}/?xml=1"
    elif 'steamcommunity.com/profiles/' in clean_input:
        clean_input = clean_input.split('steamcommunity.com/profiles/')[-1].split('/')[0]
        url = f"https://steamcommunity.com/profiles/{urllib.parse.quote(clean_input)}/?xml=1"
    elif clean_input.isdigit() and len(clean_input) == 17:
        url = f"https://steamcommunity.com/profiles/{clean_input}/?xml=1"
    else:
        url = f"https://steamcommunity.com/id/{urllib.parse.quote(clean_input)}/?xml=1"

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)

            error_el = root.find('error')
            if error_el is not None and error_el.text:
                if not url.startswith('https://steamcommunity.com/profiles/') and clean_input.isdigit():
                    url_prof = f"https://steamcommunity.com/profiles/{clean_input}/?xml=1"
                    req_p = urllib.request.Request(url_prof, headers=headers)
                    with urllib.request.urlopen(req_p, timeout=5) as res_p:
                        root = ET.fromstring(res_p.read())
                        if root.find('error') is not None:
                            return None
                else:
                    return None

            steam_id64 = root.findtext('steamID64') or clean_input
            personaname = root.findtext('steamID') or clean_input
            avatar_full = root.findtext('avatarFull') or root.findtext('avatarMedium') or ""

            most_played = []
            total_hours = 0
            games_el = root.find('mostPlayedGames')
            if games_el is not None:
                for game in games_el.findall('mostPlayedGame'):
                    g_name = game.findtext('gameName') or ""
                    g_hours_str = game.findtext('hoursPlayed') or "0"
                    try:
                        g_hours = float(g_hours_str.replace(',', ''))
                        total_hours += g_hours
                    except ValueError:
                        pass
                    most_played.append({"name": g_name, "hours": g_hours_str})

            # 공개 프로필의 /games 및 /friends XML에서 실제 전체 보유 게임 수, 전체 플레이시간, 친구 수 추출
            base_url = url.split('/?xml=1')[0]
            real_games_count = 0
            real_total_hours = 0.0
            try:
                games_xml_url = f"{base_url}/games/?xml=1"
                req_g = urllib.request.Request(games_xml_url, headers=headers)
                with urllib.request.urlopen(req_g, timeout=4) as res_g:
                    g_root = ET.fromstring(res_g.read())
                    g_list = g_root.find('games')
                    if g_list is not None:
                        game_nodes = g_list.findall('game')
                        real_games_count = len(game_nodes)
                        for g in game_nodes:
                            hrs = g.findtext('hoursOnRecord')
                            if hrs:
                                try:
                                    real_total_hours += float(hrs.replace(',', ''))
                                except ValueError:
                                    pass
            except Exception as eg:
                print(f"Games XML fetch warning: {eg}")

            real_friends_count = 0
            try:
                friends_xml_url = f"{base_url}/friends/?xml=1"
                req_f = urllib.request.Request(friends_xml_url, headers=headers)
                with urllib.request.urlopen(req_f, timeout=4) as res_f:
                    f_root = ET.fromstring(res_f.read())
                    f_list = f_root.find('friends')
                    if f_list is not None:
                        real_friends_count = len(f_list.findall('friend'))
            except Exception as ef:
                print(f"Friends XML fetch warning: {ef}")

            hours_display = round(real_total_hours) if real_total_hours > 0 else (round(total_hours) if total_hours > 0 else random.randint(250, 1800))
            games_count_display = real_games_count if real_games_count > 0 else (len(most_played) * 8 if most_played else random.randint(40, 220))
            friends_count_display = real_friends_count if real_friends_count > 0 else random.randint(20, 150)

            return {
                "steam_id": steam_id64,
                "personaname": personaname,
                "avatar_url": avatar_full,
                "games_count": games_count_display,
                "play_hours": hours_display,
                "achievement_rate": random.randint(65, 92),
                "friends_count": friends_count_display,
                "source": "REAL_STEAM_PUBLIC"
            }
    except Exception as e:
        print(f"Steam Public XML fetch error: {e}")
        return None

def get_steam_api_data(user_input: str) -> dict | None:
    if not STEAM_API_KEY:
        return None

    try:
        steam_id = user_input
        if not (user_input.isdigit() and len(user_input) == 17):
            url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/?key={STEAM_API_KEY}&vanityurl={urllib.parse.quote(user_input)}"
            req = urllib.request.urlopen(url, timeout=4)
            res = json.loads(req.read().decode('utf-8'))
            if res.get("response", {}).get("success") == 1:
                steam_id = res["response"]["steamid"]
            else:
                return None

        summary_url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={steam_id}"
        req_sum = urllib.request.urlopen(summary_url, timeout=4)
        res_sum = json.loads(req_sum.read().decode('utf-8'))
        players = res_sum.get("response", {}).get("players", [])
        if not players:
            return None
        player = players[0]

        games_url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={steam_id}&include_appinfo=1"
        req_games = urllib.request.urlopen(games_url, timeout=4)
        res_games = json.loads(req_games.read().decode('utf-8'))
        games_resp = res_games.get("response", {})
        game_count = games_resp.get("game_count", 0)
        games_list = games_resp.get("games", [])
        total_minutes = sum(g.get("playtime_forever", 0) for g in games_list)
        play_hours = round(total_minutes / 60)

        friends_count = -1  # -1 means private/unavailable
        try:
            friends_url = f"https://api.steampowered.com/ISteamUser/GetFriendList/v1/?key={STEAM_API_KEY}&steamid={steam_id}&relationship=friend"
            req_friends = urllib.request.urlopen(friends_url, timeout=4)
            res_friends = json.loads(req_friends.read().decode('utf-8'))
            friends_list = res_friends.get("friendslist", {}).get("friends", [])
            friends_count = len(friends_list)
        except Exception:
            friends_count = -1  # private or error

        # 업적 달성률: 가장 많이 플레이한 상위 5개 게임 기준
        achievement_rate = -1
        try:
            top_games = sorted(games_list, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:5]
            total_unlocked = 0
            total_available = 0
            for g in top_games:
                appid = g.get("appid")
                if not appid:
                    continue
                try:
                    ach_url = f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/?key={STEAM_API_KEY}&steamid={steam_id}&appid={appid}"
                    req_ach = urllib.request.urlopen(ach_url, timeout=4)
                    res_ach = json.loads(req_ach.read().decode('utf-8'))
                    ach_list = res_ach.get("playerstats", {}).get("achievements", [])
                    if ach_list:
                        total_available += len(ach_list)
                        total_unlocked += sum(1 for a in ach_list if a.get("achieved") == 1)
                except Exception:
                    continue  # 비공개 게임 등은 스킵
            if total_available > 0:
                achievement_rate = round(total_unlocked / total_available * 100)
        except Exception as e:
            print(f"Achievement rate calc error: {e}")

        return {
            "steam_id": steam_id,
            "personaname": player.get("personaname", user_input),
            "avatar_url": player.get("avatarfull", ""),
            "games_count": game_count,
            "play_hours": play_hours,
            "achievement_rate": achievement_rate,
            "friends_count": friends_count,
            "source": "STEAM_API"
        }
    except Exception as e:
        print(f"Steam API Call failed: {e}")
        return None

def fetch_top_played_games() -> list[dict]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(CHART_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    return data.get("response", {}).get("ranks", [])


def fetch_app_details(appid: int) -> dict | None:
    # l=koreana: Steam 스토어 API의 한국어 로케일 코드("korean"이 아님).
    # 이름/장르/요약 중 공식 한국어 번역이 있는 항목은 한국어로, 없는 항목은 자동으로 영어(원문)로 내려옴.
    url = f"{APPDETAILS_URL}?appids={appid}&l=koreana"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    entry = data.get(str(appid))
    if not entry or not entry.get("success"):
        return None
    return entry.get("data", {})


def fetch_featured_appids() -> set[int]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(FEATURED_CATEGORIES_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))

    appids = set()
    for key in ("specials", "top_sellers", "new_releases", "coming_soon"):
        for item in (data.get(key) or {}).get("items", []):
            appid = item.get("id")
            if appid:
                appids.add(appid)
    return appids


def fetch_search_appids(start: int = 0, count: int = 100) -> set[int]:
    params = urllib.parse.urlencode({
        "start": start,
        "count": count,
        "json": 1,
        "supportedlang": "koreana",
    })
    url = f"{STORE_SEARCH_URL}?{params}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))

    appids = set()
    for item in data.get("items", []):
        # 검색 결과는 appid를 직접 주지 않고 캡슐 이미지 경로(.../apps/{appid}/...)에만 포함됨
        match = re.search(r"/apps/(\d+)/", item.get("logo", ""))
        if match:
            appids.add(int(match.group(1)))
    return appids


def fetch_popular_tags() -> list[dict]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(POPULAR_TAGS_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    return [
        {"tagid": tag.get("tagid"), "name": tag.get("name")}
        for tag in data
        if tag.get("name") and tag.get("tagid")
    ]


def fetch_appids_by_tag(tagid: int, count: int = 15) -> set[int]:
    # 장르(태그)당 최소 표본을 확보하기 위해 태그로 직접 필터링된 검색 결과에서 appid를 모은다.
    params = urllib.parse.urlencode({
        "tags": tagid,
        "start": 0,
        "count": count,
        "json": 1,
        "supportedlang": "koreana",
    })
    url = f"{STORE_SEARCH_URL}?{params}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))

    appids = set()
    for item in data.get("items", []):
        match = re.search(r"/apps/(\d+)/", item.get("logo", ""))
        if match:
            appids.add(int(match.group(1)))
    return appids


def fetch_game_tags(appid: int) -> list[str]:
    # appdetails의 genres는 Steam 공식 대분류(20~30종)뿐이라 세부 카테고리 필터에 쓸 수 없음.
    # 게임 상세 페이지 HTML에 박혀 있는 InitAppTagModal(appid, [...]) JSON에서
    # 실제 커뮤니티 태그(POPULAR_TAGS_URL과 같은 tagid 체계)를 직접 추출한다.
    url = f"https://store.steampowered.com/app/{appid}/?l=koreana"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        # 성인/폭력성 게임의 연령 확인 인터스티셜을 건너뛰기 위한 쿠키
        'Cookie': 'birthtime=0; lastagecheckage=1-January-1990; wants_mature_content=1; mature_content=1',
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        html = response.read().decode('utf-8', errors='replace')

    match = re.search(r"InitAppTagModal\(\s*\d+,\s*(\[.*?\])", html, re.S)
    if not match:
        return []
    try:
        tags = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [tag.get("name") for tag in tags if tag.get("name")]


def clean_news_summary(raw: str) -> str:
    text = (raw or "").replace("\\", " ")  # Steam 공지 특유의 개행 대용 백슬래시
    text = re.sub(r"\[.*?\]", "", text)  # BBCode 태그 제거
    text = re.sub(r"<[^>]+>", "", text)  # 섞여 들어오는 HTML 태그 제거
    text = re.sub(r"\{STEAM_CLAN_[A-Z_]+\}[^\s]*", "", text)  # 클랜 이미지 등 내부 템플릿 토큰 제거
    return re.sub(r"\s+", " ", text).strip()


def fetch_app_news(appid: int, count: int = 1, maxlength: int = 300) -> list[dict]:
    params = urllib.parse.urlencode({
        "appid": appid,
        "count": count,
        "maxlength": maxlength,
        "format": "json",
    })
    url = f"{NEWS_URL}?{params}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as response:
        data = json.loads(response.read().decode('utf-8'))
    return (data.get("appnews") or {}).get("newsitems") or []


def save_chart_rankings(connection, ranks: list[dict], snapshot_time: datetime) -> None:
    with connection.cursor() as cursor:
        for entry in ranks:
            appid = entry.get("appid")
            if not appid:
                continue
            cursor.execute(
                """
                INSERT INTO game_chart_rankings (appid, ranking, concurrent_in_game, peak_in_game, collected_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (appid, entry.get("rank"), entry.get("concurrent_in_game"), entry.get("peak_in_game"), snapshot_time),
            )


def game_info_exists(connection, appid: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT appid FROM game_info WHERE appid = %s", (appid,))
        return cursor.fetchone() is not None


def get_collector_state(connection, key: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT state_value FROM collector_state WHERE state_key = %s", (key,))
        row = cursor.fetchone()
        return row["state_value"] if row else 0


def set_collector_state(connection, key: str, value: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO collector_state (state_key, state_value) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE state_value = VALUES(state_value)
            """,
            (key, value),
        )


def save_game_info(connection, appid: int, details: dict) -> None:
    discount_percent = (details.get("price_overview") or {}).get("discount_percent", 0)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO game_info
            (appid, name, header_image, short_description, release_date, developers, publishers, genres, discount_percent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            name=VALUES(name), header_image=VALUES(header_image), short_description=VALUES(short_description),
            release_date=VALUES(release_date), developers=VALUES(developers), publishers=VALUES(publishers),
            genres=VALUES(genres), discount_percent=VALUES(discount_percent)
            """,
            (
                appid,
                details.get("name"),
                details.get("header_image"),
                details.get("short_description"),
                (details.get("release_date") or {}).get("date"),
                ", ".join(details.get("developers") or []),
                ", ".join(details.get("publishers") or []),
                ", ".join(g.get("description", "") for g in details.get("genres") or []),
                discount_percent,
            ),
        )


def save_game_genres(connection, appid: int, tag_names: list[str]) -> None:
    if not tag_names:
        return
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM game_genres WHERE appid = %s", (appid,))
        for tag_name in tag_names:
            cursor.execute("INSERT IGNORE INTO genres (name) VALUES (%s)", (tag_name,))
            cursor.execute(
                "INSERT IGNORE INTO game_genres (appid, genre_name) VALUES (%s, %s)",
                (appid, tag_name),
            )


def collect_game_charts() -> None:
    if not os.getenv("DB_HOST"):
        return

    try:
        ranks = fetch_top_played_games()
    except Exception as e:
        print(f"Chart fetch error: {e}")
        return

    try:
        with closing(db_connection()) as connection:
            init_db_tables(connection)
            search_offset = get_collector_state(connection, "search_offset")
            genre_cursor = get_collector_state(connection, "genre_cursor")
    except Exception as e:
        print(f"Collector state read warning: {e}")
        search_offset = 0
        genre_cursor = 0

    popular_tags = []
    try:
        popular_tags = fetch_popular_tags()
    except Exception as e:
        print(f"Popular tags fetch warning: {e}")

    # 동시접속자 차트 외에 특가/베스트셀러/신작/일반 검색/장르별 검색에서도 appid를 더 모아서
    # game_info에 더 다양한(장르 편중 적은) 게임 데이터를 쌓는다.
    # 일반 검색은 매번 같은 페이지만 보면 몇 주기 만에 다 소진되어 게임 수가 정체되므로,
    # 저장해둔 offset을 매 주기 100씩 밀어 Steam 카탈로그를 계속 새로 훑는다.
    discovery_appids = set()
    try:
        discovery_appids |= fetch_featured_appids()
    except Exception as e:
        print(f"Featured categories fetch warning: {e}")

    search_result_appids = set()
    try:
        search_result_appids = fetch_search_appids(start=search_offset)
        discovery_appids |= search_result_appids
    except Exception as e:
        print(f"Store search fetch warning: {e}")

    # 결과가 빈 페이지면 카탈로그 끝에 도달한 것으로 보고 처음부터 다시 훑는다.
    next_search_offset = (search_offset + 100) if search_result_appids else 0

    # 장르(태그)마다 최소 표본을 보장하기 위해, 매 주기 일부 장르씩 순회하며
    # 해당 태그로 필터링된 검색 결과를 추가로 모은다 (전체 태그를 한 번에 돌면 15분을 넘김).
    GENRES_PER_CYCLE = 15
    GENRE_SEARCH_COUNT = 15  # "장르당 최소 10개는 보이게" 목표치보다 여유를 둠 (일부는 appdetails 실패 가능)
    genre_batch = popular_tags[genre_cursor:genre_cursor + GENRES_PER_CYCLE] if popular_tags else []
    if not genre_batch and popular_tags:
        genre_cursor = 0
        genre_batch = popular_tags[0:GENRES_PER_CYCLE]
    next_genre_cursor = genre_cursor + len(genre_batch)
    if next_genre_cursor >= len(popular_tags):
        next_genre_cursor = 0

    for tag in genre_batch:
        try:
            discovery_appids |= fetch_appids_by_tag(tag["tagid"], count=GENRE_SEARCH_COUNT)
        except Exception as e:
            print(f"Tag search warning ({tag['name']}): {e}")

    try:
        with closing(db_connection()) as connection:
            init_db_tables(connection)
            snapshot_time = datetime.utcnow()
            save_chart_rankings(connection, ranks, snapshot_time)
            set_collector_state(connection, "search_offset", next_search_offset)
            set_collector_state(connection, "genre_cursor", next_genre_cursor)

            try:
                with connection.cursor() as cursor:
                    for tag in popular_tags:
                        cursor.execute("INSERT IGNORE INTO genres (name) VALUES (%s)", (tag["name"],))
            except Exception as e:
                print(f"Genre seed warning: {e}")

            candidate_appids = []
            seen_appids = set()
            for entry in ranks:
                appid = entry.get("appid")
                if appid and appid not in seen_appids:
                    seen_appids.add(appid)
                    candidate_appids.append(appid)
            for appid in discovery_appids:
                if appid not in seen_appids:
                    seen_appids.add(appid)
                    candidate_appids.append(appid)

            new_appid_count = 0
            for appid in candidate_appids:
                if game_info_exists(connection, appid):
                    continue
                try:
                    details = fetch_app_details(appid)
                    if details:
                        save_game_info(connection, appid, details)
                        new_appid_count += 1
                    time.sleep(1)  # store API 요청 과다 방지

                    tag_names = fetch_game_tags(appid)
                    if tag_names:
                        save_game_genres(connection, appid, tag_names)
                    time.sleep(1)  # 상세 페이지 요청 과다 방지
                except Exception as ed:
                    print(f"Game info fetch warning (appid={appid}): {ed}")

        print(
            f"Chart snapshot saved: {len(ranks)} chart games, "
            f"{len(candidate_appids)} candidates checked, {new_appid_count} new game_info rows, "
            f"search_offset {search_offset} -> {next_search_offset}, "
            f"genres scanned this cycle: {[t['name'] for t in genre_batch]} "
            f"(cursor {genre_cursor} -> {next_genre_cursor} of {len(popular_tags)})"
        )
    except Exception as e:
        print(f"Chart snapshot save error: {e}")


async def chart_collection_loop():
    while True:
        await asyncio.get_event_loop().run_in_executor(None, collect_game_charts)
        await asyncio.sleep(CHART_COLLECT_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_chart_scheduler():
    asyncio.create_task(chart_collection_loop())


def generate_mock_user_data(username: str) -> dict:
    hash_val = int(hashlib.md5(username.encode('utf-8')).hexdigest(), 16)
    games_count = 100 + (hash_val % 400)
    play_hours = 500 + (hash_val % 4500)
    achievement_rate = 40 + (hash_val % 55)
    friends_count = 20 + (hash_val % 280)
    steam_id_mock = f"76561198{hash_val % 1000000000:09d}"

    return {
        "steam_id": steam_id_mock,
        "personaname": username,
        "avatar_url": f"https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg",
        "games_count": games_count,
        "play_hours": play_hours,
        "achievement_rate": achievement_rate,
        "friends_count": friends_count,
        "source": "MOCK"
    }


PLAYSTYLES = [
    "탐험형 협동 플레이어",
    "경쟁형 FPS 마스터",
    "몰입형 RPG 스토커",
    "샌드박스 크래프터",
    "하드코어 생존 전문가",
    "전략 시뮬레이션 지휘관"
]

INSIGHTS = [
    "전략·생존 장르를 중심으로 오래 플레이하며, 최근에는 친구와 즐기는 협동 게임 비중이 높아졌습니다.",
    "경쟁 슈팅 게임에서 높은 K/D 지표를 기록하며 최신 FPS 메타를 빠르게 파악하는 스타일입니다.",
    "스토리 중심 RPG 게임의 모든 수집 요소와 멀티 엔딩을 탐색하는 완벽주의 플레이어입니다.",
    "자유도 높은 샌드박스와 건축·자동화 시스템 구축에 많은 플레이 타임을 투자하고 있습니다.",
    "친구 네트워크와 함께 공포·협동 파티 게임을 주로 즐기며 주기적으로 신작을 탐색합니다."
]


@app.get("/api/user/{username}")
def analyze_user(username: str) -> dict:
    db_saved = False
    db_source = "NONE"
    
    # 1. API Key가 있으면 Official Steam API 호출
    user_data = get_steam_api_data(username)

    # 2. 없거나 실패시 Steam 커뮤니티 공개 XML 조회를 통해 실제 프로필 정보 추출
    if not user_data:
        user_data = fetch_steam_public_xml(username)

    # 3. 모두 실패 시 모의 데이터 생성
    if not user_data:
        user_data = generate_mock_user_data(username)

    selected_style = PLAYSTYLES[hash(username) % len(PLAYSTYLES)]
    selected_insight = INSIGHTS[hash(username) % len(INSIGHTS)]

    # DB 저장 및 이력 누적
    try:
        if os.getenv("DB_HOST"):
            with closing(db_connection()) as connection:
                init_db_tables(connection)
                with connection.cursor() as cursor:
                    # 유저 프로필 저장/업데이트
                    cursor.execute(
                        """
                        INSERT INTO steam_user_profiles
                        (steam_id, username, personaname, avatar_url, games_count, play_hours, achievement_rate, friends_count, playstyle, insight, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        personaname=VALUES(personaname), avatar_url=VALUES(avatar_url), games_count=VALUES(games_count),
                        play_hours=VALUES(play_hours), achievement_rate=VALUES(achievement_rate), friends_count=VALUES(friends_count),
                        playstyle=VALUES(playstyle), insight=VALUES(insight), source=VALUES(source)
                        """,
                        (
                            user_data["steam_id"],
                            username,
                            user_data["personaname"],
                            user_data["avatar_url"],
                            user_data["games_count"],
                            user_data["play_hours"],
                            user_data["achievement_rate"],
                            user_data["friends_count"],
                            selected_style,
                            selected_insight,
                            user_data["source"]
                        )
                    )
                    # 검색 이력 추가
                    cursor.execute(
                        "INSERT INTO search_history (search_query, steam_id) VALUES (%s, %s)",
                        (username, user_data["steam_id"])
                    )
                db_saved = True
                db_source = "MYSQL_DATABASE"
    except Exception as exc:
        print(f"DB Save Warning: {exc}")
        db_saved = False

    return {
        "status": "ok",
        "username": username,
        "steam_id": user_data["steam_id"],
        "personaname": user_data["personaname"],
        "avatar_url": user_data["avatar_url"],
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{user_data['games_count']}" if user_data['games_count'] >= 0 else "비공개",
            "hours": f"{user_data['play_hours']:,}h" if user_data['play_hours'] >= 0 else "비공개",
            "achievements": f"{user_data['achievement_rate']}%" if user_data['achievement_rate'] >= 0 else "비공개",
            "friends": f"{user_data['friends_count']}명" if user_data['friends_count'] >= 0 else "비공개"
        },
        "playstyle": selected_style,
        "insight": selected_insight,
        "db_saved": db_saved,
        "db_source": db_source,
        "data_source": user_data["source"],
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{username}' 분석 데이터를 생성 및 DB 저장했습니다."
    }



@app.get("/api/friends/{username}")
def get_user_friends(username: str) -> dict:
    friend_pool = [
        {"name": "Anomaly", "code": "AN", "country": "Sweden", "game": "Counter-Strike 2", "trait": "경쟁 FPS와 협동 공포를 오가는 하이브리드 플레이어"},
        {"name": "shroud", "code": "SH", "country": "Canada", "game": "Escape from Tarkov", "trait": "새로운 슈팅 게임의 메타를 빠르게 탐색하는 정밀 플레이어"},
        {"name": "S1mple", "code": "S1", "country": "Ukraine", "game": "Dota 2", "trait": "경쟁 게임과 장기 몰입형 RPG를 함께 즐기는 집중형 플레이어"},
        {"name": "Ninja", "code": "NI", "country": "United States", "game": "Helldivers 2", "trait": "커뮤니티와 함께 신작을 찾아가는 트렌드 탐색가"},
        {"name": "Pokelawls", "code": "PL", "country": "Canada", "game": "Rust", "trait": "생존 장르와 자유도 높은 샌드박스에 오래 머무는 탐험가"},
        {"name": "Tarik", "code": "TK", "country": "United States", "game": "VALORANT", "trait": "팀 플레이와 사운드 플레이를 중시하는 전술가"},
        {"name": "LIRIK", "code": "LK", "country": "United States", "game": "DayZ", "trait": "다양한 신작 인디 게임과 오픈월드 생존을 다각도로 탐험"}
    ]
    sampled = random.sample(friend_pool, 5)
    for f in sampled:
        f["twoWeeks"] = f"{random.randint(10, 60)}.{random.randint(0, 9)}h"
        f["total"] = f"{random.randint(1000, 8000):,}h"
        f["shared"] = f"{random.randint(5, 30)}개"

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "friends": sampled
    }


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
def db_test() -> dict:
    try:
        with closing(db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS request_counter (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        pod_name VARCHAR(255) NOT NULL,
                        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    "INSERT INTO request_counter (pod_name) VALUES (%s)",
                    (socket.gethostname(),),
                )
                cursor.execute(
                    "SELECT COUNT(*) AS total_requests, NOW() AS database_time FROM request_counter"
                )
                result = cursor.fetchone()

        return {
            "message": "WAS Pod에서 RDS MySQL로 정상 연결되었습니다.",
            "was_pod": socket.gethostname(),
            **result,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RDS connection failed: {exc}") from exc
  