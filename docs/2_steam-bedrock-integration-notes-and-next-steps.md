# 2. Steam/Bedrock 실 데이터 연동 — 운영 노트와 다음 작업

작성일: 2026-08-07
관련 문서: [0. 코드 분석](./0_project-code-analysis.md) 4.4절, [1. TS 전환 검토](./1_frontend-typescript-migration-review.md)

이번 세션에서 `apps/was/app.py`의 Steam Web API + AWS Bedrock 실 데이터 연동을 로컬 `docker compose` 환경에서 실제 키로 끝까지 검증했다. 이 문서는 그 과정에서 얻은 운영 지식과, 다음에 이어서 할 작업 목록을 정리한다.

---

## 1. 로컬에서 실 키로 검증한 방법

- `docker-compose.yml`의 `was-service`에 `env_file: [{path: .env, required: false}]`를 추가해, 저장소 루트의 `.env`(gitignore됨)에 있는 `STEAM_API_KEY`/`BEDROCK_API_KEY`를 컨테이너 환경 변수로 주입하도록 했다.
- `.env`는 `k8s/app-secrets.yaml`(gitignore됨, 실제 배포용)과 값은 같지만 로컬 `docker compose` 전용이다. 즉 API 키를 두 군데(`. env`, `k8s/app-secrets.yaml`)에 두는 구조이며 **둘 다 git에는 올라가지 않는다** (`.gitignore`에 `.env`와 `k8s/app-secrets.yaml` 모두 등록됨. 커밋 대상은 플레이스홀더뿐인 `k8s/app-secrets.example.yaml`).
- 검증 절차: `docker compose up --build -d` → `docker exec ... printenv`로 키가 컨테이너에 실제로 들어갔는지 확인 → `curl localhost:8080/api/user/{steamid}` (WAS 직접) → `curl localhost:3080/api/user/{steamid}` (Nginx 프록시 경유, 브라우저와 동일 경로)로 이중 확인.

## 2. 트러블슈팅 기록 (다음에 키를 재발급/교체할 때 참고)

### 2.1 SteamID64를 모를 때

- Steam 프로필은 `steamcommunity.com/id/커스텀URL` 또는 `steamcommunity.com/profiles/17자리숫자` 두 형태로 접근 가능하다.
- 커스텀 URL 문자열(예: `donggus11`)이 실제로 등록되어 있지 않으면 `ISteamUser/ResolveVanityURL`이 `{"success":42,"message":"No match"}`를 반환한다 — API 키 문제가 아니라 그 문자열 자체가 존재하지 않는 것.
- 게임 내에서 보여주는 9~10자리 숫자(예: `868141702`, "친구 코드"/AccountID)는 SteamID64가 아니다. 다음 공식으로 변환된다:
  ```
  SteamID64 = 76561197960265728 + AccountID32
  ```
  (예: `868141702` → `76561198828407430`)
  **이 변환은 `resolve_steam_id()`에 자동화되어 있다.** 입력값이 17자리가 아니면서 1~10자리 순수 숫자면, 먼저 AccountID로 해석해 `GetPlayerSummaries`로 실존 여부를 확인하고, 존재하면 그대로 사용한다. 존재하지 않으면(= 진짜 바니티 URL이 우연히 숫자인 경우) 기존 `ResolveVanityURL` 경로로 폴백한다. 즉 사용자는 SteamID64든 AccountID든 그대로 입력해도 된다.
- `https://s.team/p/xxxx/yyyy` 같은 공유 단축 링크는 **자동 처리 대상이 아니며, 앞으로도 서버 사이드에서는 풀 수 없다.** 실제로 Steam 서버에 직접 요청해보면 `/user/xxxx/yyyy/`로 리다이렉트된 뒤 다시 로그인 페이지로 리다이렉트된다(로그인 세션 필요). API 키 유무나 코드 구현과 무관한 Steam 플랫폼 자체의 제약이다. 게다가 이 값은 URL 안에 `/`가 포함되어 있어 `encodeURIComponent`로 인코딩해도 FastAPI 라우팅(`/api/user/{username}`) 단계에서부터 매칭되지 않고 404가 난다 — Steam 리졸브 로직까지 도달하지도 못한다. **사용자에게 SteamID64 숫자, AccountID 숫자, 또는 `/id/커스텀URL`의 커스텀 부분을 직접 요청하는 편이 안전**하다 (공유 링크 입력에 대한 프론트엔드 안내 문구 추가는 4절 다음 작업 참고).

### 2.2 Bedrock API 키 형식

