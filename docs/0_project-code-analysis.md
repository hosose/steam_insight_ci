# 0. Steam Insight (steam_insight_ci) 코드 분석

작성일: 2026-08-07
대상 저장소: `steam_insight_ci` (Steam Insight 프로젝트의 CI 담당 소스 저장소)

---

## 1. 저장소 성격

`steam_insight_ci`는 전체 Steam Insight DevOps 구성에서 **CI(빌드/검증/이미지 Push)만 담당**하는 저장소다.
배포(CD)는 별도 저장소 `steam_insight_cd`(GitOps/Kustomize/ArgoCD)가 담당하며, 이 저장소의 CI가 이미지를 ECR에 Push한 뒤 `steam_insight_cd`의 kustomization 이미지 태그를 갱신해 넘겨준다.

```
Steam_Insight/
├─ steam_insight_ci   ← 본 분석 대상 (Terraform 인프라 + 앱 소스 + CI)
└─ steam_insight_cd   ← GitOps 배포 저장소 (별도)
```

핵심 파이프라인:

```
Push/PR → GitHub Actions
  ├─ validate: terraform fmt/validate, WAS 문법 검사, web/was 이미지 빌드 후 /health 확인
  ├─ build-and-push (push/main만): OIDC로 AWS 임시 인증 → ECR Push (web, was 매트릭스)
  └─ update-cd (push/main만): steam_insight_cd 체크아웃 → kustomize edit set image → commit/push
       (ArgoCD가 steam_insight_cd의 main 변경을 감지해 실제 배포)
```

---

## 2. 디렉터리 구조와 역할

```
steam_insight_ci/
├─ apps/
│  ├─ web/          정적 프론트엔드 (Vanilla JS SPA) + Nginx 리버스 프록시
│  └─ was/          백엔드 API 서버 (FastAPI)
├─ infra/           Terraform (VPC, EKS, RDS, ECR, IAM, GitHub OIDC 등)
├─ .github/workflows/ci.yml   CI 파이프라인 정의
├─ scripts/
│  ├─ ci/           로컬/CI 검증 스크립트 (Linux·Windows 이중 구현)
│  ├─ deploy/       최초 수동 배포용 단계별 스크립트 (01~04)
│  ├─ ops/          상태 확인 / 전체 삭제
│  └─ setup/        실습 환경 준비 (Windows EKS 설치 스크립트 등)
├─ project.bat / project.sh   전체 명령 진입점
├─ setup-ci.bat / setup-ci.sh CI 최초 1회 설정
└─ validate-ci.bat / validate-ci.sh  로컬 CI 재검증
```

Windows/Linux 스크립트가 항상 쌍으로 존재하는 것이 이 저장소의 뚜렷한 컨벤션이다.

---

## 3. `apps/web` — 프론트엔드

- **기술 스택**: 순수 HTML/CSS/Vanilla JS 단일 파일(`index.html`), 빌드 도구 없음. Nginx로 정적 서빙.
- **구성**: `nginx.conf`에서
  - `/health` → 정적 200 응답 (컨테이너 헬스체크용)
  - `/api/*` → `upstream was_backend`(`was-service:8080`)로 리버스 프록시
  - 그 외 경로 → SPA(`index.html`) fallback
- **화면 3개**: 유저 검색(`page-search`), 친구 정보(`page-friends`), 글로벌 트렌드(`page-global`)를 `data-page` 속성 기반 클라이언트 라우팅으로 전환.
- **WAS 연동**: `runSearch()`에서 `fetch('/api/user/{username}')`, `fetch('/api/friends/{username}')` 호출. 실패 시 콘솔 경고 후 화면에 정적 목업 데이터(`friendsData`, `trendGames`)로 폴백 — WAS 장애 시에도 UI가 깨지지 않도록 설계됨.
- **글로벌 트렌드 탭**은 현재 전부 정적 하드코딩 데이터이며 API 연동이 없음 (`trendGames` 배열 고정).

---

## 4. `apps/was` — 백엔드 (분석 시점 = 이번 세션에서 비동기로 재작성 전 기준)

### 4.1 원래 구조 (재작성 전)

