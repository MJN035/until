# Until Cloud 원커맨드 배포 (Windows PowerShell)
#   1) npx wrangler login   (브라우저 클릭 1회 — 이 스크립트가 안 되어 있으면 안내)
#   2) .\deploy\deploy.ps1  (리포 루트에서 실행)
# 하는 일: KV 네임스페이스 생성(없으면) → 시크릿 입력 프롬프트 → 앱 배포 → 랜딩 APP_URL 연결 → 랜딩 배포
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 0. 로그인 확인
$who = npx wrangler whoami 2>&1 | Out-String
if ($who -match "not authenticated") {
    Write-Host "먼저 로그인이 필요해요:  npx wrangler login" -ForegroundColor Yellow
    exit 1
}
Write-Host "wrangler 인증 확인됨." -ForegroundColor Green

# 1. Docker 확인 (컨테이너 이미지 빌드에 필요)
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop을 먼저 실행해 주세요." -ForegroundColor Yellow; exit 1 }

# 2. KV 네임스페이스 (이미 있으면 목록에서 재사용)
$kvList = npx wrangler kv namespace list 2>$null | Out-String
$nsId = $null
if ($kvList -match '"title":\s*"UNTIL_KV"[\s\S]*?"id":\s*"([0-9a-f]+)"' -or
    $kvList -match '"id":\s*"([0-9a-f]+)"[\s\S]*?"title":\s*"UNTIL_KV"') {
    $nsId = $Matches[1]
    Write-Host "기존 KV 네임스페이스 재사용: $nsId" -ForegroundColor Green
} else {
    $out = npx wrangler kv namespace create UNTIL_KV | Out-String
    if ($out -match '"?id"?[:=]\s*"?([0-9a-f]{16,})"?') { $nsId = $Matches[1] }
    Write-Host "KV 네임스페이스 생성: $nsId" -ForegroundColor Green
}
if (-not $nsId) { Write-Host "KV id를 얻지 못했어요 — 출력 확인 후 DEPLOY.md 수동 절차로."; exit 1 }

# 3. 앱 배포 (시크릿은 wrangler가 프롬프트)
Set-Location (Join-Path $root "deploy\app")
if (-not (Test-Path "node_modules")) { npm install }
Write-Host "`n시크릿 입력(값은 화면에 안 보임):" -ForegroundColor Cyan
Write-Host " - UNTIL_API_KEY: Groq API 키 (console.groq.com)"
npx wrangler secret put UNTIL_API_KEY
Write-Host " - UNTIL_KV_ACCOUNT: Cloudflare 계정 ID (대시보드 우측)"
npx wrangler secret put UNTIL_KV_ACCOUNT
Write-Host " - UNTIL_KV_NAMESPACE: 방금 만든 네임스페이스 id = $nsId"
npx wrangler secret put UNTIL_KV_NAMESPACE
Write-Host " - UNTIL_KV_TOKEN: 'Workers KV Storage:Edit' 권한 API 토큰"
npx wrangler secret put UNTIL_KV_TOKEN
Write-Host " - UNTIL_BETA_CODE: 초대 코드(비우려면 그냥 Enter 후 Ctrl+C — 선택)"
try { npx wrangler secret put UNTIL_BETA_CODE } catch { Write-Host "  (베타 코드 생략)" }

$deployOut = npx wrangler deploy | Out-String
Write-Host $deployOut
$appUrl = $null
if ($deployOut -match "(https://[^\s]+\.workers\.dev)") { $appUrl = $Matches[1] }
if (-not $appUrl) { Write-Host "앱 URL을 출력에서 못 찾았어요 — 대시보드에서 확인 후 랜딩 index.html의 APP_URL을 직접 채워 주세요." }
else { Write-Host "앱 배포됨: $appUrl" -ForegroundColor Green }

# 4. 랜딩 APP_URL 연결 + 배포
Set-Location (Join-Path $root "deploy\landing")
if ($appUrl) {
    $idx = Join-Path (Get-Location) "public\index.html"
    (Get-Content $idx -Raw -Encoding utf8) -replace 'var APP_URL = "[^"]*";', "var APP_URL = `"$appUrl`";" |
        Set-Content $idx -Encoding utf8 -NoNewline
    Write-Host "랜딩 CTA를 $appUrl 로 연결." -ForegroundColor Green
}
npx wrangler deploy
Set-Location $root
Write-Host "`n완료! 확인: $appUrl/healthz → ok, 랜딩 URL은 위 출력 참고." -ForegroundColor Green
Write-Host "체크리스트: deploy/DEPLOY.md '4. 확인 체크리스트'"
