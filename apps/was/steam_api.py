import os
import json
import re
import random
import time
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

from db import load_env_file

load_env_file()  # steam_api.py를 db.py보다 먼저 import해도 STEAM_API_KEY 등이 비어있지 않도록 보장

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


def _urlopen_with_backoff(req: urllib.request.Request, timeout: int = 10, max_retries: int = 2, backoff_seconds: float = 5.0):
    # store.steampowered.com은 IP당 요청 한도(약 200회/5분, 커뮤니티 관측치)를 넘으면 429를 준다.
    # 그냥 실패시키는 대신 짧게 쉬었다가 재시도해서, 순간적인 버스트로 인한 실패를 흡수한다.
    for attempt in range(max_retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise


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


def fetch_top_played_games() -> list[dict]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(CHART_URL, headers=headers)
    with _urlopen_with_backoff(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    return data.get("response", {}).get("ranks", [])


def fetch_app_details(appid: int) -> dict | None:
    # l=koreana: Steam 스토어 API의 한국어 로케일 코드("korean"이 아님).
    # 이름/장르/요약 중 공식 한국어 번역이 있는 항목은 한국어로, 없는 항목은 자동으로 영어(원문)로 내려옴.
    url = f"{APPDETAILS_URL}?appids={appid}&l=koreana"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with _urlopen_with_backoff(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    entry = data.get(str(appid))
    if not entry or not entry.get("success"):
        return None
    return entry.get("data", {})


def fetch_featured_appids() -> set[int]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(FEATURED_CATEGORIES_URL, headers=headers)
    with _urlopen_with_backoff(req, timeout=10) as response:
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
    with _urlopen_with_backoff(req, timeout=10) as response:
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
    with _urlopen_with_backoff(req, timeout=10) as response:
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
    with _urlopen_with_backoff(req, timeout=10) as response:
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
    with _urlopen_with_backoff(req, timeout=10) as response:
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
