# 1. 프론트엔드 TypeScript 전환 검토

작성일: 2026-08-07
대상: `apps/web` (현재 Vanilla JS 단일 파일 SPA)

이번 문서는 코드를 바꾸지 않고 **"apps/web을 TypeScript로 전환하면 큰 구조 변경이 필요한가?"** 에 대한 검토 결과만 정리한다. 실제 전환 작업(실 Steam 데이터 연동, 하드코딩 값 제거, 프론트 구조 조정)은 이후 세션에서 진행한다.

---

## 1. 결론 먼저

**일부 구조 변경은 불가피하지만, 전체 아키텍처를 갈아엎을 필요는 없다.**

- 불가피한 변경: 지금은 **빌드 도구가 전혀 없는 상태**(브라우저가 `index.html` 안의 `<script>`를 그대로 실행)이기 때문에, TypeScript를 쓰려면 최소한 "컴파일/번들 스텝 + Node 툴체인 + Dockerfile 멀티스테이지 빌드"가 새로 필요하다. 이건 TS를 어떤 방식으로 도입하든 피할 수 없다.
- 불필요한 변경: 현재 앱 규모(정적 페이지 3개, `data-page` 토글 방식 클라이언트 라우팅, `fetch` 기반 API 호출, DOM 직접 조작)를 고려하면 **React/Vue 같은 프레임워크로의 전면 리라이트는 과도**하다. 기존 로직 구조(전역 `state` 객체, 템플릿 리터럴 렌더링)를 그대로 둔 채 타입만 씌우는 "Vanilla TS" 접근으로도 목적(타입 안전성, API 응답 스키마 검증)을 충분히 달성할 수 있다.

즉, **"빌드 파이프라인 도입"은 구조 변경, "프레임워크 도입"은 선택 사항**으로 나눠서 봐야 한다.

---

## 2. 현재 상태

- `apps/web/index.html` 한 파일에 HTML + `<style>` CSS + `<script>` Vanilla JS가 전부 들어있음 (약 1,300줄).
- `package.json`, `tsconfig.json`, 번들러, 린터 등 **JS 툴체인이 전혀 없음**.
- `apps/web/Dockerfile`은 단일 스테이지로 `nginx:stable-alpine`에 `index.html`/`nginx.conf`/`assets`를 그대로 `COPY`.
- `scripts/ci/*/validate.sh|.bat`는 `docker build`로 web 이미지를 빌드한 뒤 `/health`만 확인 — **소스 자체의 빌드/린트 검증은 없음**.
- 상태 관리: 전역 `state` 객체(`currentPage`, `activeUser`, `selectedFriendIndex`, `activeFriendTab`) + `document.getElementById` 직접 조작 + `innerHTML` 템플릿 리터럴로 렌더링.
- WAS와의 계약: `fetch('/api/user/{username}')`, `fetch('/api/friends/{username}')` 두 곳뿐이며, 응답 JSON 형태에 대한 타입 정의가 전혀 없다 (런타임에 `data.metrics.games`처럼 옵셔널 체이닝 없이 접근).

---

## 3. 옵션 비교

### 옵션 A — Vanilla TypeScript + 경량 번들러 (권장)

기존 구조를 유지하면서 타입만 도입한다.

- 도구: TypeScript 컴파일러 + `esbuild` 또는 `Vite`(라이브러리 모드) 중 하나만 추가.
- 소스 분리: `index.html`의 `<script>` 내용을 `src/main.ts`, `src/api.ts`(fetch 래퍼 + 응답 타입), `src/render/*.ts`(페이지별 렌더 함수) 정도로만 쪼갠다. CSS는 그대로 두거나 `src/styles.css`로 분리 가능(선택).
- 런타임 동작: 지금과 동일 (data-page 토글, innerHTML 렌더링, fetch 호출) — **로직을 다시 설계할 필요 없음**, 타입만 얹는 리팩터링 수준.
- 변경이 필요한 파일:
  - 신규: `package.json`, `tsconfig.json`, 빌드 스크립트, `src/` 디렉터리
  - `apps/web/Dockerfile`: 단일 스테이지 → **멀티스테이지**로 변경 필요
    ```dockerfile
    FROM node:20-alpine AS build
    WORKDIR /app
    COPY package*.json .
    RUN npm ci
    COPY . .
    RUN npm run build      # -> dist/index.html, dist/assets/*.js

    FROM nginx:stable-alpine
    COPY nginx.conf /etc/nginx/nginx.conf
    COPY --from=build /app/dist /usr/share/nginx/html
    ```
  - `.dockerignore`: `node_modules/`, `dist/` 등 추가
  - `nginx.conf`: `root /usr/share/nginx/html;` 자체는 안 바뀌지만, 정적 파일이 `dist/` 산출물로 바뀌므로 빌드 산출물 경로와 일치해야 함
  - CI: `scripts/ci/linux/validate.sh` / `windows/validate.bat`는 **그대로 동작** — `docker build`가 멀티스테이지 안에서 `npm ci && npm run build`까지 알아서 수행하므로 CI 스크립트 자체는 손댈 필요가 거의 없음(빌드 시간 증가만 감안). 다만 소스 레벨 타입 검사(`tsc --noEmit`)를 CI에 추가하고 싶다면 `validate.sh`에 한 단계를 추가해야 함(선택 사항).
