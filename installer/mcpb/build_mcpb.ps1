$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dist = Join-Path $repo "dist\mcpb"
$stage = Join-Path $repo "build\mcpb\release"
$server = Join-Path $stage "server"
$licenses = Join-Path $stage "licenses"
$windows = Join-Path $repo "dist\windows"

& (Join-Path $PSScriptRoot "build_mcpb_poc.ps1")
if ($LASTEXITCODE -ne 0) { throw "CuePrecise MCP executable build failed" }

if (-not (Test-Path -LiteralPath (Join-Path $windows "yt-dlp.exe"))) {
    & uv run --with pyinstaller pyinstaller --noconfirm --clean --onefile --console `
        --name "yt-dlp" --distpath $windows `
        --workpath (Join-Path $repo "build\pyinstaller\yt-dlp") `
        --specpath (Join-Path $repo "build\spec") --collect-all yt_dlp `
        (Join-Path $repo "installer\yt_dlp_launcher.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: yt-dlp" }
}

$ffmpegTag = "autobuild-2026-09-03-13-17"
$ffmpegAsset = "ffmpeg-N-126390-g9fc8c785e2-win64-lgpl-shared.zip"
$ffmpegSha256 = "3C3DD10B1F4E3663F38A1FB574D7734F7606DBB758EAEC2E4F7D398B9ACDF78A"
$ffmpegCache = Join-Path $repo "build\mcpb\ffmpeg"
$ffmpegArchive = Join-Path $ffmpegCache $ffmpegAsset
$ffmpegExtract = Join-Path $ffmpegCache "unpacked"
New-Item -ItemType Directory -Force -Path $ffmpegCache | Out-Null
if (-not (Test-Path -LiteralPath $ffmpegArchive -PathType Leaf)) {
    $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$ffmpegTag/$ffmpegAsset"
    Invoke-WebRequest -Uri $url -OutFile $ffmpegArchive
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ffmpegArchive).Hash -ne $ffmpegSha256) {
    throw "The pinned FFmpeg archive failed SHA-256 verification"
}
if (Test-Path -LiteralPath $ffmpegExtract) {
    Remove-Item -LiteralPath $ffmpegExtract -Recurse -Force
}
Expand-Archive -LiteralPath $ffmpegArchive -DestinationPath $ffmpegExtract
$ffmpegRoot = (Get-ChildItem -LiteralPath $ffmpegExtract -Directory | Select-Object -First 1).FullName
$ffmpegBin = Join-Path $ffmpegRoot "bin"
$ffmpegLicense = Join-Path $ffmpegRoot "LICENSE.txt"
foreach ($required in @("ffmpeg.exe", "ffprobe.exe", "avcodec-63.dll", "avformat-63.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpegBin $required) -PathType Leaf)) {
        throw "The pinned FFmpeg archive is missing $required"
    }
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $server, $licenses, $dist | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "manifest.json") -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath (Join-Path $windows "cueprecise-mcp.exe") -Destination $server
Copy-Item -LiteralPath (Join-Path $windows "yt-dlp.exe") -Destination $server
Get-ChildItem -LiteralPath $ffmpegBin -File | Where-Object { $_.Name -ne "ffplay.exe" } | Copy-Item -Destination $server
Copy-Item -LiteralPath $ffmpegLicense -Destination (Join-Path $licenses "FFmpeg-LICENSE.txt")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $licenses

$archive = Join-Path $dist "cueprecise-windows.zip"
$bundle = Join-Path $dist "cueprecise-windows.mcpb"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Move-Item -LiteralPath $archive -Destination $bundle

Get-FileHash -Algorithm SHA256 -LiteralPath $bundle | Format-List