- FastAPI를 사용은 하고 있었으나 **모든 라우트 핸들러가 동기(`def`) 함수**였고, DB 접근은 `pymysql`(동기 드라이버)을 **요청마다 새로 커넥션을 열고 닫는 방식**으로 처리했다.
  - FastAPI는 동기 `def` 핸들러를 내부 스레드풀에서 실행하므로 이벤트 루프가 완전히 막히지는 않지만, 커넥션 풀링이 없어 매 `/api/db` 요청마다 TCP 핸드셰이크 + MySQL 인증 비용이 반복 발생 → 지연시간·RDS 커넥션 수 측면에서 비효율적.
  - 진정한 async I/O(비동기 소켓 기반 커넥션 풀)를 사용하지 않아 "FastAPI = 비동기 프레임워크"라는 이점을 활용하지 못하고 있었음.
- 엔드포인트 5개, 전부 단일 파일 `app.py`:
  - `GET /health` — 헬스체크
  - `GET /api/info` — Pod 정보
  - `GET /api/user/{username}` — **랜덤 목업** 유저 지표/플레이스타일/인사이트 생성 (`random.randint`, `random.choice`)
  - `GET /api/friends/{username}` — 고정 친구 풀에서 5명 랜덤 샘플링 + 랜덤 플레이타임 생성
  - `GET /api/db` — RDS MySQL 연결 확인용 (`request_counter` 테이블에 INSERT 후 COUNT 조회)
- **실제 Steam Web API 연동은 없음.** `/api/user`, `/api/friends`는 모두 `random` 기반 목업이며, 프론트엔드가 표시하는 "유저 분석"은 입력한 이름과 무관하게 매번 무작위 값이다. 실제 서비스 구현 전 EKS/RDS 파이프라인 검증용 스텁으로 보인다.
- 인증/인가, 요청 검증(Pydantic 모델), 레이트리밋, 로깅 미들웨어는 없음.
- `required_env()`로 DB 관련 환경변수(`DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`) 누락 시 예외를 던지지만, 이 검증은 **`/api/db` 호출 시점에만** 실행되어 DB 환경변수 없이도 나머지 API/헬스체크는 정상 동작 — CI 컨테이너 헬스체크(`scripts/ci/*/validate.*`)가 DB 환경변수를 주입하지 않고 `/health`만 확인하는 구조와 맞물려 있는 의도적 설계.

### 4.2 이번 세션에서 적용한 변경 — FastAPI 비동기 구조로 전환

`apps/was/app.py`를 아래와 같이 재작성했다 (파일 경로와 진입점 `app:app`은 CI 스크립트 호환을 위해 유지 — 4.3절 참고).

- **모든 라우트 핸들러를 `async def`로 전환** (`/health`, `/api/info`, `/api/user/{username}`, `/api/friends/{username}`, `/api/db`).
- **DB 드라이버를 `pymysql`(동기) → `aiomysql`(비동기)로 교체**, `requirements.txt`도 함께 수정.
- **커넥션 풀 도입**: 매 요청마다 커넥션을 새로 맺던 방식을 없애고, `aiomysql.create_pool(minsize=1, maxsize=10, ...)`로 재사용 가능한 비동기 풀을 구성.
- **풀 생성 시점을 지연(lazy)으로 설계**:
  - `lifespan` 컨텍스트에서는 `app.state.db_pool = None`만 세팅하고 실제 풀 생성은 하지 않음.
  - `/api/db`가 처음 호출될 때 `asyncio.Lock`으로 동시성을 보호하며 풀을 1회 생성해 캐시.
  - 이렇게 한 이유: 앱 시작 시점에 즉시 풀을 만들면 (a) DB 환경변수가 없는 CI 컨테이너 헬스체크에서 앱 자체가 기동 실패하고, (b) RDS가 아직 준비되지 않은 상태로 Pod가 뜨는 실제 배포 초기 레이스 컨디션에서도 앱 기동이 실패한다. 기존 코드의 "DB는 실제 사용 시점에만 연결한다"는 성격을 비동기 버전에서도 유지했다.
- **종료 시 정리**: `lifespan`의 `yield` 이후 구간에서 풀이 생성되어 있으면 `pool.close()` + `await pool.wait_closed()`로 정상 반환.
- 응답 스키마·엔드포인트 경로·목업 데이터 내용은 **기존과 100% 동일하게 유지** — 프론트엔드(`apps/web/index.html`)와의 계약을 깨지 않기 위함.
- `version` 필드를 `v3-eks-auto` → `v4-eks-async`로, FastAPI `title`의 `version`도 `4.0.0-async`로 갱신해 이번 전환을 식별 가능하게 함.

