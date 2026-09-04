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

$ffmpeg = (Get-Command "ffmpeg.exe" -ErrorAction Stop).Source
$ffprobe = (Get-Command "ffprobe.exe" -ErrorAction Stop).Source
$ffmpegVersion = (& $ffmpeg -version 2>&1 | Select-Object -First 1) -join ""
if ($ffmpegVersion -notmatch "gyan\.dev") {
    throw "The Claude bundle currently accepts only the documented Gyan FFmpeg build: $ffmpegVersion"
}
$ffmpegRoot = Split-Path -Parent (Split-Path -Parent $ffmpeg)
$ffmpegLicense = Join-Path $ffmpegRoot "LICENSE"
if (-not (Test-Path -LiteralPath $ffmpegLicense -PathType Leaf)) {
    throw "The FFmpeg distribution license was not found beside the selected build: $ffmpegLicense"
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $server, $licenses, $dist | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "manifest.json") -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath (Join-Path $windows "cueprecise-mcp.exe") -Destination $server
Copy-Item -LiteralPath (Join-Path $windows "yt-dlp.exe") -Destination $server
Copy-Item -LiteralPath $ffmpeg -Destination $server
Copy-Item -LiteralPath $ffprobe -Destination $server
Copy-Item -LiteralPath $ffmpegLicense -Destination (Join-Path $licenses "FFmpeg-COPYING.GPLv3.txt")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $licenses

$archive = Join-Path $dist "cueprecise-windows.zip"
$bundle = Join-Path $dist "cueprecise-windows.mcpb"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Move-Item -LiteralPath $archive -Destination $bundle

Get-FileHash -Algorithm SHA256 -LiteralPath $bundle | Format-List
