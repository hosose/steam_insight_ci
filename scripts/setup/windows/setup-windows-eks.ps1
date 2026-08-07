#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Windows 실습 PC에 WSL2 + Ubuntu + Docker Desktop을 설치하고,
  Ubuntu 내부에 Terraform, AWS CLI v2, kubectl, Python3를 설치합니다.

.DESCRIPTION
  project.sh deploy 실행에 필요한 다음 명령을 준비합니다.
    - terraform
    - aws
    - kubectl
    - docker
    - python3

  최초 WSL 설치 후 Windows 재부팅이 필요한 경우에는 재부팅 후
  이 파일을 다시 실행하세요.

.EXAMPLE
  PowerShell을 관리자 권한으로 실행한 후:

  Set-ExecutionPolicy -Scope Process Bypass
  .\scripts\setup\windows\setup-windows-eks.ps1

.EXAMPLE
  kubectl 저장소 버전을 변경하려면:

  .\scripts\setup\windows\setup-windows-eks.ps1 -KubectlMinorVersion "v1.35"
#>

[CmdletBinding()]
param(
    [ValidatePattern('^v\d+\.\d+$')]
    [string]$KubectlMinorVersion = 'v1.35',

    [string]$UbuntuDistribution = 'Ubuntu',

    [switch]$SkipDockerDesktop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Test-Command {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-WslDistributions {
    if (-not (Test-Command 'wsl.exe')) {
        return @()
    }

    $items = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    return @(
        $items |
            ForEach-Object { ($_ -replace "`0", '').Trim() } |
            Where-Object { $_ }
    )
}

function Test-WingetPackage {
    param([Parameter(Mandatory)][string]$Id)

    if (-not (Test-Command 'winget.exe')) {
        return $false
    }

    & winget.exe list --id $Id --exact --accept-source-agreements 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

Write-Step "1. Windows 환경 확인"

$os = Get-CimInstance Win32_OperatingSystem
Write-Host "Windows : $($os.Caption)"
Write-Host "Version : $($os.Version)"
Write-Host "Build   : $($os.BuildNumber)"

if (-not (Test-Command 'winget.exe')) {
    throw @"
winget 명령을 찾을 수 없습니다.
Microsoft Store에서 '앱 설치 관리자(App Installer)'를 설치하거나 업데이트한 후 다시 실행하세요.
"@
}

Write-Step "2. WSL2 및 Ubuntu 설치"

& wsl.exe --update
if ($LASTEXITCODE -ne 0) {
    Write-Warning "WSL 업데이트가 완료되지 않았습니다. Windows Update 상태를 확인하세요."
}

& wsl.exe --set-default-version 2
if ($LASTEXITCODE -ne 0) {
    throw "WSL2 기본 버전을 설정하지 못했습니다."
}

$distros = Get-WslDistributions
$selectedDistro = $distros |
    Where-Object { $_ -eq $UbuntuDistribution } |
    Select-Object -First 1

if (-not $selectedDistro) {
    $selectedDistro = $distros |
        Where-Object { $_ -like "$UbuntuDistribution*" } |
        Select-Object -First 1
}

if (-not $selectedDistro) {
    Write-Host "$UbuntuDistribution 배포판을 설치합니다."
    & wsl.exe --install --distribution $UbuntuDistribution

    $distros = Get-WslDistributions
    $selectedDistro = $distros |
        Where-Object { $_ -like "$UbuntuDistribution*" } |
        Select-Object -First 1

    if (-not $selectedDistro) {
        Write-Warning @"
WSL 또는 Ubuntu 설치 후 Windows 재부팅이 필요합니다.

1. Windows를 재부팅합니다.
2. 시작 메뉴에서 Ubuntu를 한 번 실행합니다.
3. Linux 사용자 이름과 비밀번호를 설정합니다.
4. 이 PowerShell 파일을 관리자 권한으로 다시 실행합니다.
"@
        exit 3010
    }
}

Write-Host "사용할 WSL 배포판: $selectedDistro"

& wsl.exe --set-version $selectedDistro 2
if ($LASTEXITCODE -ne 0) {
    throw "$selectedDistro 배포판을 WSL2로 설정하지 못했습니다."
}

Write-Host ""
Write-Host "Ubuntu 최초 사용자 설정 화면이 나타나면 사용자 이름과 비밀번호를 설정하세요." -ForegroundColor Yellow
Write-Host "설정이 끝나면 설치가 자동으로 계속됩니다." -ForegroundColor Yellow

& wsl.exe --distribution $selectedDistro -- bash -lc 'printf "[OK] Ubuntu 사용자: %s\n" "$USER"'
if ($LASTEXITCODE -ne 0) {
    throw "Ubuntu 초기 실행에 실패했습니다. 시작 메뉴에서 Ubuntu를 실행해 초기 설정을 완료하세요."
}

if (-not $SkipDockerDesktop) {
    Write-Step "3. Docker Desktop 설치"

    $dockerPackageId = 'Docker.DockerDesktop'

    if (Test-WingetPackage -Id $dockerPackageId) {
        Write-Host "[SKIP] Docker Desktop이 이미 설치되어 있습니다."
    }
    else {
        & winget.exe install `
            --id $dockerPackageId `
            --exact `
            --accept-package-agreements `
            --accept-source-agreements

        if ($LASTEXITCODE -ne 0) {
            throw "Docker Desktop 설치에 실패했습니다."
        }
    }

    $dockerDesktopCandidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Docker\Docker\Docker Desktop.exe')
    )

    $dockerDesktop = $dockerDesktopCandidates |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1

    if ($dockerDesktop) {
        $running = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
        if (-not $running) {
            Write-Host "Docker Desktop을 시작합니다."
            Start-Process -FilePath $dockerDesktop
        }
    }
    else {
        Write-Warning "Docker Desktop 실행 파일을 찾지 못했습니다. 설치 후 직접 실행하세요."
    }
}
else {
    Write-Step "3. Docker Desktop 설치 건너뜀"
    Write-Host "-SkipDockerDesktop 옵션이 지정되었습니다."
}

Write-Step "4. Ubuntu 내부 필수 도구 설치"

$linuxInstallScript = @'
#!/usr/bin/env bash
set -Eeuo pipefail

KUBECTL_MINOR="${1:-v1.35}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$1"
}

log "sudo 권한 확인"
sudo -v

log "Ubuntu 기본 패키지 설치"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  apt-transport-https \
  bash-completion \
  ca-certificates \
  curl \
  git \
  gnupg \
  lsb-release \
  unzip \
  wget \
  python3 \
  python3-pip

log "Terraform 저장소 등록 및 설치"
sudo install -m 0755 -d /usr/share/keyrings

curl -fsSL https://apt.releases.hashicorp.com/gpg \
  | sudo gpg --dearmor --yes \
      -o /usr/share/keyrings/hashicorp-archive-keyring.gpg

sudo chmod 0644 /usr/share/keyrings/hashicorp-archive-keyring.gpg

UBUNTU_CODENAME="$(
  grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release 2>/dev/null \
    || lsb_release -cs
)"

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${UBUNTU_CODENAME} main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y terraform

