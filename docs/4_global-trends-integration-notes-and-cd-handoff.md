# 4. 글로벌 트렌드 실 데이터 연동 — 운영 노트와 CD 인수인계

작성일: 2026-08-08
관련 문서: [0. 코드 분석](./0_project-code-analysis.md), [2. Steam/Bedrock 연동 노트](./2_steam-bedrock-integration-notes-and-next-steps.md) 4.2절("글로벌 트렌드 탭은 여전히 프론트 하드코딩 데이터 그대로 — 이번 범위에 포함할지 확인 필요")

이번 세션에서 글로벌 트렌드 페이지(`apps/web/index.html` `#page-global`)를 하드코딩 데이터에서 Steam 실 데이터 연동으로 전환했다. 이 문서는 사용한 데이터 소스, DB 스키마, 배치 갱신 방식, 그리고 `steam_insight_cd`(GitOps 배포 저장소, 이 저장소에는 없음) 담당자가 이어서 해야 할 작업을 정리한다.

---

## 1. 무엇이 바뀌었나

- 탭 구성을 `개요/인기순/할인순/급상승/카테고리/뉴스·패치`(클릭 핸들러 없음, 고정 mock 4개)에서 `인기/인기순/누적 판매순/할인 제품/최신작/뉴스·패치` 6개로 재구성했다.
- **인기** 탭: Steam 동시 접속자 기준 TOP 10 랜딩 뷰 + 게임 이름/App ID 검색.
- **인기순**: 같은 데이터의 TOP 100까지 더보기+로 확장.
- **누적 판매순 / 할인 제품 / 최신작**: 장르 필터 + 더보기+(최대 100개).
- **뉴스 · 패치**: 인기 상위 10개 게임의 뉴스를 발행일순으로 병합.
- 신규 백엔드 라우트: `GET /api/trends/popularity`, `GET /api/trends/popularity/search`, `GET /api/trends/genres`, `GET /api/trends/list`, `GET /api/trends/news`, `POST /internal/jobs/refresh-trends` (전부 `apps/was/app.py`).
- 신규 DB 테이블 6개(§3) — 전부 선택 사항이며, `DB_HOST` 등 DB 환경변수가 없어도 모든 기능이 실시간 조회로 동작한다(§2).

## 2. 데이터 소스 — 실제로 호출해서 검증한 내용

| 용도 | 엔드포인트 | 비고 |
|---|---|---|
| 인기 순위 | `GET api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/` (공식, 키 불필요) | **정확히 100개**만 반환. 게임 이름 없음. "어제" 비교 없음(지난주 대비만 제공) — §4 참고 |
| 게임 이름 해석 | `GET store.steampowered.com/api/appdetails?appids=X&filters=basic` (비공식) | **appid 1개당 1번 호출해야 한다(배치 불가 — 콤마로 여러 개 넘기면 `null` 응답, 실측 확인).** `ISteamApps/GetAppList`는 더 이상 존재하지 않는다(404, 실측 확인 — Steam이 이 API를 내렸다) |
| 누적판매순/할인제품/최신작 | `GET store.steampowered.com/search/results/?...&filter=topsellers|popularnew&specials=1&tags=<tagid>&start=&count=` (비공식, Steam 자체 무한스크롤용) | HTML 프래그먼트 응답 — `apps/was/app.py`의 `parse_store_search_html()`이 정규식으로 파싱. `count`를 25 미만으로 줘도 항상 25개 이상 반환(실측 확인) |
| 장르 목록 | `GET store.steampowered.com/tagdata/populartags/koreana` (비공식) | 한글 태그명 JSON, 430개 확인 |
| 뉴스 · 패치 | `GET .../ISteamNews/GetNewsForApp/v2/?appid=X` (공식) | appid 단위만 지원 — "글로벌 피드"가 없어 상위 10개 게임을 fan-out으로 병합 |
| `featuredcategories` | `GET store.steampowered.com/api/featuredcategories` | 위 스크래핑 실패 시 tag_id=0(전체 장르)에 한해 쓰는 얕은(10~30개) 폴백 |

**전부 키리스** — `STEAM_API_KEY` 없이도 이 기능 전체가 동작한다.

## 3. DB 스키마 (선택 사항)

`apps/was/app.py`에 6개 테이블이 `CREATE TABLE IF NOT EXISTS`로 정의되어 있다(`request_counter`와 동일한 지연 생성 패턴):

- `trend_daily_snapshot` — 일별 순위 스냅샷. **"어제 대비" 순위 변동을 계산하는 유일한 방법**(Steam API는 지난주 대비만 줌).
- `game_metadata` — appid → 이름/장르 캐시.
- `trend_store_cache` — 누적판매순/할인제품/최신작 스크래핑 결과 캐시 (category+tag_id+rank로 upsert).
- `genre_tag` — 장르 드롭다운 캐시.
- `trend_news_cache` — 뉴스·패치 병합 캐시.
- `job_lock` — 배치 중복 실행 방지 (여러 WAS 파드가 동시에 배치를 돌려도 하나만 통과).

