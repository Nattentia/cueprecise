param(
    [switch]$SkipExecutableBuild
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dist = Join-Path $repo "dist\mcpb"
$stage = Join-Path $repo "build\mcpb\cueprecise"
$server = Join-Path $stage "server"
$exe = Join-Path $repo "dist\windows\cueprecise-mcp.exe"
$manifest = Join-Path $PSScriptRoot "manifest.json"

foreach ($path in @($dist, $stage)) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($repo + [IO.Path]::DirectorySeparatorChar,
                              [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to use a path outside the repository: $full"
    }
}

if (-not $SkipExecutableBuild) {
    New-Item -ItemType Directory -Force -Path (Split-Path $exe),
        (Join-Path $repo "build\pyinstaller\cueprecise-mcp"),
        (Join-Path $repo "build\spec") | Out-Null
    & uv run --with pyinstaller pyinstaller `
        --noconfirm --clean --onefile --console `
        --name cueprecise-mcp `
        --distpath (Split-Path $exe) `
        --workpath (Join-Path $repo "build\pyinstaller\cueprecise-mcp") `
        --specpath (Join-Path $repo "build\spec") `
        --hidden-import google.genai `
        (Join-Path $repo "src\mcp_server.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: cueprecise-mcp" }
}

if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Build cueprecise-mcp.exe first or omit -SkipExecutableBuild: $exe"
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $server, $dist | Out-Null
Copy-Item -LiteralPath $manifest -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath $exe -Destination (Join-Path $server "cueprecise-mcp.exe")

$archive = Join-Path $dist "cueprecise-windows-poc.zip"
$bundle = Join-Path $dist "cueprecise-windows-poc.mcpb"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Move-Item -LiteralPath $archive -Destination $bundle

$bundleHash = Get-FileHash -Algorithm SHA256 -LiteralPath $bundle
$exeHash = Get-FileHash -Algorithm SHA256 -LiteralPath $exe
[pscustomobject]@{
    Bundle = $bundle
    BundleSha256 = $bundleHash.Hash
    Executable = $exe
    ExecutableSha256 = $exeHash.Hash
    IncludesFfmpeg = $false
    IncludesYtDlp = $false
} | Format-List
