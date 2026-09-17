# Third-party components

This CuePrecise MCPB includes separate programs and a separate language runtime used to
retrieve and process media. They remain under their own licenses. CuePrecise's own code
(`app/*.py`) runs as ordinary Python source on top of this runtime; it does not
statically link against any of the components below.

## Python

- Project: https://www.python.org/
- Distribution: the official python.org Windows embeddable package, amd64, version 3.13.15
- Source: https://www.python.org/ftp/python/3.13.15/
- License: Python Software Foundation License (PSF License Agreement)

The distribution's license text is included as `Python-LICENSE.txt`. CuePrecise runs
`python.exe` unmodified and loads its own `.py` sources from `app` and its
dependencies from `py/lib`; nothing in the interpreter itself
is changed.

## yt-dlp

- Project: https://github.com/yt-dlp/yt-dlp
- License: The Unlicense
- Installed as an ordinary Python package (`py/lib`), invoked
  with `python.exe -m yt_dlp`. No separate executable is bundled.

## google-genai and other Python dependencies

- Project: https://github.com/googleapis/python-genai
- License: Apache License 2.0
- Installed as ordinary Python packages under `py/lib`, resolved
  from this repository's `uv.lock`. Each dependency remains under its own upstream
  license, viewable in its package's `*.dist-info` metadata inside the installed bundle.

## FFmpeg

- Project: https://ffmpeg.org/
- Windows build provider: Gyan Doshi (https://www.gyan.dev/ffmpeg/), essentials build
- Included build: https://github.com/GyanD/codexffmpeg/releases/tag/9.0.1
  (`ffmpeg-9.0.1-essentials_build.zip`)
- License for the included build: GNU General Public License version 3
- Corresponding FFmpeg 9.0.1 source: https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz
  (source commit https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a)

The distribution's license text is included as `FFmpeg-LICENSE.txt`. CuePrecise ships the
static `ffmpeg.exe` and `ffprobe.exe` programs from this build unchanged (no `ffplay.exe`,
no shared libraries) and runs them as separate child processes via `subprocess` — it does
not link against FFmpeg's libraries. Because the bundled build is static and GPLv3-licensed,
this MCPB as a whole is distributed under terms compatible with the GPLv3 for the FFmpeg
programs specifically; CuePrecise's own code remains MIT-licensed (see the repository's
`LICENSE`) and yt-dlp/Python remain under their own licenses noted above.
