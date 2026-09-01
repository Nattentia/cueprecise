$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $repo "dist\windows"
$work = Join-Path $repo "build\pyinstaller"
$spec = Join-Path $repo "build\spec"

New-Item -ItemType Directory -Force -Path $dist, $work, $spec | Out-Null
foreach ($stale in @("ytx.exe", "ytx-mcp.exe", "ytx-onboarding.exe", "ytx-setup.exe",
                     "cueprecise.exe", "cueprecise-setup.exe")) {
    Remove-Item -LiteralPath (Join-Path $dist $stale) -Force -ErrorAction SilentlyContinue
}

function Build-Executable {
    param(
        [string]$Name,
        [string]$Script,
        [switch]$Windowed,
        [string[]]$CollectAll = @(),
        [string[]]$HiddenImports = @()
    )
    $mode = if ($Windowed) { "--windowed" } else { "--console" }
    $arguments = @(
        "run", "--with", "pyinstaller", "pyinstaller",
        "--noconfirm", "--clean", "--onefile", $mode,
        "--name", $Name,
        "--distpath", $dist,
        "--workpath", (Join-Path $work $Name),
        "--specpath", $spec,
        (Join-Path $repo $Script)
    )
    foreach ($package in $CollectAll) {
        $arguments += @("--collect-all", $package)
    }
    foreach ($package in $HiddenImports) {
        $arguments += @("--hidden-import", $package)
    }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $Name" }
}

Build-Executable -Name "cueprecise-mcp" -Script "src\mcp_server.py" -HiddenImports @("google.genai")
Build-Executable -Name "yt-dlp" -Script "installer\yt_dlp_launcher.py" -CollectAll @("yt_dlp")
Build-Executable -Name "cueprecise-onboarding" -Script "installer\cueprecise_onboarding.py" -Windowed

$iscc = (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidate = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $candidate) { $iscc = $candidate }
}
if (-not $iscc) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $candidate) { $iscc = $candidate }
}
if (-not $iscc) {
    throw "Inno Setup 6 is required. Install it with: winget install --id JRSoftware.InnoSetup -e"
}

& $iscc (Join-Path $repo "installer\cueprecise.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$installer = Join-Path $dist "cueprecise-setup.exe"
if (-not (Test-Path -LiteralPath $installer)) { throw "Installer was not created: $installer" }
Get-FileHash -Algorithm SHA256 -LiteralPath $installer | Format-List