### 4.3 왜 패키지 구조(다중 파일)로 쪼개지 않았는가

`scripts/ci/linux/validate.sh`, `scripts/ci/windows/validate.bat`가 다음을 **하드코딩된 경로**로 직접 참조한다:

```bash
python3 -m py_compile "${ROOT_DIR}/apps/was/app.py"
```

그리고 `apps/was/Dockerfile`의 `CMD`도 `uvicorn app:app`으로 단일 모듈을 가정한다. 이 두 지점을 함께 바꾸지 않고 `apps/was/app/main.py` 같은 패키지 구조로 옮기면 로컬 CI 검증 스크립트와 Docker 빌드가 즉시 깨진다. 이번 요청 범위는 "FastAPI 비동기 형식으로 구성"이었으므로, CI/Dockerfile을 함께 손대는 대신 **단일 파일 안에서 완전한 비동기 구조**로 재작성하는 쪽을 선택했다. 라우터 분리, `pydantic` 응답 모델 도입 등 추가 구조화가 필요하면 `validate.sh/.bat`와 `Dockerfile`의 경로 참조를 함께 갱신해야 한다 (7절 권장사항 참고).

### 4.4 실 데이터 연동 — Steam Web API + AWS Bedrock (v5)

4.1~4.3의 비동기 전환 이후, `/api/user`, `/api/friends`의 `random` 목업을 실제 Steam Web API 호출과 Bedrock LLM 기반 인사이트 생성으로 교체했다 (`version`을 `v5-eks-steam-bedrock`으로 갱신).

- **환경 변수 기반 기능 전환**: `STEAM_API_KEY`가 없으면 두 엔드포인트 모두 기존과 동일한 `random` 목업(`_mock_user_response`, `_mock_friends_response`)으로 폴백한다. CI 헬스체크·로컬 무설정 실행이 여전히 깨지지 않는다.
- **Steam Web API 연동** (`STEAM_API_KEY` 설정 시):
  - `ResolveVanityURL`로 SteamID64/커스텀 URL/프로필 URL 입력을 모두 SteamID64로 정규화
  - `GetPlayerSummaries`(프로필), `GetOwnedGames`(보유 게임·누적 플레이 시간), `GetFriendList`(친구 목록), `GetRecentlyPlayedGames`(최근 2주 플레이)를 조합해 실제 지표 계산
  - 업적 달성률은 Steam이 계정 전체 단일 지표를 제공하지 않으므로, 최다 플레이 게임 상위 5개의 `GetPlayerAchievements` 결과를 평균해 근사치를 계산 (비공개/업적 없는 게임은 평균에서 제외, 전부 실패 시 `"N/A"`)
  - 친구 목록이 비공개면 `GetFriendList`가 403을 반환하는데, 이 경우 빈 목록으로 처리(에러로 취급하지 않음)
  - 존재하지 않는 프로필은 `404`, Steam API 자체 장애(타임아웃/5xx)는 `502`로 구분해 응답 — 목업 버전과 달리 **실 데이터 연동 이후에는 잘못된 유저명에 대해 정상적으로 실패할 수 있다** (프론트엔드의 fetch 실패 시 로컬 폴백 로직이 이 케이스를 흡수한다)
- **AWS Bedrock 연동** (`BEDROCK_API_KEY` 설정 시, IAM SigV4가 아닌 **Bedrock API 키 Bearer 토큰 인증** 방식 사용):
  - 유저의 상위 5개 게임(이름+누적 시간)을 프롬프트에 담아 Bedrock Runtime `InvokeModel`을 호출하고, `{"playstyle": ..., "insight": ...}` JSON만 응답하도록 지시 → 실제 게임/시간 데이터를 근거로 한 플레이스타일·인사이트 문구를 생성
  - 친구 카드의 `trait` 문구도 동일한 방식으로 친구별 상위 게임 데이터를 근거로 생성 (친구별로 별도 호출, `asyncio.gather`로 동시 실행)
  - `BEDROCK_REGION`(기본 `us-east-1`), `BEDROCK_MODEL_ID`(기본 `anthropic.claude-3-5-haiku-20241022-v1:0`)로 리전/모델을 override 가능
  - Bedrock 키가 없거나 호출/파싱이 실패하면 조용히 기존 `random.choice(PLAYSTYLES/INSIGHTS/FRIEND_TRAITS)` 문구로 폴백 — Bedrock 장애가 API 전체를 502로 만들지 않는다