- AWS 콘솔에서 Bedrock API 키를 복사하면 `BedrockAPIKey-<key-id>,<실제 토큰>` 형태로 **Key ID 라벨과 실제 토큰이 콤마로 붙어서** 복사되는 경우가 있었다 (이번 세션에서 두 번 다 이 형태로 붙여넣어짐).
- 실제 `Authorization: Bearer` 헤더에 넣어야 하는 값은 `ABSK`로 시작하는 뒷부분뿐이다. base64로 디코딩하면 `BedrockAPIKey-<id>:<secret>` 형태가 나오는 것으로 확인했다.
- 이 문제를 코드에서 흡수하도록 `apps/was/app.py`에 `normalize_bedrock_api_key()`를 추가했다: `BEDROCK_API_KEY` 값에 콤마가 있으면 `ABSK`로 시작하는 조각만 자동으로 골라 쓴다. 따라서 **앞으로는 콘솔에서 복사한 값을 그대로(라벨+콤마+토큰) `.env`/`k8s/app-secrets.yaml`에 붙여넣어도 정상 동작**한다.

### 2.3 Bedrock 모델 ID

- 기본값으로 시도했던 `anthropic.claude-3-5-haiku-20241022-v1:0`, `anthropic.claude-3-5-sonnet-20241022-v2:0`, `anthropic.claude-3-7-sonnet-20250219-v1:0`, `amazon.titan-text-express-v1`은 현재 Bedrock에서 **"reached the end of its life"(404)** 로 더 이상 호출되지 않는다.
- `anthropic.claude-3-sonnet-20240229-v1:0`, 이후에는 `anthropic.claude-3-haiku-20240307-v1:0`까지도 시간이 지나며 "Legacy 모델이며 최근 30일간 사용 이력이 없어 접근 거부"로 막혔다 — **한 번 정상 호출되던 모델도 계정의 사용 이력에 따라 나중에 다시 막힐 수 있다는 뜻**이라 이 값에 의존하지 않는 편이 좋다.
- `anthropic.claude-sonnet-4-20250514-v1:0`처럼 최신 모델은 리전 접두어 없는 raw 모델 ID로는 호출이 안 되고 `Retry your request with the ID or ARN of an inference profile`로 거부된다 — **`us.` 접두어가 붙은 크로스 리전 추론 프로필 ID**(`us.anthropic.<model>`)로 호출해야 한다.
- **현재(2026-08-07 기준) 정상 동작 확인된 값: `us.anthropic.claude-haiku-4-5-20251001-v1:0`** — `apps/was/app.py`의 `BEDROCK_DEFAULT_MODEL_ID` 기본값을 이걸로 변경했다. 리전은 `us-east-1` 기본값 그대로 동작한다.
- 모델 가용성은 AWS 쪽에서 수시로(심지어 같은 세션 안에서도) 바뀔 수 있으므로, 나중에 다시 404/EOL/Legacy 에러가 나면 `BEDROCK_MODEL_ID` 환경 변수로 다른 모델(또는 다른 추론 프로필)을 지정해 우회할 수 있다 (코드 수정 불필요). 증상이 나타나면 이 문서의 curl 예시로 모델 ID 후보를 직접 순회 테스트해보는 것이 가장 빠르다.

### 2.4 실제 검증 결과

테스트 계정(SteamID64 `76561198828407430`, 보유 게임 2개, 누적 143시간)으로 확인:
- `games`, `hours` — Steam `GetOwnedGames` 기반 실제 값 정상 반환
- `achievements` — `N/A` (해당 계정의 보유 게임에 공개 업적 데이터가 없거나 비공개)
- `friends` — `0명` (해당 계정의 `GetFriendList`가 빈 목록 반환 — 친구 목록 비공개이거나 실제로 0명)
- `playstyle`/`insight` — Bedrock이 **실제 보유 게임 이름과 시간을 근거로** 생성한 문장으로 정상 대체됨 (기존 `random.choice` 목업이 아님을 확인)

---

## 3. 사용자 질문에 대한 답변 — "친구 조회 → 친구 목록 → 친구 스팀코드 확인 → 친구 검색" 플로우가 어려운가?

**아니다. 이미 자동으로 해결되어 있다.** `/api/friends/{username}`는 내부적으로 `ISteamUser/GetFriendList`를 호출하는데, 이 API가 각 친구의 **SteamID64를 직접** 돌려주기 때문에 사용자가 친구의 "스팀 코드"를 수동으로 찾아 다시 검색할 필요가 없다. WAS가 친구 목록을 받은 즉시 각 친구의 프로필/보유 게임/최근 플레이를 동시에(`asyncio.gather`) 조회해서 한 번에 응답한다.

실제로 막히는 지점은 따로 있다:
- **친구 목록 자체가 비공개인 계정**이면 `GetFriendList`가 애초에 빈 목록(또는 403)을 반환한다 — 이건 Steam 프라이버시 설정의 한계이며 API로 우회할 수 없다.
- 친구 목록은 공개이지만 **개별 친구의 프로필/보유 게임이 비공개**이면 해당 친구는 `build_real_friend_entry`에서 조용히 제외된다 (에러로 전체 요청이 실패하지 않도록 설계함).

