import os
import socket
import random
import asyncio
import math
from contextlib import closing
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException

from db import db_connection, init_db_tables
from steam_api import (
    fetch_steam_public_xml,
    get_steam_api_data,
    generate_mock_user_data,
    fetch_app_news,
    clean_news_summary,
    PLAYSTYLES,
    INSIGHTS,
)
from collector import chart_collection_loop

KST_OFFSET = timedelta(hours=9)  # 한국은 DST 없이 UTC+9 고정이라 오프셋 상수로 충분함

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


@app.on_event("startup")
async def start_chart_scheduler():
    asyncio.create_task(chart_collection_loop())


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
