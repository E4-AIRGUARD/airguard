# cb.ps1 — CleanBox 빌드/업로드 헬퍼 (Windows, arduino-cli 포터블)
#
# 사용:
#   .\scripts\cb.ps1 list                 # 연결된 보드/포트 보기
#   .\scripts\cb.ps1 compile T01          # T01 스케치만 컴파일 (보드 없이 가능)
#   .\scripts\cb.ps1 compile-all          # T01~T08 전부 컴파일 검증
#   .\scripts\cb.ps1 flash T01 COM7       # T01 컴파일 후 COM7로 업로드
#   .\scripts\cb.ps1 monitor COM7         # 시리얼 모니터 115200
#
# 보드: ESP32 Dev Module (esp32:esp32:esp32) · 코어 3.x · 이 저장소 안(tools/)에만 설치됨(시스템 오염 없음)

$ErrorActionPreference = "Stop"
$Root   = Split-Path -Parent $PSScriptRoot
$Cli    = Join-Path $Root "tools\arduino-cli.exe"
$Cfg    = Join-Path $Root "tools\arduino-cli.yaml"
$Fqbn   = "esp32:esp32:esp32"           # ESP32 Dev Module
$FwDir  = Join-Path $Root "firmware"

function Sketch($t) {
  $d = Get-ChildItem -Path $FwDir -Directory | Where-Object { $_.Name -like "$t*" } | Select-Object -First 1
  if (-not $d) { throw "스케치 폴더를 못 찾음: $t  (예: T01, T03, T08)" }
  return $d.FullName
}

$cmd = $args[0]
switch ($cmd) {
  "list"    { & $Cli board list --config-file $Cfg }
  "compile" { & $Cli compile --fqbn $Fqbn --config-file $Cfg (Sketch $args[1]) }
  "compile-all" {
    $fail = 0
    Get-ChildItem -Path $FwDir -Directory | Sort-Object Name | ForEach-Object {
      Write-Host "`n=== compile $($_.Name) ===" -ForegroundColor Cyan
      & $Cli compile --fqbn $Fqbn --config-file $Cfg $_.FullName
      if ($LASTEXITCODE -ne 0) { $fail++; Write-Host "  FAIL $($_.Name)" -ForegroundColor Red }
    }
    if ($fail -eq 0) { Write-Host "`n전부 통과" -ForegroundColor Green } else { Write-Host "`n$fail 개 실패" -ForegroundColor Red; exit 1 }
  }
  "flash"   {
    if (-not $args[2]) { throw "포트 필요:  .\scripts\cb.ps1 flash T01 COM7" }
    & $Cli compile --fqbn $Fqbn --config-file $Cfg (Sketch $args[1])
    & $Cli upload -p $args[2] --fqbn $Fqbn --config-file $Cfg (Sketch $args[1])
  }
  "monitor" {
    if (-not $args[1]) { throw "포트 필요:  .\scripts\cb.ps1 monitor COM7" }
    & $Cli monitor -p $args[1] --config-file $Cfg -c baudrate=115200
  }
  default {
    Write-Host "명령: list | compile <Txx> | compile-all | flash <Txx> <COM> | monitor <COM>"
  }
}
