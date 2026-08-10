import os
import time
import asyncio
from contextlib import closing
from datetime import datetime

from db import db_connection, init_db_tables
from steam_api import (
    fetch_top_played_games,
    fetch_app_details,
    fetch_featured_appids,
    fetch_search_appids,
    fetch_popular_tags,
    fetch_appids_by_tag,
    fetch_game_tags,
)

CHART_COLLECT_INTERVAL_SECONDS = 15 * 60

# 장르(태그)당 최소 게임 수 보장 - 부족하면 "빈 장르 채우기" 부트스트랩 모드로 전환된다.
MIN_GAMES_PER_GENRE = 10
GENRE_SEARCH_COUNT = 15  # 장르당 최소 10개 목표치보다 여유를 둠 (일부는 appdetails 실패 가능)
GENRES_PER_CYCLE_STEADY = 15  # 평상시(모든 장르가 이미 채워진 뒤): 커서 순회 페이스
GENRES_PER_CYCLE_BOOTSTRAP = 40  # 콜드스타트: 안 채워진 장르를 몰아서 처리


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


def get_underfilled_genre_names(connection, min_games: int) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT g.name AS genre_name, COUNT(gg.appid) AS game_count
            FROM genres g
            LEFT JOIN game_genres gg ON gg.genre_name = g.name
            GROUP BY g.name
            HAVING game_count < %s
            """,
            (min_games,),
        )
        return {row["genre_name"] for row in cursor.fetchall()}


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

    # 콜드스타트 부트스트랩: 아직 최소 표본(장르당 MIN_GAMES_PER_GENRE개)을 못 채운 장르가
    # 있으면, 평소처럼 커서 순서대로 15개씩 도는 대신 안 채워진 장르를 우선 몰아서 처리한다.
    # 다 채워지고 나면(운영 중 평상시) 다시 커서 기반 순회로 돌아가 최신성만 유지한다.
    underfilled_genre_names = set()
    if popular_tags:
        try:
            with closing(db_connection()) as connection:
                underfilled_genre_names = get_underfilled_genre_names(connection, MIN_GAMES_PER_GENRE)
        except Exception as e:
            print(f"Underfilled genre check warning: {e}")

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

    # 장르(태그)마다 최소 표본을 보장하기 위해 태그로 필터링된 검색 결과를 추가로 모은다.
    # 안 채워진 장르가 있으면 그것부터 몰아서 처리(부트스트랩)하고, 커서는 건드리지 않는다 —
    # 그래야 다 채워진 뒤 평상시 순회가 원래 자리에서 이어진다.
    is_bootstrapping = bool(underfilled_genre_names)
    if is_bootstrapping:
        genre_batch = [t for t in popular_tags if t["name"] in underfilled_genre_names][:GENRES_PER_CYCLE_BOOTSTRAP]
        next_genre_cursor = genre_cursor
    else:
        genre_batch = popular_tags[genre_cursor:genre_cursor + GENRES_PER_CYCLE_STEADY] if popular_tags else []
        if not genre_batch and popular_tags:
            genre_cursor = 0
            genre_batch = popular_tags[0:GENRES_PER_CYCLE_STEADY]
        next_genre_cursor = genre_cursor + len(genre_batch)
        if next_genre_cursor >= len(popular_tags):
            next_genre_cursor = 0

    for tag in genre_batch:
        try:
            discovery_appids |= fetch_appids_by_tag(tag["tagid"], count=GENRE_SEARCH_COUNT)
        except Exception as e:
            print(f"Tag search warning ({tag['name']}): {e}")
        time.sleep(0.5)  # 장르 검색 요청 과다 방지 (부트스트랩 때는 40개까지 연속 호출됨)

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
            f"{'BOOTSTRAP' if is_bootstrapping else 'steady'} genres scanned this cycle "
            f"({len(underfilled_genre_names)} still underfilled before this run): "
            f"{[t['name'] for t in genre_batch]} "
            f"(cursor {genre_cursor} -> {next_genre_cursor} of {len(popular_tags)})"
        )
    except Exception as e:
        print(f"Chart snapshot save error: {e}")


async def chart_collection_loop():
    while True:
        await asyncio.get_event_loop().run_in_executor(None, collect_game_charts)
        await asyncio.sleep(CHART_COLLECT_INTERVAL_SECONDS)


if __name__ == "__main__":
    # 향후 K8s CronJob 엔트리포인트: `python collector.py`로 단발 실행 (WAS 프로세스 없이도 동작).
    collect_game_charts()
