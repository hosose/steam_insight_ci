# 목표
- CI Workflow 구성
  ```
    # 소스 변경 => push => GitHub Actions => 이미지 생성 => 임시인증(OIDC) => ECR 업데이트 : devops관련
    # 최초 1회 인프라 구성(테라폼) => 변경시 반영됨
    devops_tf_k8s_ci -> GitHub Actions -> AWS ECR 
  ```
- 지금까지 구성한 인프라(aws 기반 테라폼구성)와 플랫폼/서비스(쿠버네티스) 위에 Devops 올려서 형태 최종 완성

- 세부적인 workflow
```
        개발자 Push/PR
            │
            ▼
        GitHub Actions
            │
            ├─ Terraform fmt / validate / apply  # 인프라 구성/변경사항 반영

            ├─ WEB Docker 이미지 빌드              # web, was 이미지 생성 병렬 구성
            ├─ WEB /health 테스트
            ├─ WAS Python 문법 검사
            ├─ WAS Docker 이미지 빌드
            ├─ WAS /health 테스트
            
            ├─ GitHub OIDC로 AWS 임시 인증         # pem 사용 x, OIDC 임시 인증서로 발급받아서 로그인
            
            ├─ WEB 이미지 ECR Push                # 이미지 푸시 -> 태그 수정(해시값,commit id등) -> CD 작동의 근거가됨
            ├─ WAS 이미지 ECR Push
            └─ ECR 이미지 등록 확인
```
- devops_tf_k8s_ci 는 CI/CD 구성에서 `CI 영역만 담당하는 소스 저장소(source repository)` 임

# 프로젝트 구조
```text
        devops-tf-k8s-ci/
        ├─ setup-ci.bat             # 신규 1회 수행 : Windows CI 전체 최초 설정
        ├─ setup-ci.sh              # 신규 1회 수행 : macOS/Linux/WSL CI 전체 최초 설정
        ├─ validate-ci.bat          # 신규 수시 수행 : Windows 로컬 CI 재검증 
        ├─ validate-ci.sh           # 신규 수시 수행 : macOS/Linux/WSL 로컬 CI 재검증
        ├─ project.bat              # 신규 인프라 구성시/변경되면 수행 : Windows 전체 명령 진입점
        ├─ project.sh               # 신규 인프라 구성시/변경되면 수행 : macOS/Linux/WSL 전체 명령 진입점
        ├─ .github/                 # 신규
        │  ├─ workflows/ci.yml      # 신규 GitHub Actions CI Workflow
        │  └─ dependabot.yml        # 신규 GitHub Actions 버전 점검
        ├─ apps/
        │  ├─ web/                  # Nginx 소스와 Dockerfile
        │  └─ was/                  # FastAPI 소스와 Dockerfile
        ├─ infra/
        │  ├─ github-actions-ci.tf  # 신규 GitHub OIDC, CI IAM Role, ECR Push 정책
        │  ├─ variables.tf          # 수정 CI 입력변수 추가 (OIDC, Git Action, Github 관련)
        │  ├─ outputs.tf            # GitHub Actions Role ARN 출력 추가
        │  └─ ...                   # 기존 인프라 구성 파일 동일
        ├─ scripts/
        │  ├─ ci/
        │  │  ├─ verify_ci_config.py # CI 파일과 terraform.tfvars 사전 검사
        │  │  ├─ linux/             # macOS/Linux/WSL CI 실행 로직
        │  │  └─ windows/           # Windows CMD CI 실행 로직
        │  ├─ deploy/               # 최초 수동 배포가 필요할 때 사용하는 로직
        │  ├─ ops/                  # 상태 확인과 전체 삭제
        │  └─ setup/                # 실습 환경 설치
        ├─ tools/
        └─ docs/
```

- 전체 저장소 배치
  ```
    devops_tf_k8s/
    L devops_tf_k8s_ci
    L devops_tf_k8s_cd
  ```


# 세팅
## OIDC 처리 (AWS 인증 방식 변경)
- 절차
  - 기존 : pem(엑세스키)
  - 변경 : `OIDC`
    - OIDC(OpenID Connect)는 ⁠OAuth 2.0 프로토콜을 바탕으로 만든 신원 인증 표준
    - 절차 : Git Action에서 ECR에 Push할때 단기 AWS 자격인증 발급받아서 로그인 수행 -> 보안 이슈
    - IAM 계정별 엑세스키(개발 PC는 사용), PEM 파일 (git 사용 x)

- workflow
```
    GitHub Actions
          │
          │ OIDC Token 발급
          ▼
    GitHub OIDC Provider
          │
          │ sts:AssumeRoleWithWebIdentity
          ▼
    GitHub Actions CI IAM Role
          │
          │ ECR Push 권한
          ▼
    WEB ECR / WAS ECR
```