- **API 키 배포 방식**: `k8s/app-secrets.example.yaml`(git 추적, 플레이스홀더)과 `k8s/app-secrets.yaml`(gitignore, 실제 값)로 분리. `STEAM_API_KEY`, `BEDROCK_API_KEY`를 한 Secret(`app-secrets`)에 담아 WAS Pod에 환경 변수로 주입하는 구조를 전제로 한다.
- **의존성 추가**: `requirements.txt`에 `httpx`(Steam/Bedrock 비동기 HTTP 클라이언트) 추가. DB 커넥션 풀과 동일한 패턴(app.state에 지연 생성 + 종료 시 정리)으로 `httpx.AsyncClient`도 관리한다.

---

## 5. `infra/` — Terraform 인프라

| 파일 | 내용 |
|---|---|
| `vpc.tf` | Public / App(Private) / DB(Private) 3계층 서브넷, AZ 2개(`a`, `c`) |
| `eks.tf` | EKS **Auto Mode** 클러스터 (Karpenter 기반 NodePool `general-purpose`/`system`, ALB/NLB 자동 관리, EBS CSI 자동 관리), `metrics-server` Add-on만 별도 설치 |
| `rds.tf` | MySQL 8.0 RDS, Multi-AZ, `manage_master_user_password = true`(Secrets Manager 자동 관리), `publicly_accessible = false` |
| `ecr.tf` | `web`, `was` 리포지토리, push 시 스캔, 최근 10개 이미지만 유지하는 lifecycle policy |
| `iam.tf` | EKS 클러스터/노드 역할 |
| `github-actions-ci.tf` | GitHub OIDC Provider + CI 전용 IAM Role (ECR Push 권한) — PEM/액세스키 없이 단기 자격증명 사용 |
| `security.tf` | 보안 그룹 |
| `logging.tf` | CloudWatch 로그 그룹 |
| `locals.tf` | `cluster_name = "${project_name}-${environment}"`, 공통 태그 |
| `variables.tf` / `outputs.tf` / `provider.tf` / `versions.tf` | 입력 변수, 출력값, Provider/버전 고정 |

특징: RDS는 `deletion_protection = false`, `skip_final_snapshot = true`로 **개발/실습 환경**을 전제로 한 설정(운영 환경 승격 시 재검토 필요 — 7절 참고).

---

## 6. CI/CD 파이프라인 (`.github/workflows/ci.yml`)

3개 Job:

1. **`validate`**: `scripts/ci/linux/validate.sh` 실행 → CI 필수 파일 검사(`verify_ci_config.py`) → `terraform fmt -check` → `terraform validate` → WAS `py_compile` → web/was Docker 이미지 빌드 후 `/health` 컨테이너 헬스체크.
2. **`build-and-push`** (PR 제외, push/dispatch만): OIDC로 임시 AWS 자격증명 획득 → `web`/`was` 매트릭스로 이미지 빌드 → 커밋 SHA 12자리 태그 + `dev-latest` 이동 태그로 ECR Push → 푸시 확인.
3. **`update-cd`** (push/dispatch만): `steam_insight_cd` 체크아웃 → `kustomize edit set image`로 두 이미지 태그 갱신 → 변경 있으면 커밋/푸시 (ArgoCD가 감지해 실제 배포).

PR에서는 Push까지 가지 않고 `validate`만 실행되도록 `if: github.event_name != 'pull_request'`로 분리되어 있어, 외부 기여자 PR에서 AWS 자격증명이 노출되지 않는다.

---

## 7. 발견된 특이사항 및 권장 개선 (참고용, 이번 세션에서 코드 변경은 하지 않음)