## 4. 배치 갱신 — 현재는 in-process 스케줄러, CronJob은 다음 단계

`steam_insight_cd`(k8s Deployment/CronJob 매니페스트를 담당하는 별도 저장소)가 이 세션에서는 로컬에 없어 실제 CronJob을 배포할 수 없었다. 대신:

- 실제 작업 로직은 `apps/was/app.py`의 `refresh_trend_snapshot(app)` 순수 함수로 분리했다.
- `lifespan()`에서 매일 KST 04:00에 이 함수를 실행하는 asyncio 루프(`trend_refresh_scheduler_loop`)를 띄운다. `DB_HOST`가 없으면 매 사이클 조용히 스킵한다.
- 같은 함수를 `POST /internal/jobs/refresh-trends`(헤더 `X-Internal-Job-Token`)로도 트리거할 수 있다. 이 경로는 `apps/web/nginx.conf`의 `location /api/` 프록시 규칙 밖이라 웹 공개 경로로는 도달하지 않지만, 방어적으로 토큰도 검증한다.

**`steam_insight_cd` 담당자가 할 일 (선택 — 안 해도 실시간 조회 경로로 계속 동작함):**
1. RDS 접속 정보(`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`)를 WAS 파드에 주입 — 현재 `.env`/`k8s/app-secrets.yaml` 어디에도 없다(`request_counter`용 `/api/db`도 지금은 DB 미연동 상태로 추정됨). RDS 마스터 비밀번호는 `infra/rds.tf`의 `manage_master_user_password=true`로 AWS Secrets Manager가 관리 중이니, 이를 실제로 Pod 환경변수/Secret으로 배선하는 작업이 필요하다.
2. `INTERNAL_JOB_TOKEN` 값을 생성해 `k8s/app-secrets.yaml`(또는 동등한 시크릿)에 추가.
3. (선택) 아래와 같은 k8s `CronJob`을 추가해 in-process 스케줄러를 대체 — WAS가 여러 레플리카로 뜨는 경우 `job_lock` 테이블이 중복 실행을 막아주므로 in-process 스케줄러와 CronJob이 동시에 있어도 안전하다:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: trend-refresh
  namespace: steam-insight
spec:
  schedule: "0 19 * * *"  # UTC 19:00 = KST 04:00
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: trend-refresh
              image: curlimages/curl:8.10.1
              args:
                - -sf
                - -X
                - POST
                - http://was-service:8080/internal/jobs/refresh-trends
                - -H
                - "X-Internal-Job-Token: $(INTERNAL_JOB_TOKEN)"
              envFrom:
                - secretRef:
                    name: app-secrets
          restartPolicy: OnFailure
```

## 5. 의도적으로 범위에서 뺀 것 — 왜

- **TOP 100 밖 게임의 순위 검색은 만들지 않았다.** GetMostPlayedGames가 정확히 100개만 주고, 그 이상은 Steam이 공개 API로 노출하지 않는다(전체 앱 폴링은 비현실적). 사용자가 사전에 "마이너 게임이면 기능을 넣지 말라"고 명시했고, 실측으로 그 가정이 맞다고 확인됐다.
- **인기 / 인기순 탭에는 장르 필터가 없다.** GetMostPlayedGames 응답에 장르 정보가 아예 없다. `store/api/appdetails`로 게임마다 장르를 보강하는 방법도 있었지만, appid 1개당 1번 호출해야 하는 제약(§2) 때문에 TOP 100 전체에 매번 붙이면 비용이 크고, 안정적인 실시간 소스가 아니라고 판단해 붙이지 않았다. 누적판매순/할인제품/최신작 3개 탭은 스크래핑 결과에 태그가 이미 포함돼 있어(`tags=` 파라미터) 장르 필터를 제공한다.

## 6. 로컬 검증 방법

```bash
docker compose up --build
curl "http://localhost:3080/api/trends/popularity?limit=5"
curl "http://localhost:3080/api/trends/list?category=top_sellers&limit=5"
curl "http://localhost:3080/api/trends/news?limit=5"
```

DB 연동까지 검증하려면 로컬 MySQL 컨테이너를 띄우고 `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME`/`INTERNAL_JOB_TOKEN`을 채운 뒤 `POST /internal/jobs/refresh-trends`를 직접 호출해 테이블이 채워지는지 확인한다 (이번 세션에서 `mysql:8.0` 컨테이너로 6개 테이블 생성·upsert·`job_lock` 동시성·"어제 대비" 순위 계산까지 전부 실제로 검증했다).