- 수정 및 추가작업
  - infra/ 내 수정및 추가작업
    - terraform.tfvars
  - git 관련 id 조회
    ```
      # github_owner_id, github_ci_repository_id 값 획득
      # gh 명령어 사용
      # 설치
        # 윈도우        
          winget install --id GitHub.cli
        # 맥        
          brew install gh

      # 공통
        # 로그인
        gh auth login
        ---
        GitHub.com
        HTTPS
        Login with a web browser
        Press Enter to open https://github.com/login/device in your browser
        로그인 수행 > continue > 발급퇸 키 8자리 붙여넣기 > continue
        `Au... github` 버튼 클릭 > 설정완료

      # ID 조회
      gh api repos/ucoccto/devops_tf_k8s_ci --jq "{owner_id: .owner.id, repository_id: .id, created_at: .created_at}"
      ---
      {
        "created_at": "2026-08-05T23:51:38Z",
        "owner_id": 173376075,
        "repository_id": 1324570033
      }
    ```

## 전체 인프라 구성 및 최초 web/was 이미지 빌드 -> ECR Push : ci worlflow 진행
- 최초 (인프라구성) / 수정된 인프라 배포
```
./project.sh deploy
./project.bat deploy
```

- ci 셋업 1회 진행
```
./setup-ci.bat ucoccto/devops_tf_k8s_ci 
./setup-ci.sh ucoccto/devops_tf_k8s_ci
```



- 오류 발생
```
## 🛠️ EKS / Terraform CI/CD 트러블슈팅 종합 리포트

### 1. Terraform Resource Already Exists 충돌 (ECR / IAM)

* **발생 에러:** `RepositoryAlreadyExistsException`, `EntityAlreadyExists`
* **원인:** AWS상에 이미 동일한 이름의 ECR 리포지토리(`was`) 및 IAM OIDC Provider가 생성되어 있으나, 테라폼 상태 파일(`tfstate`)에 관리 대상으로 등록되지 않아 생성 시도 중 충돌 발생.
* **해결 방법:** `terraform import` 명령어를 사용하여 기존 AWS 리소스를 테라폼 관리 상태로 가져와 동기화 수행.
```bash
terraform import aws_ecr_repository.was de-ai-07-eks-auto-dev/was
terraform import 'aws_iam_openid_connect_provider.github_actions[0]' arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com

```



---

### 2. Kustomize 배포 배치 스크립트 경로 탐색 오류

* **발생 에러:** `[ERROR] Kustomize file not found.`
* **원인:** Windows 배치 스크립트 내 GitOps(CD) 레포지토리 경로 탐색 구문에서 상위 디렉토리 이동(`..\`) 누락 및 폴더명 불일치로 잘못된 중복 경로(`...\devops_tf_k8s_ci\devops_tf_k8s_cd\...`) 참조.
* **해결 방법:** 배치 스크립트 내 `GITOPS_REPO_DIR` 설정 구문에 `..\`를 추가하고 실제 폴더명(`devops_tf_k8s_cd`)에 맞게 경로 수정.
```cmd
for %%I in ("%SOURCE_REPO_ROOT%\..\devops_tf_k8s_cd") do set "GITOPS_REPO_DIR=%%~fI"

```



---

### 3. K8s Service 매니페스트 Strict Decoding 오류

* **발생 에러:** `unknown field "metadata.monitoring"`
* **원인:** Service 매니페스트 파일 작성 중 Prometheus 모니터링 라벨/어노테이션 설정이 표준 필드가 아닌 `metadata` 최상위 들여쓰기 레벨에 잘못 기입됨.
* **해결 방법:** `metadata.monitoring` 구문을 삭제하고, `metadata.labels` 하위에 `app: was`를 맞춰 인프라 표준 라벨링 구조 적용.

---

### 4. ALB 접속 연결 시간 초과 (ERR_TIMED_OUT)

* **발생 에러:** 브라우저 접속 시 `ERR_TIMED_OUT` (응답 시간 초과)
* **원인:** Ingress를 통해 생성된 AWS ALB의 보안 그룹(Security Group) 인바운드 규칙에 HTTP(80) 포트 접근 권한이 누락되었거나, ALB 연결 설정 문제로 외부 트래픽이 차단됨.
* **해결 방법:** kubectl delete ingress public-alb -n de-ai-07 이 명령어 치고 다시 deploy 실행

---

### 5. ALB 백엔드 서비스 미발견 오류 (Backend service does not exist)

* **발생 에러:** 브라우저 접속 시 `Backend service does not exist` 출력 (`<error: services "web" not found>`)
* **원인:** Ingress 매니페스트의 백엔드 서비스 참조 이름(`name: web`)과 실제 Kubernetes에 생성된 Service 리소스의 이름(`metadata.name: web-service`) 불일치.
* **해결 방법:** `ingress.yaml`의 백엔드 서비스 이름을 실제 Service 이름인 `web-service`로 수정 후 재배포.
```yaml
backend:
  service:
    name: web-service # web -> web-service 변경
    port:
      number: 80

```