즉 프론트엔드 UX 관점에서 "친구를 다시 검색"하는 수동 단계는 필요 없고, 유저 검색 한 번으로 공개된 친구 데이터까지 자동으로 딸려 온다. 다만 **테스트 계정처럼 친구 목록이 비어 있거나 비공개인 경우 friends 배열이 빈 상태로 응답**하는 것은 정상 동작이니, 프론트엔드에서 "공개된 친구가 없습니다" 같은 빈 상태 문구를 추가하는 것을 고려할 만하다 (4절 다음 작업 참고).

---

## 4. 다음 작업 목록

우선순위 순으로 정리 (사용자가 명시적으로 언급한 순서: 실 데이터 연동 → 하드코딩 제거 확인 → 프론트엔드 조정 → TS 전환 여부는 별도 검토 완료).

1. **[완료] 하드코딩 → 실 데이터 전환**: WAS 쪽 완료. Bedrock이 대체하는 필드(`playstyle`, `insight`, 친구 `trait`)는 Bedrock 호출이 실패/미설정이어도 더 이상 `random.choice(...)` 하드코딩 값으로 채우지 않고 `null`로 남긴다 — 프론트가 실제로 생성된 값인지 아닌지 구분할 수 있게 됨. AccountID(9~10자리 "친구 코드") 입력도 SteamID64로 자동 변환하도록 `resolve_steam_id()`에 추가함 (2.1절 참고).
2. **프론트엔드 조정 작업** (`apps/web/index.html`):
   - `playstyle`/`insight`/친구 `trait`가 이제 `null`로 올 수 있다 — 현재 프론트는 `if (data.playstyle) ...`로 이미 방어되어 있어 크래시는 안 나지만, "생성 실패" 같은 명시적 문구 대신 그냥 이전 값이 유지되는 상태라 사용자에게 혼란을 줄 수 있음
   - `achievements`가 `"N/A"`로 올 때의 표시 처리 (현재는 숫자/퍼센트를 그대로 `textContent`에 꽂는 구조라 `"N/A"`도 그대로 표시는 되지만, 스타일링/문구 조정 여지가 있음)
   - `friends` 배열이 비어 있을 때("공개된 친구가 없습니다" 등 빈 상태 UI) — 현재는 친구 탭에 빈 리스트가 그대로 렌더링됨
   - Steam API 실패(404/502) 시 프론트의 기존 `catch` 블록이 로컬 목업(정적 HTML에 박혀있는 예시 값: 247개/1,842h/68%/184명 등)으로 폴백하는데, 이 폴백이 "실 데이터 실패"와 "설정 안 됨(목업 모드)"를 구분 없이 같은 문구로 보여준다 — 사용자에게 더 명확한 상태 메시지가 필요한지 검토
   - **공유 링크(`s.team/...`) 등 슬래시가 포함된 입력에 대한 안내**: 이런 입력은 URL 인코딩되더라도 FastAPI 라우팅 단계에서부터 매칭되지 않아 조용히 404로 실패하고 로컬 목업으로 넘어간다. 검색창에 "SteamID64 숫자, 친구 코드(AccountID) 숫자, 또는 `/id/커스텀URL`만 입력하세요" 같은 안내 문구/입력 검증을 추가하는 것을 고려
   - 글로벌 트렌드 탭은 여전히 프론트 하드코딩 데이터 그대로(백엔드 엔드포인트 없음) — 이번 범위에 포함할지 확인 필요
3. **TypeScript 전환**: [1번 문서](./1_frontend-typescript-migration-review.md)에서 이미 검토 완료. 결론은 "Vanilla TS + 경량 번들러"로 최소 구조 변경만으로 가능하며, 실 데이터 연동 작업과 순서 의존성 없음. 프론트엔드 조정 작업과 동시에 진행할지, 조정 후에 진행할지는 아직 미정.
4. **비용/지연 관리**: 유저 1명 조회당 Bedrock 호출이 최소 1회(인사이트) + 친구 수만큼(친구 트레잇) 발생한다. 트래픽이 늘어나면 RDS(`request_counter`용으로 이미 연결돼 있는 MySQL)에 유저별 인사이트를 캐싱하는 테이블을 추가하는 것을 고려할 만하다.
5. **보안 — 키 재발급 권고(반복 강조)**: 이번 세션 중 Steam API 키와 Bedrock API 키(구/신 2개) 값이 모두 대화 로그에 평문으로 노출되었다. 실서비스 배포 전 반드시 AWS/Steam 콘솔에서 재발급하고, `.env`/`k8s/app-secrets.yaml`을 새 값으로 갱신할 것.