- **구조 변경 규모: 중간.** Node 빌드 스텝과 파일 분리는 필요하지만, 화면 구조·라우팅·상태관리 로직은 그대로 이식된다.

### 옵션 B — 프레임워크 기반 리라이트 (React/Vue + TS + Vite)

- 컴포넌트 트리, 라우팅 라이브러리(또는 자체 라우팅), 상태관리(Context/Zustand 등)를 새로 설계해야 함.
- 3개 페이지 + 상세 카드/탭 정도 규모에서는 프레임워크가 주는 이점(컴포넌트 재사용, 선언적 렌더링)보다 **마이그레이션 비용이 더 큼**.
- 향후 페이지 수·상호작용이 크게 늘어날 계획이 명확할 때만 정당화됨.
- **구조 변경 규모: 큼.** 옵션 A의 빌드 파이프라인 변경 전부 + 렌더링/상태관리 전면 재작성.

---

## 4. 백엔드(WAS)와의 관계

- WAS는 Python/FastAPI이므로 **TS 전환 대상이 아니다.** "TS 전환"은 `apps/web`에만 해당.
- 다만 FastAPI는 `/openapi.json`을 자동 생성하므로, 프론트를 TS로 전환하는 시점에 `openapi-typescript` 같은 도구로 WAS 응답 타입을 자동 생성해 `src/api.ts`에서 재사용하면 프론트-백엔드 응답 스키마 불일치를 컴파일 타임에 잡을 수 있다. (지금 `/api/user`, `/api/friends`가 목업이라도, 응답 구조 자체는 실제 데이터 연동 이후에도 크게 바뀌지 않을 가능성이 높으므로 미리 타입을 고정해두는 것이 유효하다.)
- 진행 순서 관련: "하드코딩 값 → 실 Steam 데이터 연동"과 "TS 전환"은 서로 독립적이다. WAS 응답 JSON 필드 이름만 유지되면 어느 순서로 진행해도 상호 블로킹이 없다. 다만 **TS를 먼저 도입해 응답 타입을 고정해두면, 실 데이터 연동 시 필드 누락/타입 불일치를 더 빨리 발견**할 수 있어 TS를 먼저 하는 편이 약간 유리하다.

---

## 5. 권장안

1. **옵션 A(Vanilla TS + esbuild/Vite)** 채택을 권장한다. 현재 규모에 맞고, 기존 UI/UX·라우팅 로직을 그대로 재사용할 수 있어 리스크가 낮다.
2. 마이그레이션은 다음 순서로 진행하는 것을 제안한다 (실행은 이후 세션에서):
   1. `apps/web`에 `package.json` + `tsconfig.json` 추가, `index.html`의 `<script>` 블록을 `src/` 아래 모듈로 분리
   2. WAS 응답에 대응하는 타입(`UserAnalysis`, `FriendsResponse` 등)을 `src/types.ts`에 정의 — 가능하면 `/openapi.json` 기반 자동 생성 검토
   3. `esbuild`(또는 `vite build`)로 `dist/`에 번들 산출 확인 (로컬에서 `npm run build` 후 정적 파일로 직접 열어 동작 검증)
   4. `apps/web/Dockerfile`을 멀티스테이지로 변경, `docker compose up --build`로 기존과 동일하게 `localhost:3080`에서 동작하는지 재검증
   5. 필요 시 `scripts/ci/*/validate.sh|.bat`에 `tsc --noEmit` 단계 추가 검토
3. 프레임워크 도입(옵션 B)은 지금 시점에서는 보류하고, 페이지/컴포넌트 수가 실제로 늘어나는 시점에 재검토한다.
