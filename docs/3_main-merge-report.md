# 3. `origin/main` → `sdhbranch` 병합 결과 보고서

작성일: 2026-08-07
관련 커밋: `34e1a46` (Merge remote-tracking branch 'origin/main' into sdhbranch)
병합 전 sdhbranch 체크포인트: `7ffd01e`

---

## 1. 왜 이 문서가 필요한가

`git pull origin main`을 그대로 실행했다면 `apps/was/app.py`, `apps/web/index.html`에서 **git이 자동으로 풀 수 없는 실제 코드 충돌**이 발생했을 것이다. 원인은 `main`이 단순한 디자인/인프라 브랜치가 아니라, **sdhbranch와는 독립적으로 Steam 연동 기능 전체를 다시 구현한 별도 작업**을 담고 있었기 때문이다. 이 문서는 무엇이 충돌했고, 어떤 기준으로 어느 쪽을 선택했는지, 병합 후 최종 구조가 어떻게 되는지를 기록한다.

---

## 2. 병합 전 두 브랜치의 실제 차이

`git merge-base sdhbranch origin/main` 기준 분기점(`65ddf9e`) 이후 각 브랜치가 독립적으로 쌓은 커밋:

```
origin/main (9 commits ahead of 분기점)
  bfcedc1 Feat: readme.md 수정
  c061672 Feat: ci test
  71b8291 Feat: gitaction 오류 수정
  c8d2c3f Feat: 오류수정
  41a67e1 Feat: github action 오류 수정
  c7fb725 Feat: user search              ← app.py/index.html 독자 구현
  f9be071 Merge pull request #2 ...
  3ce77ce Feat: health 체크 api 작성
  a859b61 Feat: steam api key aws 등록 자동화   ← infra 자동화

sdhbranch (분기점 이후, 이번 세션 전체)
  b417df4 Feat: api입력 및 하드코딩 전환
  7ffd01e Feat: LLM 하드코딩 제거, AccountID 자동 인식, 게임 목록/이미지, 로딩 UX 개선
```

### 2.1 main이 독자적으로 구현한 것 (`apps/was/app.py`, 469줄)

- **완전 동기(sync) 구조**: `def` 핸들러 + 블로킹 `urllib.request` + 블로킹 `pymysql` (sdhbranch는 `async def` + `httpx.AsyncClient` + `aiomysql` 풀)
- **3단계 폴백 체인**: ① `STEAM_API_KEY`가 있으면 공식 Steam Web API 호출 → ② 실패 시 `steamcommunity.com/id/...../?xml=1` **공개 프로필 XML 스크레이핑**으로 실제 데이터 일부 추출 → ③ 그마저 실패하면 `hashlib.md5(username)` 기반 **결정론적(같은 이름 → 항상 같은 값) 가짜 데이터** 생성
- **DB 영속화**: `steam_user_profiles`(조회한 유저 프로필 캐시), `search_history`(검색 이력) 두 테이블에 조회 결과를 저장 — sdhbranch에는 없는 기능
- **`.env` 파일 자체 로딩**: `app.py`가 시작할 때 자기 디렉터리의 `.env`를 직접 파싱해 환경변수로 주입 (Docker/K8s의 환경변수 주입 방식과는 다른 별도 경로)
- `playstyle`/`insight`는 `hash(username) % len(...)`로 **정적 리스트에서 결정론적으로 선택** — Bedrock 등 LLM 연동 없음
- 프론트에는 `steam_id`, `personaname`, `avatar_url`, `db_saved`, `db_source`, `data_source` 필드를 응답에 추가하고, 이를 이용해 아바타 이미지 표시·"MySQL DB 저장됨" 배지 등을 보여줌

### 2.2 sdhbranch(이번 세션)가 구현한 것

- 완전 비동기 구조, `aiomysql`/`httpx` 커넥션 재사용
- **AWS Bedrock 실 연동**: 유저의 실제 보유 게임/시간 데이터를 근거로 `playstyle`/`insight`/친구 `trait`를 LLM이 생성 (실패 시 랜덤 하드코딩이 아니라 `null`)
- AccountID(친구 코드) 자동 SteamID64 변환, 보유 게임 이미지+Steam 스토어 링크(`top_games`), Friend Discovery 하드코딩 제거(실제 0명이면 0명으로 표시), 검색 로딩 스피너/스켈레톤, "n분 전 갱신 완료" 상대 시간 배지, Pod 해시 대신 "실시간 서버 연동 중" 문구
- DB 영속화 없음, XML 스크레이핑 폴백 없음