log "AWS CLI v2 설치"
case "$(uname -m)" in
  x86_64|amd64)
    AWS_CLI_ARCH="x86_64"
    ;;
  aarch64|arm64)
    AWS_CLI_ARCH="aarch64"
    ;;
  *)
    echo "[ERROR] 지원하지 않는 CPU 아키텍처: $(uname -m)" >&2
    exit 1
    ;;
esac

AWS_TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${AWS_TMP_DIR}"
}
trap cleanup EXIT

curl -fsSL \
  "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_CLI_ARCH}.zip" \
  -o "${AWS_TMP_DIR}/awscliv2.zip"

unzip -q "${AWS_TMP_DIR}/awscliv2.zip" -d "${AWS_TMP_DIR}"

if command -v aws >/dev/null 2>&1; then
  sudo "${AWS_TMP_DIR}/aws/install" \
    --bin-dir /usr/local/bin \
    --install-dir /usr/local/aws-cli \
    --update
else
  sudo "${AWS_TMP_DIR}/aws/install" \
    --bin-dir /usr/local/bin \
    --install-dir /usr/local/aws-cli
fi

log "kubectl ${KUBECTL_MINOR} 저장소 등록 및 설치"
sudo install -m 0755 -d /etc/apt/keyrings

curl -fsSL \
  "https://pkgs.k8s.io/core:/stable:/${KUBECTL_MINOR}/deb/Release.key" \
  | sudo gpg --dearmor --yes \
      -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

