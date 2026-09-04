# Third-party components

This CuePrecise MCPB includes separate executable programs used to retrieve and process
media. They remain under their own licenses.

## yt-dlp

- Project: https://github.com/yt-dlp/yt-dlp
- License: The Unlicense
- Source: https://github.com/yt-dlp/yt-dlp

## FFmpeg

- Project: https://ffmpeg.org/
- Windows build provider: https://github.com/BtbN/FFmpeg-Builds
- Included build: https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-03-13-17
- License for the included build: GNU Lesser General Public License version 2.1 or later
- Corresponding FFmpeg source revision: https://github.com/FFmpeg/FFmpeg/commit/9fc8c785e2
- Build scripts: https://github.com/BtbN/FFmpeg-Builds

The distribution's license text is included as `FFmpeg-LICENSE.txt`. CuePrecise ships the
shared libraries unchanged and invokes the separate `ffmpeg.exe` and `ffprobe.exe` programs.
