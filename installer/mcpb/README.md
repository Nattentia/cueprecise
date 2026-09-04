# CuePrecise MCPB binary proof of concept

This package answers three questions before CuePrecise adopts MCPB as a release format:

1. Can Claude Desktop install and launch an unsigned PyInstaller binary from an MCPB?
2. Which tools work before `ffmpeg` and `yt-dlp` are bundled?
3. Does Claude Desktop mask, store, pass, and remove a `sensitive` Gemini API key safely?

The PoC contains `cueprecise-mcp.exe` only. The Claude release bundle additionally contains
`yt-dlp`, `ffmpeg`, and `ffprobe`, so a Claude Desktop user does not need Python or a separate
media-tool installation.

## Build

```powershell
pwsh -File installer/mcpb/build_mcpb_poc.ps1
```

The result is `dist/mcpb/cueprecise-windows-poc.mcpb`.

For the complete Claude Desktop bundle, run:

```powershell
pwsh -File installer/mcpb/build_mcpb.ps1
```

This creates `dist/mcpb/cueprecise-windows.mcpb`. The build downloads a pinned BtbN LGPL
shared FFmpeg archive, verifies its SHA-256 hash, omits `ffplay`, and includes the applicable
license and source/build links. The MCP executable also contains yt-dlp; a small sibling shim
preserves the existing subprocess interface without bundling a second Python runtime.

## Manual acceptance test

Use a canary value such as `CUEPRECISE_POC_KEY_DO_NOT_USE` instead of a real API key.

1. Download or otherwise mark the `.mcpb` as originating from the Internet.
2. In Claude Desktop, open Settings > Extensions > Advanced settings > Install Extension.
3. Select the bundle, enter the canary key, and keep the default data directory.
4. Start a new chat and confirm that CuePrecise tools are listed.
5. Call `cueprecise_status` with a nonexistent video id. A structured response proves the
   binary launched without requiring `ffmpeg` or `yt-dlp`.
6. Try registering a video and record the first missing-tool error. Do not install a tool
   during this test.
7. Search Claude settings, logs, and the extension directory for the canary value; record
   whether it is plaintext, encrypted, or absent.
8. Remove the extension and repeat the canary search.

Do not publish this bundle until the test passes on a clean Windows account with Smart App
Control or SmartScreen enabled.

## Observed on Windows

Claude Desktop 1.40609.1.0 installed and enabled an Internet-zone-marked bundle without a
SmartScreen prompt. The extracted unsigned executable had no `Zone.Identifier` stream. The
canary was absent from a plaintext search of Claude's data directory; the extension settings
stored it with an `__encrypted__:` prefix. Removing the extension deleted both its extracted
directory and settings file, and the canary remained absent from a plaintext search. These
observations cover this machine and Claude version only. Server environment delivery still
needs separate verification.
