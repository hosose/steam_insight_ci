import os
import socket
from contextlib import closing

import pymysql
from fastapi import FastAPI, HTTPException

app = FastAPI(title="DE-AI-07 EKS Auto Mode WAS", version="3.0.0-auto")


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


import random

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info")
def info() -> dict[str, str]:
    return {
        "message": "WEB Pod에서 WAS Service로 정상 연결되었습니다.",
        "was_pod": socket.gethostname(),
        "version": "v3-eks-auto",
    }


@app.get("/api/user/{username}")
def analyze_user(username: str) -> dict:
    games_count = random.randint(150, 500)
    play_hours = random.randint(1000, 5000)
    achievement_rate = random.randint(50, 98)
    friends_count = random.randint(40, 300)

    selected_style = random.choice(PLAYSTYLES)
    selected_insight = random.choice(INSIGHTS)

    return {
        "status": "ok",
        "username": username,
        "was_pod": socket.gethostname(),
        "metrics": {
            "games": f"{games_count}",
            "hours": f"{play_hours:,}h",
            "achievements": f"{achievement_rate}%",
            "friends": f"{friends_count}명"
        },
        "playstyle": selected_style,
        "insight": selected_insight,
        "message": f"WAS Pod ({socket.gethostname()})에서 유저 '{username}' 분석 데이터를 생성했습니다."
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
