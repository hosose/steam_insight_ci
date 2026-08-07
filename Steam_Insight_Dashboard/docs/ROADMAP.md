# Steam Insight Dashboard - Development & Deployment Roadmap

본 문서는 `Steam Insight Dashboard`의 로컬 개발 환경 구성 완료 이후부터 최종 AWS EC2 및 Kubernetes 환경 배포까지의 순차적 개발 및 인프라 구축 단계를 정의합니다.

## Phase 0: 사전 준비 및 환경 고도화 (현재 단계)

본격적으로 API 연동을 시작하기 전, 프로젝트의 안정성을 위해 점검하고 세팅해야 할 사전 단계입니다.

1. **상태 관리 및 데이터 페칭 라이브러리 세팅 (Web)**
   - API 데이터 통신의 효율성과 캐싱을 위해 `TanStack Query (React Query)` 또는 `SWR` 설정
   - 글로벌 상태가 필요한 경우 `Zustand` 등 가벼운 라이브러리 도입
2. **API 클라이언트 구성 (Web)**
   - `Axios` 인스턴스 생성 및 공통 Error Handler, Interceptor (토큰 등 헤더 주입) 세팅
3. **환경 변수 관리 체계화**
   - 로컬(`dev`), 스테이징(`stg`), 프로덕션(`prod`) 환경별 `.env` 분리 및 검증 로직 추가
4. **코드 품질 관리 (Web/WAS 공통)**
   - `ESLint`, `Prettier` 및 `Husky` + `lint-staged` 훅을 활용한 커밋 컨벤션/포맷팅 강제화

---

## Phase 1: 하드코딩 제거 및 API 실시간 연동 (Step 1)

프론트엔드에 하드코딩된 더미 데이터를 실제 백엔드 API로부터 받아오도록 변경하는 단계입니다.

1. **API 명세서 작성 및 공유**
   - 백엔드(WAS) 쪽에 `Swagger` 등을 설정하여 프론트엔드가 참고할 수 있는 명세서 확보
2. **데이터 모델 및 타입 정의 (Web)**
   - API 응답 구조에 맞춘 TypeScript Interface / Type 지정
3. **컴포넌트 데이터 바인딩 (Web)**
   - 기존 하드코딩된 UI 컴포넌트를 API 응답 데이터로 대체
   - 로딩(Loading) 스켈레톤 UI 및 에러(Error) 바운더리 적용
4. **Steam API 실시간 호출 로직 구현 (WAS)**
   - WAS에서 외부 Steam API를 호출하고 데이터를 가공(Parsing)하여 프론트엔드로 전달하는 컨트롤러/서비스 계층 구현

---

## Phase 2: 백엔드 고도화 및 DB 연동

실시간 API의 속도 저하를 막기 위해 데이터를 캐싱하거나 유저 데이터를 저장하는 단계입니다.

1. **데이터베이스 연결 (WAS)**
   - PostgreSQL, MySQL 또는 MongoDB 연동 (필요시 Prisma, TypeORM 등 ORM 사용)
2. **캐싱 레이어 도입 (WAS)**
   - Steam 외부 API 호출 횟수 제한(Rate Limit) 방지 및 속도 향상을 위한 `Redis` 도입
3. **비즈니스 로직 최적화**
   - 배치(Batch) 작업이 필요한 데이터(예: 일간 통계 등)에 대한 스케줄러(Cron) 작성

---

## Phase 3: 운영(Production) 환경을 위한 컨테이너 최적화

로컬 개발용 `docker-compose.dev.yml`이 아닌, 실제 배포를 위한 Docker 이미지 빌드 최적화 단계입니다.

1. **프로덕션용 Dockerfile 작성 (Web/WAS)**
   - **Web**: Next.js Standalone 빌드를 활용한 경량화 및 Multi-stage build (`target: runner`)
   - **WAS**: `devDependencies`를 제외한 프로덕션 전용 빌드
2. **프로덕션용 docker-compose.yml 작성**
   - 로컬 마운트(Volume)를 제거하고 포트 매핑 및 환경변수를 배포용으로 구성

---

## Phase 4: CI/CD 파이프라인 구축 (GitHub Actions)

코드가 푸시되면 자동으로 테스트, 빌드, 이미지 푸시가 이루어지는 파이프라인 구성입니다.

1. **지속적 통합 (CI)**
   - PR 생성 시 Lint, Type Check, Unit Test 자동 수행
2. **컨테이너 레지스트리 (CR) 연동**
   - AWS ECR 또는 Docker Hub로 빌드된 이미지를 자동 Push하는 Action 작성

---

## Phase 5: AWS EC2 인프라 프로비저닝 및 Kubernetes 클러스터 구성

최종 목표인 쿠버네티스 환경을 AWS EC2에 구성하는 단계입니다.

1. **AWS EC2 인스턴스 생성 및 설정**
   - 보안 그룹(Security Group) 설정: HTTP(80), HTTPS(443), 쿠버네티스 API 포트 등 개방
   - VPC 및 서브넷 구성
2. **Kubernetes 클러스터 셋업**
   - **옵션 A (Managed):** AWS EKS를 사용하여 컨트롤 플레인을 AWS에 위임 (권장, 단 비용 발생)
   - **옵션 B (Self-hosted):** EC2 인스턴스 위에 `K3s` 또는 `Minikube`를 직접 설치하여 가벼운 클러스터 구성
3. **필수 애드온 설치 (K8s)**
   - `Ingress Controller` (NGINX 등), `Cert-Manager` (HTTPS 인증서), `Metrics Server` 등

---

## Phase 6: Kubernetes 리소스 배포 (최종 배포)

애플리케이션을 K8s 클러스터에 배포하고 외부로 서비스하는 단계입니다.

1. **K8s Manifest 작성 (YAML)**
   - `Deployment`: Web 및 WAS Pod의 레플리카(Replica) 설정
   - `Service`: Pod간 네트워크 연결 (ClusterIP)
   - `ConfigMap` / `Secret`: 환경 변수 및 중요 키(Steam API Key 등) 관리
   - `Ingress`: 외부 트래픽을 Web 또는 WAS로 라우팅 (도메인 연결)
2. **배포 자동화 (CD)**
   - `ArgoCD` 등을 도입하여 Git 저장소의 Manifest 변경 사항을 감지하고 클러스터에 자동 배포(GitOps)
3. **모니터링 및 로깅 설정**
   - Prometheus & Grafana 모니터링 대시보드 구축
