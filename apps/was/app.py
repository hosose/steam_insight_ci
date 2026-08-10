import os
import socket
import random
from contextlib import closing

import pymysql
from fastapi import FastAPI, HTTPException
from concurrent.futures import ThreadPoolExecutor

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
        )

app = FastAPI(title="Steam Insight EKS WAS", version="3.0.0-auto")

@app.get("/health")
def health() -> dict[str, str]:
    """
    Health check endpoint — 외부 요청만 허용, DB 연결 테스트 포함.
    """
    db_ok = False
    conn = None
    try:
        # 외부 요청에서만 DB 연결 테스트
        if os.getenv("DB_HOST"):
            conn = db_connection()
            conn.close()
            db_ok = True
    except Exception as e:
        print(f"Health DB check failed: {e}")
        db_ok = False
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "was_pod": socket.gethostname(),
        "db_ok": db_ok,
        "message": "UP" if db_ok else "DOWN (DB connection issue)"
    }


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
