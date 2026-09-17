# CuePrecise MCPB (Claude Desktop extension)

## Why this layout

Windows Smart App Control (SAC, enforcement mode) blocks rare unsigned EXEs. On a real
SAC machine we measured PyInstaller onefile binaries (our old `cueprecise-mcp.exe`), a
small custom-compiled C# shim (the old `yt-dlp.exe`), and BtbN's FFmpeg autobuilds all
getting blocked at least some of the time. Common, mainstream files were not blocked:
the official python.org **embeddable** CPython interpreter running our `.py` source, and
Gyan's FFmpeg "essentials" build's original `ffmpeg.exe` / `ffprobe.exe`.

So this MCPB ships:

- `py/` — the official python.org embeddable package (pinned version + SHA-256)
  with our runtime dependencies installed into `lib` (from this repo's
  `uv.lock`) and `ffmpeg.exe` / `ffprobe.exe` (pinned Gyan essentials build, SHA-256
  verified) copied in unmodified, next to `python.exe`.
- `app/` — CuePrecise's own `.py` source (`src/*.py`, runtime modules only).
- `licenses/` — the Python PSF license, FFmpeg's GPLv3 license text, and
  `THIRD_PARTY_NOTICES.md`.

Claude Desktop launches `py/python.exe -m mcp_server --bundle-root <dir>`
(see `manifest.json`). Nothing is installed onto the user's machine outside the unpacked
extension folder — the interpreter, the dependencies, and the media tools all travel
inside the `.mcpb`. `src/runtime.py` finds `ffmpeg.exe` / `ffprobe.exe` next to
`sys.executable` in every mode (not just when frozen), and calls yt-dlp as
`python.exe -m yt_dlp` when the `yt_dlp` package is importable — which it always is here,
since `uv pip install --target` put it in `lib`.

This replaces the previous approach of one PyInstaller onefile binary (which bundled
yt-dlp itself), a tiny C# executable that shimmed subprocess calls into it, and a BtbN
shared FFmpeg build. Those pieces are gone (`mcpb_entrypoint.py`, `yt_dlp_shim.cs`); the
legacy PyInstaller `setup.exe` installer (`installer/build_windows.ps1`) is unaffected —
it still produces its own frozen `cueprecise-mcp.exe`, which now recognizes a `--yt-dlp`
flag (see `src/mcp_server.py`) so it no longer needs a second bundled executable either.

## Build

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File installer/mcpb/build_mcpb.ps1
```

This downloads (and caches) the pinned python.org embeddable package and the pinned
Gyan FFmpeg essentials build, verifies both against hardcoded SHA-256 hashes, installs
this repository's locked Python dependencies with `uv pip install --target`, copies
CuePrecise's own source, and zips the result into `dist/mcpb/cueprecise-windows.mcpb`.

For the older binary-type proof of concept (PyInstaller `cueprecise-mcp.exe` alone, no
media tools, used only to answer install/SmartScreen/`sensitive`-key questions), run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File installer/mcpb/build_mcpb_poc.ps1
```

## Manual acceptance test

Use a canary value such as `CUEPRECISE_POC_KEY_DO_NOT_USE` instead of a real API key.

1. Download or otherwise mark the `.mcpb` as originating from the Internet.
2. In Claude Desktop, open Settings > Extensions > Advanced settings > Install Extension.
3. Select the bundle, enter the canary key, and keep the default data directory.
4. Start a new chat and confirm that CuePrecise tools are listed.
5. Register a short public video and confirm transcription/frames succeed without the
   user installing Python, FFmpeg, or yt-dlp separately.
6. Search Claude settings, logs, and the extension directory for the canary value; record
   whether it is plaintext, encrypted, or absent.
7. Remove the extension and repeat the canary search.

On a Smart App Control (enforcement mode) machine, also check
`Microsoft-Windows-CodeIntegrity/Operational` (event 3077) for any blocked file after
running the above — the whole point of this layout is that there should be none.
