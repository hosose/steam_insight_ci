import os

import pymysql


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
