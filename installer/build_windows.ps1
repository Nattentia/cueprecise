$ErrorActionPreference = "Stop"

# The Inno Setup/PyInstaller path (cueprecise-setup.exe) that used to live here
# is retired from the default flow: on a real Smart App Control (SAC,
# enforcement mode) machine the unsigned installer exe itself is blocked, so no
# payload change inside it can save it. This script instead packages the same
# MCPB staging tree (installer/mcpb/build_mcpb.ps1: python.org embeddable
# CPython + Gyan FFmpeg essentials, no PyInstaller-built or custom-compiled
# executable) as a plain zip with a `.cmd` launcher, for AI clients other than
# Claude Desktop that cannot install a `.mcpb`.
#
# installer/cueprecise.iss is left in place (unused by this script) because
# tests/test_naming.py and installer/release_version.py still read it as one
# of the five places a release version must agree.

$repo = Split-Path -Parent $PSScriptRoot
$mcpbStage = Join-Path $repo "build\mcpb\release"
$stage = Join-Path $repo "build\windows\release"
$dist = Join-Path $repo "dist\windows"
$zipPath = Join-Path $dist "cueprecise-windows.zip"

if (-not (Test-Path -LiteralPath (Join-Path $mcpbStage "py\python.exe"))) {
    & (Join-Path $repo "installer\mcpb\build_mcpb.ps1")
    if ($LASTEXITCODE -ne 0) { throw "installer\mcpb\build_mcpb.ps1 failed" }
}

foreach ($required in @("py\python.exe", "py\ffmpeg.exe", "py\ffprobe.exe",
                        "app\mcp_server.py", "app\cueprecise_setup.py", "licenses")) {
    if (-not (Test-Path -LiteralPath (Join-Path $mcpbStage $required))) {
        throw "MCPB staging is missing $required; rerun installer\mcpb\build_mcpb.ps1"
    }
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stage, $dist | Out-Null

Copy-Item -LiteralPath (Join-Path $mcpbStage "py") -Destination (Join-Path $stage "py") -Recurse
Copy-Item -LiteralPath (Join-Path $mcpbStage "app") -Destination (Join-Path $stage "app") -Recurse
Copy-Item -LiteralPath (Join-Path $mcpbStage "licenses") -Destination (Join-Path $stage "licenses") -Recurse
Copy-Item -LiteralPath (Join-Path $repo "LICENSE") -Destination (Join-Path $stage "LICENSE")

# --- The launcher ------------------------------------------------------------
# Written without a BOM: cmd.exe misreads a leading UTF-8 BOM as part of the
# first command, breaking `@chcp 65001` (and everything after it). `chcp 65001`
# itself is what makes the following Korean text and paths render correctly no
# matter the user's default console codepage.
$cmdPath = Join-Path $stage "CuePrecise 설치.cmd"
$cmdBody = "@chcp 65001 >nul`r`n`"%~dp0py\python.exe`" -m cueprecise_setup %*`r`n"
[System.IO.File]::WriteAllText($cmdPath, $cmdBody, (New-Object System.Text.UTF8Encoding($false)))

Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal

Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath | Format-List
Write-Output ("Zip size: {0:N1} MiB" -f ((Get-Item -LiteralPath $zipPath).Length / 1MB))
