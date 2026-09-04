"""Single-file Claude MCPB entry point for CuePrecise and its yt-dlp subprocess."""
from __future__ import annotations

import sys
from pathlib import Path


if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import mcp_server


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
        del sys.argv[1]
        from yt_dlp import main as yt_dlp_main

        return int(yt_dlp_main() or 0)
    return mcp_server.main()


if __name__ == "__main__":
    raise SystemExit(main())
