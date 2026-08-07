aws_region   = "us-east-2"
project_name = "de-ai-07-eks-auto"
environment  = "dev"

kubernetes_version = "1.35"

# 운영 환경에서는 반드시 본인/회사 공인 IP만 허용
cluster_endpoint_public_access_cidrs = ["0.0.0.0/0"]

additional_admin_role_arns = []

# 비용을 더 낮추려면 Single-AZ로 변경할 수 있지만 현재는 v2와 동일한 Multi-AZ 효과를 유지
db_instance_class    = "db.t3.micro"
db_allocated_storage = 20

# ------------------------------------------------------------
# GitHub Actions CI, OIDC 관련 추가
# ------------------------------------------------------------
enable_github_actions_ci = true
github_owner             = "hosose"           # 본 프로젝트의 깃허브의 소유주 명
github_ci_repository     = "devops_tf_k8s_ci" # 본 프로젝트 저장소 이름
github_ci_branch         = "main"             # 어떤 브런치에서만 ECR push를 위한 인증 허가할것인가

# 아래 오류 나면 false로 설정 (이미 있음)
#│ Error: creating IAM OIDC Provider: operation error IAM: CreateOpenIDConnectProvider, https response error StatusCode: 409, RequestID: 50cef171-e45d-48bf-a7ea-bff005d1aa6f, EntityAlreadyExists: Provider with url https://token.actions.githubusercontent.com already exists.
#│ 
#│   with aws_iam_openid_connect_provider.github_actions[0],
#│   on github-actions-ci.tf line 28, in resource "aws_iam_openid_connect_provider" "github_actions":
#│   28: resource "aws_iam_openid_connect_provider" "github_actions" {
# 최초라면 true, 만약 1번 이상 수행 => aws내 iam 본인 계정에 등록되어있다면 false로 설정
create_github_oidc_provider = false
# 조회발급 -> 추후 세팅 => git 로그인 처리후 조회 => 모두 숫자임
github_owner_id         = "112751572"
github_ci_repository_id = "1324578512"