sudo chmod 0644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${KUBECTL_MINOR}/deb/ /" \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y kubectl

log "명령 자동 완성 설정"
if ! grep -Fq 'source <(kubectl completion bash)' "${HOME}/.bashrc"; then
  printf '\n# kubectl 자동 완성\nsource <(kubectl completion bash)\n' >> "${HOME}/.bashrc"
fi

if ! grep -Fq 'complete -C /usr/bin/terraform terraform' "${HOME}/.bashrc"; then
  terraform -install-autocomplete 2>/dev/null || true
fi

log "설치 결과"
printf '%-12s %s\n' "terraform" "$(terraform version | head -n 1)"
printf '%-12s %s\n' "aws"       "$(aws --version 2>&1)"
printf '%-12s %s\n' "kubectl"   "$(kubectl version --client --output=yaml | awk '/gitVersion:/{print $2; exit}')"
printf '%-12s %s\n' "python3"   "$(python3 --version)"

if docker info >/dev/null 2>&1; then
  printf '%-12s %s\n' "docker" "$(docker version --format '{{.Server.Version}}')"
else
  echo
  echo "[WARNING] Ubuntu에서 Docker 엔진에 연결되지 않습니다."
  echo "Docker Desktop을 실행한 후 다음 설정을 확인하세요."
  echo "Settings > Resources > WSL Integration > 현재 Ubuntu 활성화"
fi
'@

$tempScript = Join-Path $env:TEMP 'setup-eks-tools-wsl.sh'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($tempScript, $linuxInstallScript, $utf8NoBom)

try {
    $wslScriptPath = (
        & wsl.exe --distribution $selectedDistro -- wslpath -a $tempScript
    ).Trim()

    if (-not $wslScriptPath) {
        throw "임시 설치 스크립트의 WSL 경로를 변환하지 못했습니다."
    }

    & wsl.exe `
        --distribution $selectedDistro `
        -- bash $wslScriptPath $KubectlMinorVersion

    if ($LASTEXITCODE -ne 0) {
        throw "Ubuntu 내부 도구 설치가 실패했습니다."
    }
}
finally {
    Remove-Item $tempScript -Force -ErrorAction SilentlyContinue
}

Write-Step "5. 최종 확인"

$verifyCommand = @'
set -u

commands=(terraform aws kubectl docker python3)
failed=0

for command_name in "${commands[@]}"; do
  if command -v "${command_name}" >/dev/null 2>&1; then
    printf '[OK]    %s\n' "${command_name}"
  else
    printf '[ERROR] %s\n' "${command_name}"
    failed=1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo
  echo '[ACTION] Docker Desktop을 실행하고 WSL Integration에서 Ubuntu를 활성화하세요.'
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo
  echo '[ACTION] Ubuntu에서 AWS 인증을 설정하세요:'
  echo '         aws configure'
fi

exit "${failed}"
'@

& wsl.exe --distribution $selectedDistro -- bash -lc $verifyCommand
$verificationExitCode = $LASTEXITCODE

Write-Host ""
Write-Host "설치 후 Ubuntu 실행:" -ForegroundColor Green
Write-Host "  wsl -d `"$selectedDistro`""
Write-Host ""
Write-Host "AWS 인증 설정:" -ForegroundColor Green
Write-Host "  aws configure"
Write-Host "  aws sts get-caller-identity"
Write-Host ""
Write-Host "프로젝트 실행 예시:" -ForegroundColor Green
Write-Host "  cd ~/workspace/tf-k8s-ci"
Write-Host "  sed -i 's/\r$//' project.sh"
Write-Host "  chmod +x project.sh && find scripts -name '*.sh' -exec chmod +x {} +"
Write-Host "  ./project.sh deploy"
Write-Host ""

if ($verificationExitCode -ne 0) {
    Write-Warning "일부 명령 확인에 실패했습니다. 위의 [ACTION] 안내를 처리한 후 다시 확인하세요."
    exit $verificationExitCode
}

Write-Host "Windows EKS 실습 환경 설치가 완료되었습니다." -ForegroundColor Green
