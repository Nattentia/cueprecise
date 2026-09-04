<p align="right"><strong>English</strong> | <a href="./README.ko.md">한국어</a></p>

# CuePrecise

> **A long video in a language you do not speak should still be searchable.**

CuePrecise transcribes the original speech with Gemini instead of relying on YouTube captions
alone. It keeps each passage tied to the timeline and captures the matching frames, so your AI can
explain the video in your language and show exactly where the answer came from. In our recorded
test, a 68-minute video was ready for questions in about three minutes; processing time varies by
video and available captions. It also leaves a local reference for later conversations, so you do
not have to start over.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-477%20passing-brightgreen.svg)](#tests)

### What it feels like

```text
You: I do not speak Polish. What is this interview about?

Your AI + CuePrecise:
Explains the interview in your language, points to the exact moments,
and provides the original transcript and matching frames as evidence.
```

https://github.com/user-attachments/assets/ce7d595b-871f-469a-bcb8-798713751ffd

<sub>Demo source: [“Czym jest prompt injection i jak chronić firmę przed złośliwą instrukcją dla AI? Gośc. Tomasz Bartel”](https://www.youtube.com/watch?v=W5C3FdUO0vs) by Daniel Bartosiewicz | Content i Automatyzacja, licensed under CC BY.</sub>

| | |
|---|---|
| 🌍 **Explore videos in other languages** | Ask in your language while every answer stays tied to the original video. |
| 🔎 **Find exact moments** | Get timestamped passages instead of unsupported guesses. |
| 👁️ **Inspect the screen** | Retrieve frames when the answer exists visually, not in the captions. |
| 🧩 **Recover missed terms** | Combine Gemini transcription with captions when names or technical terms disappear. |
| 💾 **Continue in a later chat** | Leave a local reference your AI can consult without starting over. |
| 🔐 **No CuePrecise account or server** | No advertising, tracking, or project-operated backend. |

### Start in Claude Desktop on Windows

[**Download CuePrecise →**](https://github.com/Nattentia/cueprecise/releases)

1. Download `cueprecise-windows.mcpb` from the newest release.
2. In Claude Desktop, open **Settings → Extensions → Advanced settings → Install Extension**.
3. Select the downloaded file, then paste your [Gemini API key](https://aistudio.google.com/api-keys)
   when Claude asks for it.
4. Enable CuePrecise and ask Claude about a YouTube link.

That one file contains CuePrecise and its video tools. It is about 86 MiB and requires no Python,
Git, FFmpeg installation, terminal commands, or manual configuration. The extension is currently
available for Claude Desktop on Windows. For Codex, Claude Code, Cursor, Windsurf, VS Code, or
Gemini CLI, use `cueprecise-setup.exe` from the same release.

Both downloads contain unsigned executables. Download them only from this repository's Releases
page and verify `SHA256SUMS.txt` if you want to check the file before installing it.

**[Full installation guide](#quick-start)** · **[MCP tools](#tools)** ·
**[How it works](#how-it-works)** · **[Known limitations](#known-limitations)**

---

## Why CuePrecise?

Captions alone can be incomplete, rough, or unavailable. A translated summary may be easier to
read, but it often loses the connection to the exact words and moments in the original video.
CuePrecise keeps three kinds of evidence together:

- Gemini transcription of the original speech.
- Timestamp-aligned YouTube captions that can recover missed names and technical terms.
- Frames from the relevant moments, including information that was shown but never spoken.

Your AI host can explain that evidence in the language you ask, while CuePrecise preserves the
original timeline and source of every recovered word. CuePrecise itself does not replace the
source transcript with a translation.

### Measured transcription example

One measured edge case shows why captions still matter. On a 23-minute Korean technical lecture
(`jcBDSLSeud4`), the phrase
`self supervised learning` disappeared from all four Gemini-only transcription runs. CuePrecise
recovered it from timestamp-aligned YouTube captions without rewriting or deleting Gemini's
original words.

| Source | Latin-script words | `self supervised learning` | Korean quality |
|---|---:|---|---|
| YouTube `ko-orig` auto-captions | 91 | Present | Rough |
| Gemini transcription (`ko-KR`) | 29 | **Missing** | Good |
| Gemini transcription (auto-detect) | 28 | **Missing** | Good |
| **CuePrecise merge** | **38** | **Present** | **Good** |

This is a measured example, not a general accuracy claim. See [`CONTRACT.md`](CONTRACT.md) for
the exact merge rules and validation criteria.

---

## Quick start

### Claude Desktop on Windows — one-file extension

1. Open [Releases](https://github.com/Nattentia/cueprecise/releases) and download
   `cueprecise-windows.mcpb`.
2. In Claude Desktop, open **Settings → Extensions → Advanced settings → Install Extension**.
3. Select the file. Claude will ask for a Gemini API key and a folder for local video context.
4. Enable CuePrecise. Restart Claude Desktop if it asks you to.

The API key field is marked as sensitive and is managed through Claude's extension settings. The
default context folder is `~/.cueprecise/data`. The approximately 86 MiB bundle includes the
CuePrecise server, `yt-dlp`, FFmpeg, and FFprobe, so there are no other runtime downloads or
prerequisites.

### Other supported AI apps on Windows

1. Open [Releases](https://github.com/Nattentia/cueprecise/releases) and download
   `cueprecise-setup.exe`.
2. Run the installer. In the onboarding window, click **Create API key**.
3. In Google AI Studio, create or select a Gemini API key and copy it.
4. Paste the key into CuePrecise.
5. Select the AI apps found on your computer and click **Connect**.
6. Fully quit and reopen the connected apps.

The installer prepares the bundled video tools, detects supported AI apps, backs up their existing
configuration, and adds only the CuePrecise MCP entry. The API key is stored in the selected apps'
local configuration; CuePrecise does not send it to a project-operated server.

> **Unsigned preview:** `v0.2.1` is not digitally signed, so Windows may display an
> unknown-publisher warning. Download it only from this repository's Releases page.

### macOS, Linux, and command-line installation

With [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
```

Install `ffmpeg` and `ffprobe`, then check the environment:

```bash
cueprecise doctor
```

Create a key at [Google AI Studio](https://aistudio.google.com/api-keys), then run:

```bash
export GEMINI_API_KEY="..."          # Windows PowerShell: $env:GEMINI_API_KEY="..."
cueprecise setup
cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language pl-PL
cueprecise status VIDEO_ID
```

The key can also come from a file or standard input, which keeps it out of your shell
history:

```bash
cueprecise setup --api-key-file ~/.gemini-key
pass show gemini/api-key | cueprecise setup --api-key -
```

If a key is exposed, delete it at [Google AI Studio](https://aistudio.google.com/api-keys)
and create a new one. See [the privacy policy](PRIVACY.md#api-키를-폐기하는-방법) for the
full procedure.

For source development only:

```bash
git clone https://github.com/Nattentia/cueprecise.git
cd cueprecise
python -m pip install -r requirements.txt
python src/pipeline.py --help
```

---

## Connect another MCP host

The MCP specification dropped the `initialize` handshake in revision `2026-07-28` and now carries
the protocol version on every request. **CuePrecise works with both the new revision and the
earlier handshake-based ones:** it answers `server/discover` with the versions it supports, and
still accepts an `initialize` handshake.

`cueprecise setup` finds the AI apps installed on this computer and connects all of them. You do
not need to know their names.

```bash
cueprecise setup                    # every app that is found
cueprecise setup --client codex     # one app only
cueprecise doctor                   # per-app install and connection state
```

| App | Configuration file | How it is connected |
|---|---|---|
| Claude Desktop | `claude_desktop_config.json` | file |
| Codex | `$CODEX_HOME/config.toml` (default `~/.codex`) | `codex mcp add` |
| Claude Code | `~/.claude.json` | `claude mcp add -s user` |
| VS Code | `Code/User/mcp.json` (top-level key is `servers`) | `code --add-mcp`; removal edits the file |
| Cursor | `~/.cursor/mcp.json` | file |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | file |
| Gemini CLI | `~/.gemini/settings.json` | `gemini mcp add -s user`, file if that fails |

An app counts as installed when its executable is on `PATH`. A leftover configuration folder is not
treated as proof that the app is there. An app that is not detected can still be named explicitly
with `--client <name>`.

If an entry called `cueprecise` already exists and CuePrecise did not create it, that app is
skipped. Someone else's configuration is never overwritten, and one failing app does not stop the
others.

**ChatGPT connectors and Claude.ai on the web are not supported.** Both accept only remote MCP
servers reached over HTTPS with OAuth. CuePrecise runs locally on your computer, so it cannot be
registered there.

The JSON below is needed only for a source checkout or an MCP host that is not listed above:

```json
{
  "mcpServers": {
    "cueprecise": {
      "command": "python",
      "args": [
        "C:/path/to/cueprecise/src/mcp_server.py",
        "--bundle-root",
        "C:/path/to/cueprecise/data"
      ],
      "env": { "GEMINI_API_KEY": "..." }
    }
  }
}
```

Use absolute paths. `--bundle-root` stores persistent video bundles and `index.sqlite3`. The server
can start without `GEMINI_API_KEY`; existing analyses remain searchable, while new transcription
requests stop with a configuration message.

### Tools

| Tool | Purpose | Gemini calls |
|---|---|---:|
| `cueprecise_register` | Register and analyze a video | One per audio chunk |
| `cueprecise_status` | Report progress, artifacts, and estimated usage | None |
| `cueprecise_outline` | Return a timestamped outline and speaker state | None |
| `cueprecise_query` | Search transcript and frame evidence | None |
| `cueprecise_excerpt` | Return transcript and frames for a time range | None |
| `cueprecise_frames` | Extract frames around visually relevant moments | None |
| `cueprecise_summary` | Create or retrieve a summary on request | None |
| `cueprecise_set_summary` | Validate and store a host-improved summary | None |
| `cueprecise_set_chapter_titles` | Validate and store host-written chapter titles | None |
| `cueprecise_purge` | Explicitly remove regenerable artifacts or data | None |

All stages other than transcription run locally. Chapter titles and summaries are produced by the
host model from retrieved evidence; CuePrecise does not create an additional Gemini call for them.

---

## Evidence bundle

```text
data/<video_id>/
  job.json                  chunk plan and progress
  raw/
    source.<ext>            source audio
    captions.json           original-language YouTube captions
    metadata.json           video metadata used for language checks
    transcripts/            per-chunk transcript and raw response
    frames/                 extracted JPEG frames
  derived/
    transcript.json         assembled Gemini transcription
    merged.json             transcription + caption evidence
    chapters.json           timestamped outline
    frames.json             frame index
    output.srt, output.txt  optional rendered files
  index.sqlite3             search index and summary
```

Every word in `merged.json` carries provenance and speaker confidence:

```json
{
  "text": "supervised", "start": 208.93, "end": 209.87,
  "speaker": "speaker:0", "speaker_status": "confirmed",
  "origin": "youtube"
}
```

---

## How it works

```text
fetch       URL             → audio + 360p video + captions + metadata
plan        audio           → chunk plan
transcribe  chunks          → timestamped Gemini transcripts    1 call/chunk
assemble    chunk results   → transcript.json
merge       transcript + captions → merged.json with provenance
chapters    merged evidence → timestamped outline
visual      evidence + video → frames and frame index
index       bundle          → persistent SQLite search index
```

Stages communicate through JSON files and can be rerun independently. Completed chunks are reused
when settings match. Raw transcription responses are saved before validation, so a parse failure
does not automatically spend another Gemini call on the same response.

Passing the video's original language with `--language` is recommended (`pl-PL`, `ko-KR`, and other
BCP-47 codes). Without it, Gemini may occasionally return a translation instead of a verbatim
transcript. CuePrecise detects that condition and stops before spending calls on remaining chunks.

---

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` for audio chunks and frame extraction
- Optional `tesseract` for frame OCR

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt  # optional OCR and timezone support
```

The installable distribution is `cueprecise-mcp`; its primary commands are `cueprecise` and
`cueprecise-mcp`. The package has not been published to PyPI yet, so install from the GitHub URL
shown above.

## Tests

```bash
python -m unittest discover -s tests
```

The suite contains 477 tests and uses the standard-library `unittest` runner. Tests do not access
the network or call the Gemini API. Tests requiring `google-genai` are skipped when the SDK is not
installed.

## Gemini API usage

Before a job starts, CuePrecise shows the estimated number of transcription calls and stops if the
configured daily limit would be exceeded. A local ledger tracks attempts by a hash of the API key
and Pacific date; it does not store the original key. Google AI Studio remains the authoritative
source for server-side usage.

---

## Roadmap

- [ ] **Clickable timestamps** — Open the exact moment in the original YouTube video.
- [ ] **Pipelined chunk transcription** — Analyze one chunk while Gemini processes the next.
- [ ] **Multi-video research** — Search and compare related videos with source-specific evidence.

---

## Known limitations

- Caption spelling errors can remain in recovered terms.
- OCR requires both `pytesseract` and the Tesseract binary.
- The caption-merge threshold was tuned on one measured video and needs broader validation.
- Across three or more chunks, a speaker absent from the overlap can remain `unresolved`; CuePrecise
  avoids assigning a potentially wrong identity.
- Real-video validation currently covers videos up to 58 minutes. A real-API interruption/resume
  path still needs end-to-end validation.
- Visual-reference phrase matching currently supports Korean and English.

## Documentation

- [`README.ko.md`](README.ko.md) — Korean README.
- [`CODE_SIGNING_POLICY.md`](CODE_SIGNING_POLICY.md) — official Windows release review and signing.
- [`PRIVACY.md`](PRIVACY.md) — API key handling, local data, external services, and uninstall behavior.
- [`CONTRACT.md`](CONTRACT.md) — authoritative data contracts and validation rules.
- [`DECISIONS/`](DECISIONS/) — design decisions and rejected alternatives.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development environment and pull request process.
- [`SECURITY.md`](SECURITY.md) — private vulnerability reporting.

## License

MIT. See [`LICENSE`](LICENSE).

The initial transcription flow was informed by the MIT-licensed
[`gemini-transcribe-wrapper`](https://pypi.org/project/gemini-transcribe-wrapper/0.0.13/).
CuePrecise is an independently written project.

CuePrecise is not affiliated with or endorsed by YouTube or Google. YouTube is a supported service,
not part of the product name.