두 브랜치는 **같은 문제(Steam 데이터로 유저 분석하기)를 처음부터 각자 다시 구현**한 셈이라, 자동 3-way 병합이 `apps/was/app.py`·`apps/web/index.html`에서 충돌했다.

---

## 3. 충돌 해결 기준과 실제 선택

사용자 지시: *"main은 디자인/인프라 위주로, 유저 맞춤형 변환된 값이나 불러오는 부분은 sdhbranch 위주로."*

| 영역 | 충돌 여부 | 채택 | 근거 |
|---|---|---|---|
| `infra/github-actions-ci.tf` | 없음(자동 병합) | **main** | OIDC 신뢰 조건을 `StringEquals`→`StringLike`로 완화해 owner/repo id 조합까지 허용하는 순수 인프라 개선. sdhbranch가 건드리지 않은 영역 |
| `infra/terraform.tfvars` | 없음 | **main** | `github_ci_repository_id` 값 갱신 |
| `scripts/deploy/windows/03-secret-sync.bat` | 없음 | **main** | RDS Secret 동기화 시 로컬 `.env`에서 `STEAM_API_KEY`/`BEDROCK_API_KEY`까지 함께 수집하는 자동화 추가 |
| `README.md` | 없음 | **main** | 저장소 이름 오타 수정 등 문서 정정 |
| `apps/was/Dockerfile` | 없음(양쪽 변경이 서로 다른 줄) | **양쪽 다 반영** | main의 `COPY app.py .`→`COPY app.py ./`와 sdhbranch의 포트 8000→8080 변경이 자동 병합됨 |
| **`apps/was/app.py`** | **충돌** | **sdhbranch 기반 + main 일부 필드 이식** | 아래 4절 참고 |
| **`apps/web/index.html`** | **충돌(1개 블록)** | **sdhbranch 기반 + main 일부 필드 이식** | 아래 4절 참고 |

`app.py`/`index.html`은 git이 자동으로 텍스트를 섞으면 두 아키텍처(동기 vs 비동기, DB 영속화 vs Bedrock 생성)가 뒤엉켜 실행 자체가 안 되는 코드가 나올 상황이었다. 그래서 조각을 억지로 짜깁기하지 않고, **sdhbranch 버전을 그대로 최종본의 뼈대로 삼고, main에서 가져올 가치가 있는 부분만 선택적으로 이식**하는 방식으로 수동 해결했다.

---

## 4. 실제로 이식한 것 / 이식하지 않은 것

### 4.1 `apps/was/app.py`에 main에서 이식한 것

- 응답에 `steam_id`, `avatar_url`, `data_source` 3개 필드 추가
  - `steam_id`: `resolve_steam_id()`가 확정한 SteamID64
  - `avatar_url`: `GetPlayerSummaries` 응답의 `avatarfull`
  - `data_source`: 실 데이터 경로면 `"STEAM_API"`, 목업 경로면 `"MOCK"`
- 이 필드들은 main의 프론트엔드 아바타 표시 로직이 그대로 기대하는 이름이라, 필드만 채워주면 프론트 코드 수정 없이 호환된다.

### 4.2 `apps/web/index.html`에서 자동/수동으로 반영된 것

- **자동 병합(충돌 없음)**: 아바타 이미지 표시(`profileAvatar`에 `<img>` 삽입), `profileSubText`(Steam ID·데이터 출처 표시) — main이 sdhbranch가 손대지 않은 라인에 추가했기 때문에 git이 알아서 합쳤다.
- **수동 해결(충돌 블록)**: `insightTitle`/`insightDesc`/`navStatusText`/`badgeUpdated`를 갱신하는 부분에서 main은 옛날 방식(`WAS POD: <해시> · LIVE`, `WAS 갱신 완료 (<해시>) [실제 스팀 데이터][DB 저장 완료]`)을 그대로 유지하고 있었다. 이건 **바로 직전 요청에서 사용자가 명시적으로 바꿔달라고 한 부분**(Pod 해시 노출 제거 → "실시간 서버 연동 중", 상대 시간 배지)과 정면으로 배치되므로, 이 블록은 **sdhbranch 쪽을 100% 채택**하고 main 쪽 코드는 버렸다.
- `profileSubText`의 `data.db_saved` 참조는 sdhbranch가 `db_saved` 필드를 내려주지 않으므로 `undefined`로 평가되어 자동으로 "DB 저장 안 됨"으로 표시된다 — 실제 상태와 일치하므로 별도 수정 없이 그대로 두었다.

