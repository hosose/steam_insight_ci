# 스인싸 (Steam Insight) 🎮

> **Steam 프로필 인텔리전스 플랫폼** — Steam 공개 데이터를 기반으로 유저의 플레이 취향, 친구 네트워크, 글로벌 게임 트렌드를 분석합니다.

![Stack](https://img.shields.io/badge/Frontend-Next.js_15-black?logo=next.js)
![Stack](https://img.shields.io/badge/Backend-Express_+_TypeScript-blue?logo=express)
![Stack](https://img.shields.io/badge/Style-Tailwind_CSS_v4-38BDF8?logo=tailwindcss)
![Stack](https://img.shields.io/badge/Container-Docker-2496ED?logo=docker)
![Stack](https://img.shields.io/badge/AI-AWS_Bedrock-FF9900?logo=amazon-aws)

---

## 📌 프로젝트 개요

**스인싸(Steam Insight)**는 Steam Web API를 통해 공개된 데이터를 수집·분석하여, 아래 세 가지 핵심 인사이트를 제공하는 대시보드 서비스입니다.

| 기능 | 설명 |
|------|------|
| 🔍 **유저 검색** | Steam ID / 프로필 URL / 커스텀 ID로 공개 프로필 분석. 보유 게임, 누적 플레이, 업적 달성률, 플레이 취향을 도출 |
| 🌍 **글로벌 트렌드** | Steam 동시접속자 수·할인 정보·급상승 게임을 실시간으로 탐색. 탭별로 인기순/할인순/카테고리별 필터 지원 |
| 👥 **친구 네트워크** | 기준 유저의 공개 친구 목록을 플레이타임 기준으로 분석. 함께 보유한 게임, 추천 함께 플레이 게임, 플레이 스타일 인사이트 제공 |

---

## 🏗️ 프로젝트 구조

```
Steam_Insight_new_version/
└── Steam_Insight_Dashboard/          # 메인 모노레포
    ├── app/
    │   ├── web/                      # 프론트엔드 (Next.js 15 + Tailwind CSS v4)
    │   │   ├── app/
    │   │   │   ├── page.tsx          # 메인 페이지 (SearchPage / GlobalPage / FriendsPage)
    │   │   │   ├── layout.tsx        # 루트 레이아웃
    │   │   │   ├── globals.css       # 글로벌 스타일
    │   │   │   ├── components/       # 공용 컴포넌트 (ImageWithFallback 등)
    │   │   │   └── imports/          # 정적 에셋 (로고 이미지 등)
    │   │   ├── Dockerfile
    │   │   ├── next.config.ts
    │   │   ├── tsconfig.json
    │   │   └── package.json          # sinsa-frontend
    │   │
    │   └── was/                      # 백엔드 (Express + TypeScript)
    │       ├── src/
    │       │   ├── index.ts          # Express 서버 진입점
    │       │   ├── config/
    │       │   │   └── env.ts        # 환경변수 스키마 (Zod 검증)
    │       │   ├── routes/
    │       │   │   ├── steam.routes.ts        # GET /api/steam/profile
    │       │   │   ├── trends.routes.ts       # GET /api/trends/global
    │       │   │   ├── influencers.routes.ts  # GET /api/influencers
    │       │   │   └── health.routes.ts       # GET /health (Docker healthcheck)
    │       │   └── services/
    │       │       └── steam.service.ts       # Steam API 연동 + Mock 데이터
    │       ├── Dockerfile
    │       ├── tsconfig.json
    │       └── package.json          # sinsa-backend
    │
    ├── docker-compose.yml            # 프로덕션 환경
    ├── docker-compose.dev.yml        # 개발 환경 (hot-reload 포함)
    ├── .env                          # 환경변수 (로컬 전용, Git 제외)
    └── package.json                  # 루트 스크립트 (concurrently 기반)
```

---

## 🛠️ 기술 스택

### Frontend (`app/web`)

| 항목 | 기술 |
|------|------|
| 프레임워크 | Next.js 15 (App Router) |
| UI 라이브러리 | React 19 |
| 스타일링 | Tailwind CSS v4 (`@tailwindcss/postcss`) |
| 언어 | TypeScript 5.7 |
| 패키지 매니저 | pnpm |

### Backend (`app/was`)

| 항목 | 기술 |
|------|------|
| 프레임워크 | Express 4 |
| 언어 | TypeScript 5.7 (ESM) |
| 유효성 검증 | Zod |
| 보안 미들웨어 | Helmet, CORS |
| 실행 도구 | tsx (개발), tsc + node (프로덕션) |

### 인프라 & 외부 서비스

| 항목 | 기술 |
|------|------|
| 컨테이너화 | Docker + Docker Compose |
| AI 분석 | AWS Bedrock |
| 게임 데이터 | Steam Web API |

---

## 🚀 시작하기

### 사전 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 또는 Docker Engine + Docker Compose
- (로컬 직접 실행 시) Node.js 22+, pnpm

### 1. 환경변수 설정

`Steam_Insight_Dashboard/` 디렉토리에 `.env` 파일을 생성합니다.

```env
# Steam Web API Key (https://steamcommunity.com/dev/apikey)
STEAM_API_KEY=your_steam_api_key_here

# Backend
PORT=4000
CORS_ORIGIN=http://localhost:3000

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:4000

# AWS Bedrock API Key (선택)
Bedrock_API_Key=your_bedrock_api_key_here
```

> **⚠️ 주의:** `.env` 파일은 절대 Git에 커밋하지 마세요. `.gitignore`에 이미 포함되어 있습니다.
>
> **ℹ️ 참고:** `STEAM_API_KEY`를 설정하지 않으면 Mock 데이터 모드로 동작합니다.

---

### 2. Docker로 실행 (권장)

#### 개발 환경 (Hot-reload 포함)

```bash
cd Steam_Insight_Dashboard

# 빌드 및 실행
docker compose -f docker-compose.dev.yml up -d --build

# 또는 루트에서
npm run docker:dev
```

#### 프로덕션 환경

```bash
cd Steam_Insight_Dashboard

docker compose up -d --build

# 또는 루트에서
npm run docker:prod
```

#### 종료

```bash
docker compose -f docker-compose.dev.yml down

# 또는 루트에서
npm run docker:down
```

---

### 3. 로컬 직접 실행 (Docker 없이)

```bash
cd Steam_Insight_Dashboard

# 의존성 설치
npm install

# 프론트엔드 + 백엔드 동시 실행
npm run dev
```

개별 실행:

```bash
# 백엔드만
npm run dev:backend   # http://localhost:4000

# 프론트엔드만
npm run dev:frontend  # http://localhost:3000
```

---

## 🌐 서비스 엔드포인트

### Frontend

| 주소 | 설명 |
|------|------|
| `http://localhost:3000` | 메인 대시보드 |

### Backend API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 서버 헬스체크 (Docker healthcheck 용) |
| `GET` | `/api/steam/profile?q={steamId}` | Steam 유저 프로필 조회 |
| `GET` | `/api/trends/global` | 글로벌 게임 트렌드 조회 |
| `GET` | `/api/influencers` | 인플루언서 목록 조회 |

---

## ⚙️ 주요 스크립트

`Steam_Insight_Dashboard/package.json` 기준:

| 커맨드 | 설명 |
|--------|------|
| `npm run dev` | 프론트엔드 + 백엔드 동시 실행 |
| `npm run dev:frontend` | 프론트엔드만 실행 |
| `npm run dev:backend` | 백엔드만 실행 |
| `npm run build` | 프론트엔드 + 백엔드 빌드 |
| `npm run docker:dev` | 개발용 Docker Compose 실행 |
| `npm run docker:prod` | 프로덕션용 Docker Compose 실행 |
| `npm run docker:down` | Docker Compose 종료 |

---

## 🔒 보안 정책

- Steam API를 통해 **공개(public)** 프로필 데이터만 조회합니다.
- 비공개 프로필 정보는 조회·저장하지 않습니다.
- 브라우저를 닫거나 새 검색 실행 시 분석 기준 유저가 초기화됩니다 (서버 저장 없음).
- `STEAM_API_KEY`가 없으면 자동으로 **Mock 모드**로 전환됩니다.

---

## 🚨 Git 관리 주의사항 (`.pnpm-store` 문제)

현재 `app/web/.pnpm-store/` 디렉토리(pnpm 로컬 패키지 캐시)가 Git에 이미 추적되어 있어 **10,000개 이상의 변경/미추적 파일**이 표시됩니다.

이를 해결하려면 아래 명령어를 실행하세요:

```bash
# 1. .gitignore에 추가 (이미 되어 있으면 생략)
echo "**/.pnpm-store/" >> .gitignore

# 2. Git 추적 캐시에서 제거 (파일 자체는 삭제되지 않음)
git rm -r --cached Steam_Insight_Dashboard/app/web/.pnpm-store/

# 3. 커밋
git add .gitignore
git commit -m "Fix: .pnpm-store를 git 추적에서 제거"
```

> **⚠️ 절대 `.pnpm-store/`를 커밋하지 마세요.** 이 디렉토리는 pnpm이 자동으로 관리하는 로컬 캐시이며, 수만 개의 바이너리 파일을 포함합니다.

---

## 📝 개발 현황

| 기능 | 상태 |
|------|------|
| Steam 프로필 검색 UI | ✅ 완료 |
| 글로벌 트렌드 UI | ✅ 완료 |
| 친구 네트워크 UI | ✅ 완료 |
| Docker 개발 환경 | ✅ 완료 |
| Steam API 실제 연동 | 🔧 개발 중 (현재 Mock 모드) |
| AWS Bedrock AI 분석 | 🔧 개발 중 |
| Steam Store API (할인·뉴스) | 📋 예정 |
| 인증 / 유저 저장 | 📋 예정 |

---

## 🤝 기여 방법

1. 이 저장소를 Fork합니다.
2. Feature 브랜치를 생성합니다: `git checkout -b feat/기능명`
3. 변경사항을 커밋합니다: `git commit -m "Feat: 기능 설명"`
4. 브랜치에 Push합니다: `git push origin feat/기능명`
5. Pull Request를 생성합니다.

---

## 📄 라이선스

이 프로젝트는 팀 내부 프로젝트로 운영됩니다. 외부 배포 시 별도 라이선스 정책을 적용합니다.

---

> 스인싸는 Steam에서 공개된 데이터를 조회 시점에 분석합니다. 비공개 정보는 조회·저장하지 않습니다.
