$ErrorActionPreference = "Stop"

# Windows Smart App Control (SAC, enforcement mode) blocks rare unsigned EXEs:
# PyInstaller onefile binaries, custom C# shims, and non-mainstream FFmpeg builds
# have all been observed blocked on a real SAC machine. This script instead ships
# only files that are common and were observed ALLOWED under SAC: the official
# python.org embeddable CPython interpreter running our .py source as a module,
# and the Gyan "essentials" FFmpeg build's original, unmodified ffmpeg.exe /
# ffprobe.exe. The MCPB stays self-contained (the user installs nothing else) —
# it just no longer contains any PyInstaller-built or custom-compiled executable.

$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dist = Join-Path $repo "dist\mcpb"
$stage = Join-Path $repo "build\mcpb\release"
# Short folder names keep deep dependency paths (google-genai) well under MAX_PATH
# inside Claude Desktop's long extension directory.
$pythonDir = Join-Path $stage "py"
$appDir = Join-Path $stage "app"
$sitePackages = Join-Path $pythonDir "lib"
$licenses = Join-Path $stage "licenses"
$cache = Join-Path $repo "build\mcpb\cache"

New-Item -ItemType Directory -Force -Path $cache | Out-Null

# --- Pinned python.org embeddable package (Windows amd64) -----------------
# Downloaded once, hash computed, then hardcoded here. Verified on every
# build the same way the FFmpeg pin below is.
$pythonVersion = "3.13.15"
$pythonAsset = "python-$pythonVersion-embed-amd64.zip"
$pythonSha256 = "D1F04D990AEE1253D8569E8E5104E30FA9F5FA830899F14843448872D936A2CF"
$pythonArchive = Join-Path $cache $pythonAsset
if (-not (Test-Path -LiteralPath $pythonArchive -PathType Leaf)) {
    $url = "https://www.python.org/ftp/python/$pythonVersion/$pythonAsset"
    Invoke-WebRequest -Uri $url -OutFile $pythonArchive
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $pythonArchive).Hash -ne $pythonSha256) {
    throw "The pinned python.org embeddable archive failed SHA-256 verification"
}

# --- Pinned Gyan FFmpeg "essentials" build ---------------------------------
# Gyan's essentials build is a static, GPLv3 build. We take only the original,
# unmodified ffmpeg.exe and ffprobe.exe from it (no ffplay, no shared DLLs).
$ffmpegVersion = "9.0.1"
$ffmpegAsset = "ffmpeg-$ffmpegVersion-essentials_build.zip"
$ffmpegSha256 = "FEC81AE03971D9DD4BE3EBE02E263BD2EC1D789483F931BDBA5F5715E65DA2E9"
$ffmpegArchive = Join-Path $cache $ffmpegAsset
if (-not (Test-Path -LiteralPath $ffmpegArchive -PathType Leaf)) {
    $url = "https://github.com/GyanD/codexffmpeg/releases/download/$ffmpegVersion/$ffmpegAsset"
    Invoke-WebRequest -Uri $url -OutFile $ffmpegArchive
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ffmpegArchive).Hash -ne $ffmpegSha256) {
    throw "The pinned FFmpeg archive failed SHA-256 verification"
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $pythonDir, $appDir, $sitePackages, $licenses, $dist | Out-Null

# --- Unpack the embeddable python ------------------------------------------
Expand-Archive -LiteralPath $pythonArchive -DestinationPath $pythonDir -Force
$pythonLicense = Join-Path $pythonDir "LICENSE.txt"
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw "The pinned python.org embeddable archive is missing LICENSE.txt"
}
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenses "Python-LICENSE.txt")

# The embeddable distribution ships an isolated `._pth` that, by default, does
# not import `site` and therefore ignores `site-packages` entirely. Point it at
# our vendored dependencies (Lib\site-packages) and at the copied CuePrecise
# source (..\app) and turn `site` back on.
$pth = Join-Path $pythonDir "python313._pth"
if (-not (Test-Path -LiteralPath $pth -PathType Leaf)) {
    throw "python313._pth was not found in the embeddable package"
}
@(
    "python313.zip"
    "."
    "lib"
    "..\app"
    "import site"
) | Set-Content -LiteralPath $pth -Encoding ascii

# --- Install runtime dependencies from the lock -----------------------------
$requirements = Join-Path $cache "requirements.txt"
& uv export --no-dev --no-hashes --no-emit-project --format requirements-txt |
    Out-File -FilePath $requirements -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "uv export failed" }
& uv pip install --target $sitePackages `
    --python-platform x86_64-pc-windows-msvc --python-version 3.13 `
    --only-binary :all: -r $requirements
if ($LASTEXITCODE -ne 0) { throw "uv pip install --target failed" }
# uv puts console-script launcher exes here. Nothing runs them, and unsigned
# per-package exes are exactly what Smart App Control blocks.
Remove-Item -LiteralPath (Join-Path $sitePackages "bin") -Recurse -Force -ErrorAction SilentlyContinue
foreach ($required in @("yt_dlp", "google")) {
    if (-not (Test-Path -LiteralPath (Join-Path $sitePackages $required))) {
        throw "site-packages is missing $required after uv pip install"
    }
}
if (-not (Get-ChildItem -LiteralPath $sitePackages -Filter "google_genai-*.dist-info" -Directory)) {
    throw "site-packages is missing the google-genai distribution info"
}

# --- Copy CuePrecise's own runtime modules (not tests) ----------------------
Get-ChildItem -LiteralPath (Join-Path $repo "src") -Filter "*.py" -File |
    Copy-Item -Destination $appDir

# --- Unpack FFmpeg and take only the two programs we call ------------------
$ffmpegExtract = Join-Path $cache "ffmpeg-unpacked"
if (Test-Path -LiteralPath $ffmpegExtract) {
    Remove-Item -LiteralPath $ffmpegExtract -Recurse -Force
}
Expand-Archive -LiteralPath $ffmpegArchive -DestinationPath $ffmpegExtract
$ffmpegRoot = (Get-ChildItem -LiteralPath $ffmpegExtract -Directory | Select-Object -First 1).FullName
$ffmpegBin = Join-Path $ffmpegRoot "bin"
$ffmpegLicense = Join-Path $ffmpegRoot "LICENSE"
foreach ($required in @("ffmpeg.exe", "ffprobe.exe")) {
    $source = Join-Path $ffmpegBin $required
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "The pinned FFmpeg archive is missing $required"
    }
    # Copied unmodified, next to python.exe, so src/runtime.py's sibling-exe
    # lookup finds them regardless of sys.frozen.
    Copy-Item -LiteralPath $source -Destination $pythonDir
}
Copy-Item -LiteralPath $ffmpegLicense -Destination (Join-Path $licenses "FFmpeg-LICENSE.txt")

# --- Manifest and notices ---------------------------------------------------
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "manifest.json") -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $licenses

# --- Pack -------------------------------------------------------------------
$archive = Join-Path $dist "cueprecise-windows.zip"
$bundle = Join-Path $dist "cueprecise-windows.mcpb"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Move-Item -LiteralPath $archive -Destination $bundle

Get-FileHash -Algorithm SHA256 -LiteralPath $bundle | Format-List
Write-Output ("MCPB size: {0:N1} MiB" -f ((Get-Item -LiteralPath $bundle).Length / 1MB))
