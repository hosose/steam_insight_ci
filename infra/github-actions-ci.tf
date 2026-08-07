# ─────────────────────────────────────────────
# Github Action이 장기키(pem, access key)없이 임시 자격 증명(OIDC)을 이용 인증 역활,권한등 처리
# 발급받아서 WEB/WAS 이미지를 ECR에 PUSH 목적 -> 보안 관리 이슈 x (깃상에 변수등이라도 존재 x)
# 
# 누가 발급한 인증 토큰을 신뢰할것인가?(발급 기관), Github에서 어떤 실행를 할때 인증 허가할것인가?
# 이 2개의 대한 내용을 role에 반영하여 arn 구성
# ─────────────────────────────────────────────

# 현재 어떤 사용자(IAM)/역활등이 실행중이지 => data => 조회후 채움
data "aws_caller_identity" "current" {}
# AWS 영역 구분값 조회
data "aws_partition" "current" {}

locals {
  # 위의 값을 조회하여 oidc를 발급하는 공급자의 arn 생성
  github_actions_oidc_provider_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"

  # IAM Role에서 사용가능한 Github 저장소, 브런치 지정 (소유자, 저장소, 브런치)
  # 2026년 7월 이후 변경된 주소 체계때문에 이전과 같이 배치 -> 보안강화됨
  #github_actions_ci_subject = "repo:${var.github_owner}/${var.github_ci_repository}:ref:refs/heads/${var.github_ci_branch}"
  github_actions_ci_subject = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_ci_repository}@${var.github_ci_repository_id}:ref:refs/heads/${var.github_ci_branch}"
}

# ─────────────────────────────────────────────
# oidc PROVIDER 생성. IAM Role을 이용하여 Github action을 사용하도록 신뢰연결
# ─────────────────────────────────────────────
# GitHub oidc PROVIDER 생성
resource "aws_iam_openid_connect_provider" "github_actions" {
  count = var.enable_github_actions_ci && var.create_github_oidc_provider ? 1 : 0

  url = "https://token.actions.githubusercontent.com" # 발급처 주소
  # sts : IAM Role의 임시 자격 증명 발급 서비스명
  client_id_list = ["sts.amazonaws.com"] # aws sts 사용 목적으로 발급한다 

  tags = {
    Name = "github-actions-oidc"
  }
}

data "aws_iam_policy_document" "github_actions_ci_assume" {
  count = var.enable_github_actions_ci ? 1 : 0

  # IAM에 정책을 하나 허용하겟다~ 
  statement {
    # 정책을 구분하는 이름 
    sid = "GitHubActionsAssumeRole"
    # 허가
    effect = "Allow"
    #  IAM Role의 임시 자격 증명 발급 서비스명
    actions = ["sts:AssumeRoleWithWebIdentity"]

    # AWS서비스가 아니라 외부 인증기관을 신뢰
    principals {
      type        = "Federated"
      identifiers = [local.github_actions_oidc_provider_arn]
    }
    # 조건 : 다른 서비스용 토큰 발급 거부
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # 조건 : 다른 저장소, 다른 브런치 Role 사용 x
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_actions_ci_subject]
    }
  }
}


# ─────────────────────────────────────────────
# ECR 권한 정책 반영 절차 -> 유효시간 1시간, 해당 IAM Role이 push 가동하도록(신뢰 정책 반영)
# ─────────────────────────────────────────────
resource "aws_iam_role" "github_actions_ci" {
  count = var.enable_github_actions_ci ? 1 : 0

  name        = "${local.cluster_name}-github-actions-ci-role"
  description = "GitHub Actions CI role for ECR image push"
  # 앞서 만든 신뢰정책(인증서 발급처 신뢰, 특정소유주의 저장소/브런치만 작동)을 본 IAM Role에 연결
  assume_role_policy = data.aws_iam_policy_document.github_actions_ci_assume[0].json
  # 최대시간 role이 유지되는 3600초 => 1시간
  max_session_duration = 3600
  depends_on           = [aws_iam_openid_connect_provider.github_actions]
}

data "aws_iam_policy_document" "github_actions_ci" {
  count = var.enable_github_actions_ci ? 1 : 0

  # 해당 OIDC 인증서(토큰), repo등 정보가 일치하는 유저의 ECRLogin 허가
  # ECR 로그인 허용 정책
  statement {
    sid    = "ECRLogin"
    effect = "Allow"
    # 도커가 ecr 로그인하는 토큰을 받은다
    actions = ["ecr:GetAuthorizationToken"]
    # ECR 제한 없음
    resources = ["*"]
  }

  # WEB/WAS 이미지 Push 권한 정책 허가
  statement {
    sid    = "PushApplicationImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability", # 동일  이미지 레이어가 이미 있는지 체크
      "ecr:BatchGetImage",               # 기존 이미지 조회
      "ecr:CompleteLayerUpload",         # 이미지 업로드 완료
      "ecr:DescribeImages",              # 등록된 이미지, 태그 조회
      "ecr:GetDownloadUrlForLayer",      # 이미지 다운로드 주소 조회
      "ecr:InitiateLayerUpload",         # 이미지 업로드 시작
      "ecr:PutImage",                    # 이미지 매니페스트, 테그 등록
      "ecr:UploadLayerPart"              # 이미지 부분 단위로 업로드
    ]
    # 위의 권한이 허가되는 이미지를 web, was용으로 제한
    resources = [
      aws_ecr_repository.web.arn, # web arn
      aws_ecr_repository.was.arn  # was arn
    ]
  }
}

# IAM Role에 ECR 정책 추가 연결
resource "aws_iam_role_policy" "github_actions_ci" {
  count = var.enable_github_actions_ci ? 1 : 0
  # 정책명
  name = "${local.cluster_name}-ecr-push-policy"
  # IAM Role
  role = aws_iam_role.github_actions_ci[0].id
  # 추가될 정책(ECR)
  policy = data.aws_iam_policy_document.github_actions_ci[0].json
}
