#!/usr/bin/env python3
"""tf-k8s-ci의 필수 CI 파일과 terraform.tfvars 설정을 사전 점검한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ASSIGNMENT_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("(?:[^"\\]|\\.)*"|true|false|[-+]?\d+(?:\.\d+)?)\s*(?:#.*)?$'
)
PLACEHOLDERS = {
    "YOUR_GITHUB_ID",
    "GITHUB_ID",
    "CHANGE_ME",
    "REPLACE_ME",
    "본인_GITHUB_ID",
}


def parse_scalar(value: str) -> object:
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return json.loads(value)
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def read_tfvars(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT_RE.match(line)
        if match:
            values[match.group(1)] = parse_scalar(match.group(2))
        elif any(key in line for key in ("enable_github_actions_ci", "github_owner", "github_ci_repository", "github_ci_branch")):
            raise ValueError(f"{path}:{line_number}: CI 변수 형식을 해석할 수 없습니다: {line}")
    return values


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)
    print(f"[ERROR] {message}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="tf-k8s-ci CI 설정 사전 점검")
    parser.add_argument("--root", type=Path, default=None, help="프로젝트 루트 경로")
    parser.add_argument("--repo", default=None, help="검사할 GitHub 저장소 OWNER/REPO")
    args = parser.parse_args()

    root = args.root.resolve() if args.root else Path(__file__).resolve().parents[2]
    tfvars_path = root / "infra" / "terraform.tfvars"
    errors: list[str] = []

    required_files = [
        ".github/workflows/ci.yml",
        ".github/dependabot.yml",
        "infra/github-actions-ci.tf",
        "infra/ecr.tf",
        "apps/web/Dockerfile",
        "apps/was/Dockerfile",
        "setup-ci.sh",
        "setup-ci.bat",
        "validate-ci.sh",
        "validate-ci.bat",
        "scripts/ci/linux/setup-all.sh",
        "scripts/ci/linux/configure-github-variables.sh",
        "scripts/ci/linux/validate.sh",
        "scripts/ci/linux/verify-github.sh",
        "scripts/ci/windows/setup-all.bat",
        "scripts/ci/windows/configure-github-variables.bat",
        "scripts/ci/windows/validate.bat",
        "scripts/ci/windows/verify-github.bat",
    ]

    for relative_path in required_files:
        if not (root / relative_path).is_file():
            fail(f"필수 CI 파일이 없습니다: {relative_path}", errors)

    workflow_path = root / ".github" / "workflows" / "ci.yml"
    if workflow_path.is_file():
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow_markers = {
            "이동된 로컬 검증 스크립트 호출": "bash scripts/ci/linux/validate.sh",
            "OIDC Token 권한": "id-token: write",
            "AWS OIDC 인증 Action": "aws-actions/configure-aws-credentials@",
            "Amazon ECR 로그인 Action": "aws-actions/amazon-ecr-login@",
            "Docker Build/Push Action": "docker/build-push-action@",
            "WEB/WAS Matrix": "app: [web, was]",
        }
        for description, marker in workflow_markers.items():
            if marker not in workflow_text:
                fail(f"ci.yml에서 필수 CI 단계가 누락되었습니다: {description}", errors)

    ci_terraform_path = root / "infra" / "github-actions-ci.tf"
    if ci_terraform_path.is_file():
        ci_terraform_text = ci_terraform_path.read_text(encoding="utf-8")
        terraform_markers = {
            "GitHub OIDC Provider": 'resource "aws_iam_openid_connect_provider" "github_actions"',
            "AssumeRoleWithWebIdentity 신뢰 정책": "sts:AssumeRoleWithWebIdentity",
            "GitHub Actions CI IAM Role": 'resource "aws_iam_role" "github_actions_ci"',
            "ECR Push 권한": "ecr:PutImage",
            "IAM Role 정책 연결": 'resource "aws_iam_role_policy" "github_actions_ci"',
        }
        for description, marker in terraform_markers.items():
            if marker not in ci_terraform_text:
                fail(f"github-actions-ci.tf에서 필수 구성이 누락되었습니다: {description}", errors)

    if not tfvars_path.is_file():
        fail("infra/terraform.tfvars 파일이 없습니다.", errors)
        return 1

    try:
        values = read_tfvars(tfvars_path)
    except (OSError, ValueError) as exc:
        fail(str(exc), errors)
        return 1

    enabled = values.get("enable_github_actions_ci")
    owner = str(values.get("github_owner", "")).strip()
    repository = str(values.get("github_ci_repository", "")).strip()
    branch = str(values.get("github_ci_branch", "")).strip()

    if enabled is not True:
        fail("enable_github_actions_ci = true로 설정해야 CI Role과 정책이 생성됩니다.", errors)

    if not owner:
        fail("github_owner 값이 비어 있습니다.", errors)
    elif owner.upper() in PLACEHOLDERS or "YOUR_" in owner.upper():
        fail("github_owner를 실제 GitHub 사용자명 또는 Organization명으로 변경하세요.", errors)
    elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        fail(f"github_owner 형식이 올바르지 않습니다: {owner}", errors)

    if not repository:
        fail("github_ci_repository 값이 비어 있습니다.", errors)
    elif not re.fullmatch(r"[A-Za-z0-9._-]+", repository):
        fail(f"github_ci_repository 형식이 올바르지 않습니다: {repository}", errors)

    if not branch:
        fail("github_ci_branch 값이 비어 있습니다.", errors)
    elif any(part in branch for part in (" ", "..", "~", "^", ":", "?", "*", "[", "\\")):
        fail(f"github_ci_branch 형식이 올바르지 않습니다: {branch}", errors)

    configured_repo = f"{owner}/{repository}" if owner and repository else ""
    if args.repo and configured_repo and args.repo.lower() != configured_repo.lower():
        fail(
            f"명령 인자 저장소({args.repo})와 terraform.tfvars 저장소({configured_repo})가 다릅니다.",
            errors,
        )

    if errors:
        print(f"\nCI 사전 점검 실패: {len(errors)}개 항목을 수정해야 합니다.", file=sys.stderr)
        return 1

    print("[OK] GitHub Actions Workflow: .github/workflows/ci.yml")
    print("[OK] GitHub OIDC/IAM Terraform: infra/github-actions-ci.tf")
    print("[OK] WEB/WAS Docker Build 대상 확인")
    print(f"[OK] CI 허용 저장소: {configured_repo}")
    print(f"[OK] CI 허용 브랜치: {branch}")
    print("CI 사전 설정 점검 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