### 4.3 의도적으로 이식하지 않은 것 (별도 기능 결정이 필요해서 보류)

- **DB 영속화** (`steam_user_profiles`, `search_history` 테이블, 조회할 때마다 저장하는 로직): "디자인"이 아니라 새 기능이라 사용자 확인 없이 끼워 넣지 않았다. 필요하면 sdhbranch의 기존 `aiomysql` 풀에 얹어 비동기로 재구현하는 것을 권장한다 (동기 pymysql 코드를 그대로 가져오면 이벤트 루프를 막는다).
- **XML 스크레이핑 폴백** (`fetch_steam_public_xml`) / **해시 기반 결정론적 목업** (`generate_mock_user_data`): sdhbranch의 async Steam Web API 경로 + 랜덤 목업으로 이미 같은 역할을 하고 있어 불필요한 중복으로 판단해 제외.
- **`.env` 자체 로딩(`load_env_file()`)**: sdhbranch는 Docker Compose의 `env_file`/K8s Secret로 환경변수를 주입하는 방식을 이미 쓰고 있어(이전 세션에서 구축) 중복. 로컬에서 `python app.py`를 직접 실행하는 워크플로우가 필요하면 재검토.

---

## 5. 병합 후 최종 구조

```
apps/was/app.py          ← sdhbranch(비동기+Bedrock) 뼈대 + main 필드(steam_id/avatar_url/data_source) 이식
apps/web/index.html      ← sdhbranch(로딩 UX+실시간 배지+게임 그리드) 뼈대 + main 필드(아바타 이미지 표시) 이식
infra/*.tf, *.tfvars     ← main 그대로 (OIDC 조건 완화, repository_id 갱신)
scripts/deploy/windows/03-secret-sync.bat  ← main 그대로 (Steam/Bedrock 키 자동 수집)
README.md                ← main 그대로 (오타 수정)
apps/was/Dockerfile      ← 양쪽 변경 모두 반영 (COPY 경로 정리 + 포트 8080)
```

커밋 그래프:

```
*   34e1a46 Merge remote-tracking branch 'origin/main' into sdhbranch
|\
| * a859b61 Feat: steam api key aws 등록 자동화
| * 3ce77ce Feat: health 체크 api 작성
| * ...(main 커밋들)
* 7ffd01e Feat: LLM 하드코딩 제거, AccountID 자동 인식, 게임 목록/이미지, 로딩 UX 개선
* b417df4 Feat: api입력 및 하드코딩 전환
```

---

## 6. 병합 후 검증

- `python -m py_compile apps/was/app.py` — 통과
- `docker compose up --build -d` — web/was 컨테이너 정상 기동
- `GET /health` — `{"status":"ok"}`
- `GET /api/user/76561198828407430` (nginx 프록시 경유) — 실제 Steam 데이터(보유 게임 2개, 143h) + Bedrock 생성 인사이트 + **새로 추가된** `steam_id`/`avatar_url`/`data_source` 필드까지 정상 반환
- `GET /api/friends/76561198828407430` — 빈 배열 정상 반환 (에러 없음)
- 빌드된 컨테이너의 `index.html` 안에 `profileSubText`/`gamesGrid`/`discoveryTitle`/`isRealSteam` 마커가 모두 존재함을 확인 (프론트 병합 결과가 실제 배포 이미지에 반영됨)

## 7. 아직 로컬에만 있고 원격에는 반영되지 않은 것

이번 병합은 **로컬 `sdhbranch`에서만 수행**했고 `git push`는 하지 않았다. `origin/sdhbranch`로 push하기 전에 사용자 확인을 받는 것을 권장한다 (원격 브랜치를 다른 협업자가 보고 있을 수 있는 공유 상태이기 때문).
