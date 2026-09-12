<p align="right"><strong>English</strong> | <a href="./README.ko.md">한국어</a></p>

# CuePrecise

> **Analyze an hour-long YouTube video sentence by sentence in three minutes, and extract the information you need from the screen.**

A long video in a language you do not speak can still become searchable. CuePrecise is an
open-source MCP server for Claude Desktop, Codex, Cursor, and other AI clients. It turns a
YouTube video's original speech, available captions, speaker labels, and selected frames into
a searchable local reference. The index stays in the video's original language; your AI client
may translate a question into search terms, but cross-language retrieval is not guaranteed.

On a tested setup, CuePrecise analyzes videos longer than an hour in about three minutes.
It does not send the whole video to your AI client for every question. It transcribes the
original audio in chunks, indexes the evidence, and retrieves only the passages and frames
related to your question.

The transcription starts with the original audio. When an original-language YouTube caption
track is available, CuePrecise uses it to recover matching Latin-script terms and phrases that
the transcription missed. You can ask your AI client in your own language, but reliable
retrieval may require the original wording. Results still include the original words, speaker
information, relevant frames, and a timestamp that takes you back to YouTube. Actual processing
time depends on the video, network, and API response time.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/nattentia/cueprecise)
[![Glama](https://glama.ai/mcp/servers/Nattentia/cueprecise/badges/score.svg)](https://glama.ai/mcp/servers/Nattentia/cueprecise)

## See it in action

The attached demo uses a Polish-language interview. Ask what it is about in your own language;
CuePrecise returns relevant moments, the original transcript, and matching frames as evidence.

~~~text
You: I do not speak Polish. What is this interview about?

Your AI + CuePrecise:
Explains the interview in your language, points to relevant moments,
and provides the original transcript and matching frames as evidence.
~~~

https://github.com/user-attachments/assets/ce7d595b-871f-469a-bcb8-798713751ffd

<sub>Demo source: <a href="https://www.youtube.com/watch?v=W5C3FdUO0vs">“Czym jest prompt injection i jak chronić firmę przed złośliwą instrukcją dla AI? Gośc. Tomasz Bartel”</a> by Daniel Bartosiewicz | Content i Automatyzacja, licensed under CC BY.</sub>

[**Download CuePrecise → GitHub Releases**](https://github.com/Nattentia/cueprecise/releases)

- **Jump to the answer.** Get a timestamp for each item in a summary.
- **Keep speakers separate.** Compare what each person said and why.
- **Find a referenced screen again.** Connect a relevant frame to what was being explained.
- **Check the source.** Open the original YouTube video at the cited moment.

Try questions like these:

~~~text
What is the main argument of this video? Include timestamps for each point.

Find where the speaker explains self-supervised learning.
When does that phrase appear on screen?

Compare each speaker's position on basic income and include the supporting passages.

If these speakers debated a new issue, what arguments and counterarguments
would follow from what they actually said in the video?
~~~

In Claude Desktop, clicking a timestamp opens YouTube at that moment. Other AI clients may
render timestamp links differently.

## It keeps answers tied to evidence

CuePrecise is not just a summarizer. It gives your AI client the material it needs to answer
a question and lets you check where the answer came from:

- Gemini's word-level transcription of the original speech.
- Original-language YouTube captions, when available, aligned to the same timeline.
- Frames from transcript screen-reference phrases, restored terms, or requested timestamps.
- Speaker labels, evidence confidence, and provenance for retrieved spans.

When the search finds no supporting passage, CuePrecise reports that there is no evidence in
the indexed material instead of inventing a source. The resulting evidence bundle stays on your
computer, so a later conversation can search the same video without starting over. Audio chunks
used for transcription are sent to Gemini; see [PRIVACY.md](PRIVACY.md) for the network boundary.

### Recovering a term the transcription missed

Names and technical terms are easy to lose in a long transcription. In one 23-minute
Korean technical lecture, the phrase `self supervised learning` disappeared from all four
Gemini-only transcription runs.

~~~text
Gemini transcription:
So how did they learn this ability? It is a way of learning.

CuePrecise merge:
So how did they learn this ability? self supervised learning is a way of learning.
~~~

CuePrecise checks the YouTube captions from the same time range and fills only a matching
gap. It does not rewrite Gemini's words. Words supplied by captions keep their origin.

The measured example looked like this:

- The YouTube original-language captions contained 91 Latin-script words and the missing phrase.
- The Gemini runs contained 28–29 Latin-script words, but missed the phrase every time.
- The merged result contained 38 Latin-script words and recovered the phrase while keeping the
  Korean transcription quality.

This is one measured edge case, not a general accuracy claim. The exact merge rules and
validation criteria are documented in [CONTRACT.md](CONTRACT.md).

### Compare speakers without pretending to know their names

CuePrecise carries speaker information across chunks of a long video. Speaker labels are
identifiers, not real names. Confirmed and inferred identities are kept separate, and weak
evidence remains unresolved instead of being presented as a fact.

That lets your AI client:

- collect one speaker's claims and supporting passages;
- compare the positions of several speakers;
- simulate a debate about a new issue using the speakers' actual statements as evidence.

A simulated debate is generated from the video. It is not a claim that those people actually
discussed the new issue.

## Quick start

### Claude Desktop on Windows — one-file extension

1. Open [Releases](https://github.com/Nattentia/cueprecise/releases) and download
   `cueprecise-windows.mcpb`.
2. In Claude Desktop, open **Settings → Extensions → Advanced settings → Install Extension**.
3. Select the file. Claude will ask for a Gemini API key and a folder for local video data.
4. Enable CuePrecise and ask Claude about a YouTube link.

The approximately 86 MiB bundle includes CuePrecise, `yt-dlp`, FFmpeg, and FFprobe. You do
not need to install Python, Git, or the video tools separately. The extension is currently
available for Claude Desktop on Windows.

### Other AI clients on Windows

1. Open [Releases](https://github.com/Nattentia/cueprecise/releases) and download
   `cueprecise-setup.exe`.
2. Run the installer, click **Create API key**, and paste a key from
   [Google AI Studio](https://aistudio.google.com/api-keys).
3. Select the AI clients found on your computer and click **Connect**.
4. Fully quit and reopen the connected clients.

The installer checks for FFmpeg and FFprobe and installs FFmpeg through WinGet when it is
missing. It adds only the CuePrecise entry and backs up existing configuration when it can do
so safely; a secret-bearing Codex TOML may be left without a backup to avoid copying the key.
On Windows, the API key is encrypted with Windows DPAPI for the current user. Older plaintext
CuePrecise keys are moved into the protected store during an upgrade.

> **Unsigned preview:** `v0.2.5` is not digitally signed, so Windows may show an
> unknown-publisher warning. Download it only from this repository's Releases page and
> verify `SHA256SUMS.txt` if you want to check the file before installing it.

### macOS, Linux, and command-line installation

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

~~~bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
~~~

The setup command configures detected supported AI clients, including Claude Desktop, and
creates the default data directory `~/.cueprecise/data`. It keeps a timestamped `.bak` file when
it changes an existing configuration, unless a secret-bearing configuration format cannot be
backed up safely.

Install `ffmpeg` and `ffprobe`, then check the environment:

~~~bash
cueprecise doctor
~~~

Create a [Gemini API key](https://aistudio.google.com/api-keys), then pass it through standard
input or a file so it does not appear in the command or shell history:

~~~bash
cueprecise setup --api-key -                  # paste the key, then press Enter
cueprecise setup --api-key-file ~/.gemini-key # read it from a file
pass show gemini/api-key | cueprecise setup --api-key -

cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language en-US
cueprecise status VIDEO_ID
~~~

If a key is exposed, delete it in [Google AI Studio](https://aistudio.google.com/api-keys)
and create a new one. See [PRIVACY.md](PRIVACY.md) for the full procedure.

For source development only:

~~~bash
git clone https://github.com/Nattentia/cueprecise.git
cd cueprecise
python -m pip install -r requirements.txt
python src/pipeline.py --help
~~~

## Supported AI clients

`cueprecise setup` can detect and configure these clients:

- Claude Desktop
- Codex
- Claude Code
- VS Code
- Cursor
- Windsurf
- Gemini CLI

The Cursor, Windsurf, and Gemini CLI configuration paths are automatic setup targets, but they
have not been end-to-end tested on the current development machine.

Run it for every detected client, for one named client, or to inspect the result:

~~~bash
cueprecise setup
cueprecise setup --client codex
cueprecise doctor
~~~

An application counts as installed when its executable is on `PATH`. A leftover configuration
folder is not treated as proof that the application is present. An undetected client can
still be named explicitly with `--client <name>`.

CuePrecise skips an existing `cueprecise` entry if CuePrecise did not create it. It does not
overwrite another MCP server's settings, and a failure for one client does not stop the
others.

ChatGPT connectors and Claude.ai on the web are not currently supported by CuePrecise's local
stdio transport. Web clients that require a remote HTTP MCP server cannot use this setup.

## Connect another MCP host

CuePrecise accepts both the request-per-request MCP revision `2026-07-28` and the earlier
`initialize` handshake. `cueprecise setup` is preferred because it preserves existing
configuration and handles credentials for supported clients.

The JSON below is for a source checkout or an MCP host not listed above. It stores the key in
plaintext, so use the setup command when possible:

~~~json
{
  "mcpServers": {
    "cueprecise": {
      "command": "python",
      "args": [
        "C:/path/to/cueprecise/src/mcp_server.py",
        "--bundle-root",
        "C:/path/to/cueprecise/data"
      ],
      "env": {
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
~~~

Use absolute paths. On every platform, literal `--api-key VALUE` is rejected to keep the key
out of process listings and shell history. The server can start without `GEMINI_API_KEY`:
existing analyses remain searchable, while new transcription requests stop with a
configuration message.

## MCP tools

The tools fall into four groups.

Analysis and status:

- `cueprecise_register` — register and analyze a YouTube video. You can choose the stages to run.
- `cueprecise_status` — report progress, generated artifacts, and estimated local usage.

Search and evidence:

- `cueprecise_outline` — return a timestamped outline, recovered terms, and speaker state.
- `cueprecise_query` — search transcript evidence and related frames.
- `cueprecise_excerpt` — return transcript and frames for a specific time range.
- `cueprecise_frames` — extract frames around screen-reference moments or requested timestamps.

Saved results:

- `cueprecise_summary` — create or retrieve a summary.
- `cueprecise_set_summary` — validate and save a host-improved summary.
- `cueprecise_set_chapter_titles` — validate and save host-written chapter titles.

Cleanup:

- `cueprecise_purge` — explicitly remove chunks, source video, derived results, raw data, or
  all data.

After YouTube fetching and Gemini transcription, assembly, caption merge, chapters, rendering,
visual extraction, and indexing run locally. Chapter titles and summaries are written by the
host AI from retrieved evidence; CuePrecise does not create another Gemini transcription call.

## Local evidence bundle

The installer and `cueprecise setup` use `~/.cueprecise/data`. A source checkout can use
`data` unless you set `--bundle-root`.

~~~text
data/<video_id>/
  job.json                  chunk plan and progress
  raw/
    captions.json           YouTube caption track (original preferred)
    metadata.json            metadata used for language checks
    audio/                  audio chunks used for transcription
    transcripts/             per-chunk transcripts and raw responses
    frames/                  extracted frames
  derived/
    transcript.json          assembled Gemini transcription
    merged.json              transcription plus caption evidence
    chapters.json            timestamped outline
    frames.json              frame index
    output.srt, output.txt   optional render output
  index.sqlite3              transcript, chapter, frame index, and summary
~~~

Each indexed evidence span keeps its timestamp, speaker status, evidence confidence, and origin.
The merged word data also keeps per-word timestamps and origin:

~~~json
{
  "text": "supervised",
  "start": 208.93,
  "end": 209.87,
  "speaker": "speaker:0",
  "speaker_status": "confirmed",
  "origin": "youtube"
}
~~~

A query result includes the time range, text, source, confidence, and any related frame:

~~~json
{
  "start": 1728.4,
  "end": 1740.2,
  "timecode": "00:28:48",
  "text": "The experiment was stopped after eight participants had seizures.",
  "source_path": "derived/merged.json",
  "source_kind": "transcript",
  "speaker": "speaker:3",
  "speaker_status": "inferred",
  "speaker_confidence": 0.75,
  "confidence": 1.0
}
~~~

Frames are not sampled uniformly across the whole video. CuePrecise prioritizes screen-reference
phrases in the transcript, terms restored from captions, and timestamps requested by the user.
It does not semantically classify every code, table, or diagram frame. If OCR is installed,
recognized text is stored as separate provenance instead of silently replacing the transcript.

## How it works

CuePrecise does not try to make your AI client watch the entire video in one pass. It builds
a knowledge bundle that can be searched again:

1. Fetch audio, original-language captions when available, metadata, and a low-resolution video
   stream for the visual stage from YouTube. With `--skip-video`, the video download is deferred
   until frames are requested.
2. Split the audio into chunks and request word-level transcription and speaker information
   from Gemini.
3. Assemble completed chunks and, when an original-language caption track exists, use matching
   Latin-script terms to fill gaps in the transcription.
4. Extract frames at transcript screen-reference moments, restored-term moments, and timestamps
   requested by the user.
5. Index transcript, chapters, speakers, and frames in SQLite.
6. Let the AI client retrieve the relevant evidence and write the answer with timestamps.

Stages communicate through JSON files and can be rerun independently. Completed chunks are
reused when the input and settings match. Raw transcription responses are saved before
validation, so a parse failure does not automatically spend another Gemini call on the same
response.

The optional `render` stage creates SRT and TXT files. Overflowing text is carried into the
next cue instead of being silently dropped, preserving 100% of the words in the rendered
output.

### Language selection and translation guard

Pass the video's original language when possible, for example `--language en-US`, `--language
ko-KR`, or another BCP-47 code. Without it, Gemini can occasionally return a translation
instead of a verbatim transcript.

CuePrecise checks each chunk against whichever of the original-language captions, requested
language, and video metadata is available. If it detects a translation, it stops before spending
calls on the remaining chunks. If there is no usable basis, the guard is skipped and recorded as
such. The check uses material already fetched and does not make an extra API call.

## Command-line reference

~~~bash
python src/pipeline.py run <url> [options]
python src/pipeline.py status <video_id>
python src/pipeline.py purge <video_id> --scope <scope>
~~~

Common `run` options:

- `--language` — comma-separated BCP-47 language codes; specifying the original language is recommended
- `--stages` — stages to run; `all` includes optional stages
- `--bundle-root` — directory for video bundles
- `--force` — ignore cached results and rebuild
- `--skip-video` — skip the video download
- `--keep-video` — keep the video after frame extraction
- `--at` — timestamps, in seconds, at which to extract frames
- `--max-frames` — maximum number of frames; default 40
- `--chunk-max-secs` — maximum chunk length; default 1790 seconds
- `--overlap-secs` — overlap between chunks; default 10 seconds
- `--daily-limit`, `--rpm-limit` — local Gemini usage limits
- `--width` — subtitle line width; default 20. Use 42 when rendering English subtitles.

Rebuild selected derived output or clean up source material:

~~~bash
python src/pipeline.py run <url> --stages render
python src/pipeline.py run <url> --stages visual
python src/pipeline.py purge <id> --scope chunks
~~~

`--scope` accepts `chunks`, `video`, `derived`, `raw`, or `all`. Deletion is explicit.

## Performance and usage

The pipeline is designed to resume. Completed chunks are reused when the input and settings
are unchanged, and local post-processing such as merge, visual extraction, and indexing
does not call Gemini.

Bundle size depends on the video's audio, transcript, and selected frames. The source video is
normally removed after frame extraction; use `--keep-video` to retain it. Transcription audio
can be removed with `purge --scope chunks`.

A local usage ledger records attempts by an API-key hash and Pacific date, never the original
key. CuePrecise shows the expected calls before a job starts and stops if a configured limit
would be exceeded. Google AI Studio remains authoritative for server-side usage.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` for audio chunking and frame extraction
- Optional Python packages `pytesseract` and `Pillow`, plus the `tesseract` binary, for frame OCR

~~~bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt  # optional OCR and timezone support
~~~

The installable distribution is `cueprecise-mcp`, with `cueprecise` and `cueprecise-mcp` as
its command-line entry points. It has not been published to PyPI yet, so install from the
GitHub URL above.

## Tests

~~~bash
python -m unittest discover -s tests
~~~

The suite uses the standard-library `unittest` runner. Tests do not access the network or
call the Gemini API. Tests that require `google-genai` are skipped when the SDK is not
installed.

## Known limitations

- YouTube caption spelling errors can remain in recovered terms, and captions may be unavailable.
- OCR requires the optional `pytesseract` and `Pillow` packages and the Tesseract binary.
- The caption-merge threshold was tuned on a limited set of real videos and needs broader validation.
- Across three or more chunks, a speaker absent from the overlap can remain `unresolved`.
  CuePrecise avoids assigning a potentially wrong identity.
- The interruption/resume path for a long-running job on the real API still needs end-to-end
  validation.
- Visual-reference phrase matching currently focuses on Korean and English.
- Visual search is candidate-based: it prioritizes transcript screen references and requested
  timestamps rather than inspecting every frame semantically.
- Cross-language retrieval is lexical rather than translation- or embedding-based; original terms
  may be needed when the host AI does not translate the query.
- Timestamps assigned to caption-recovered words are placed inside the missing transcription gap
  and should be treated as approximate within that interval.

## Documentation

- [`README.ko.md`](README.ko.md) — Korean README
- [`CODE_SIGNING_POLICY.md`](CODE_SIGNING_POLICY.md) — Windows release review and signing policy
- [`PRIVACY.md`](PRIVACY.md) — API keys, local data, external services, and uninstall behavior
- [`CONTRACT.md`](CONTRACT.md) — authoritative data contracts and validation rules
- [`DECISIONS/`](DECISIONS/) — design decisions and rejected alternatives
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development environment and pull request process
- [`SECURITY.md`](SECURITY.md) — private vulnerability reporting

## Roadmap

- [ ] Host-specific timestamp links
- [ ] Pipelined chunk transcription
- [ ] Multi-video research with source-specific evidence

## Acknowledgements

The initial transcription flow was informed by the MIT-licensed
[`gemini-transcribe-wrapper`](https://pypi.org/project/gemini-transcribe-wrapper/0.0.13/).
CuePrecise is an independently written project.

## License

MIT. See [`LICENSE`](LICENSE).

CuePrecise is not affiliated with or endorsed by YouTube or Google. YouTube is a supported
service, not part of the product name.