- ~~`/api/user`, `/api/friends`가 실제 Steam Web API를 호출하지 않고 전부 `random` 목업이다~~ → 4.4절에서 해결: `STEAM_API_KEY`/`BEDROCK_API_KEY` 설정 시 실 데이터·실 인사이트로 동작하며, 키가 없을 때만 목업으로 폴백한다.
- **입력 검증 부재**: `username` 경로 파라미터에 별도 검증이 없다. Steam Web API가 자체적으로 잘못된 값에 404/에러를 반환하므로 치명적이진 않지만, 과도하게 긴 입력값 등에 대한 명시적 길이 제한은 여전히 없다.
- **업적 달성률은 근사치**: 계정 전체의 단일 업적 달성률 API가 Steam에 없어 상위 5개 게임 평균으로 근사한다 (4.4절). 정확한 지표가 필요하면 프론트엔드 문구를 "근사치" 임을 명시하는 것을 고려.
- **Bedrock 호출 비용/지연**: 유저 1명 조회당 최소 1회(인사이트) + 친구 수만큼(최대 5회) Bedrock 호출이 발생한다. 트래픽이 늘면 캐싱(예: 동일 유저 재조회 시 RDS에 캐시된 인사이트 재사용) 도입을 검토할 필요가 있다.
- **CORS/인증 미들웨어 없음**: 현재는 Nginx가 동일 오리진으로 프록시하므로 문제가 없지만, WAS를 다른 오리진에서 직접 호출할 계획이 있다면 CORS 설정이 필요.
- **CI 검증 스크립트가 `apps/was/app.py` 단일 파일 경로를 하드코딩**하고 있어, 백엔드를 패키지 구조로 확장하려면 `scripts/ci/linux/validate.sh`, `scripts/ci/windows/validate.bat`, `apps/was/Dockerfile`을 함께 수정해야 한다.
- **RDS 설정이 개발용**(`deletion_protection=false`, `skip_final_snapshot=true`)이므로 운영 환경 분리 시 별도 `.tfvars`/환경 분기 검토 필요.
- **테스트 코드 없음**: `apps/was`, `apps/web` 모두 자동화된 단위/통합 테스트가 없고, CI는 문법 검사 + 헬스체크 수준에 그친다.
- **글로벌 트렌드 페이지**는 프론트엔드에 하드코딩된 4개 게임 데이터만 표시되며 백엔드 엔드포인트가 아직 없다.

---

## 8. 로컬 실행 (Docker Compose)

저장소 루트에 `docker-compose.yml`을 추가해 EKS 없이 로컬에서 두 컨테이너를 바로 띄워볼 수 있게 했다.

| 서비스 | 컨테이너 내부 포트 | 로컬 호스트 포트 |
|---|---|---|
| `web` (Nginx + 정적 SPA) | 80 | **3080** |
| `was-service` (FastAPI) | 8080 | **8080** |

```bash
docker compose up --build
# 브라우저: http://localhost:3080
# WAS 직접 호출: http://localhost:8080/health
```

`nginx.conf`의 `upstream was_backend`가 `was-service:8080`을 바라보도록 되어 있으므로, Compose의 WAS 서비스 이름을 반드시 `was-service`로 유지해야 한다. `apps/was/Dockerfile`도 컨테이너 내부 포트가 8000 → 8080으로 이미 변경되어 있었는데, `scripts/ci/linux/validate.sh`·`scripts/ci/windows/validate.bat`의 WAS 헬스체크 포트 매핑(`18000:8000`)이 이를 따라가지 못해 CI 로컬 검증이 깨지는 상태였다. 이번 세션에서 두 스크립트를 `18000:8080`으로 함께 수정해 CI와 로컬 실행이 같은 포트 구성을 쓰도록 맞췄다. DB 환경변수 없이 실행해도 `/health`, `/api/info`, `/api/user`, `/api/friends`는 정상 동작하며 `/api/db`만 503을 반환한다 (4.2절 참고).

## 9. 요약

이 저장소는 원래 "실제 Steam 데이터 분석 로직"보다는 **AWS EKS 기반 CI/CD 파이프라인 자체를 검증하기 위한 최소 스택**(Nginx 정적 프론트 + FastAPI 스텁 백엔드 + Terraform 인프라 + GitHub Actions OIDC 파이프라인)에 가까웠다. 이번 세션들을 거치며 `apps/was/app.py`는 **① 동기 FastAPI(요청마다 신규 DB 커넥션) → ② 완전 비동기 FastAPI(aiomysql 커넥션 풀 + lazy 초기화) → ③ 실제 Steam Web API + AWS Bedrock 연동(목업 → 실 데이터·실 인사이트, API 키 미설정 시 목업 자동 폴백)** 순으로 발전했으며, 그 과정에서 외부에 노출되는 API 계약과 CI 검증 스크립트 호환성은 유지했다. 로컬 실행 환경(포트 3080/8080, `docker-compose.yml`)과 API 키 배포 방식(`k8s/app-secrets*.yaml`)도 함께 정리되었다.
