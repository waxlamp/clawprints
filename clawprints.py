#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "psutil",
# ]
# ///
"""
claude_sessions.py - a read-only birdseye view of Claude Code sessions.

Scans ~/.claude/projects/*/*.jsonl (the transcripts Claude Code writes for
every session) and reports one row per session: when it was last active,
its working directory, and the tail end of the conversation. Cross-checks
running processes (via psutil) to flag sessions whose cwd matches a live
`claude` process.

This script only ever reads files. It never writes, modifies, or deletes
anything, and it never talks to the network.

Usage:
  ./claude_sessions.py            # sessions active in the last 24h
  ./claude_sessions.py --hours 0  # all sessions ever
  ./claude_sessions.py --json     # machine-readable output
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
TAIL_BYTES = 64 * 1024  # how much of the end of a transcript to inspect


def tail_lines(path: Path, max_bytes: int = TAIL_BYTES) -> list[str]:
    """Return the last lines of a file without reading the whole thing."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        # If we started mid-file, the first line is probably truncated.
        return lines[1:] if size > max_bytes else lines
    except OSError:
        return []


def extract_text(message: dict) -> str:
    """Pull a plain-text snippet out of a transcript message record."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def ai_title_from_record(rec: dict) -> str:
    """System-generated title from a transcript record, if present."""
    return rec.get("aiTitle") or rec.get("title") or ""


def find_names_full_scan(path: Path) -> tuple[str, str]:
    """Fallback: scan the whole file for the latest name-bearing records.

    Returns (custom_name, ai_title). Only used when the tail held a gap,
    since renames are appended but can be buried once more conversation is
    written after them. Cheap substring check first so we only JSON-parse
    the rare matching lines.
    """
    custom = auto = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Title" not in line and '"title"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                custom = rec.get("customTitle") or custom
                auto = ai_title_from_record(rec) or auto
    except OSError:
        pass
    return custom, auto


def summarize_session(path: Path) -> dict | None:
    """Build a summary dict for one session transcript, or None if unreadable."""
    lines = tail_lines(path)
    if not lines:
        return None

    cwd = session_id = last_role = ""
    snippet = custom_name = ai_title = ""
    last_ts = None

    for line in lines:  # oldest-to-newest within the tail; last wins
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cwd = rec.get("cwd") or cwd
        session_id = rec.get("sessionId") or session_id
        if rec.get("timestamp"):
            last_ts = rec["timestamp"]
        custom_name = rec.get("customTitle") or custom_name
        ai_title = ai_title_from_record(rec) or ai_title
        if rec.get("type") in ("user", "assistant"):
            text = extract_text(rec.get("message") or {})
            if text.strip():
                last_role = rec["type"]
                snippet = " ".join(text.split())

    if not custom_name or not ai_title:
        # Either entry may be buried earlier in a long transcript.
        scanned_custom, scanned_auto = find_names_full_scan(path)
        custom_name = custom_name or scanned_custom
        ai_title = ai_title or scanned_auto

    mtime = path.stat().st_mtime
    return {
        "session_id": session_id or path.stem,
        "custom_name": custom_name,
        "ai_title": ai_title,
        "cwd": cwd,
        "transcript": str(path),
        "last_active_epoch": mtime,
        "last_active": datetime.fromtimestamp(mtime, tz=timezone.utc)
        .astimezone().isoformat(timespec="seconds"),
        "last_timestamp_in_file": last_ts,
        "last_role": last_role,
        "last_message": snippet[:160],
    }


def live_claude_cwds() -> set[str]:
    """cwds of running processes that look like the Claude Code CLI."""
    cwds = set()
    for proc in psutil.process_iter(["name", "cmdline", "cwd"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = " ".join(proc.info["cmdline"] or []).lower()
            if name == "claude" or " claude" in cmdline or cmdline.startswith("claude"):
                if proc.info["cwd"]:
                    cwds.add(proc.info["cwd"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return cwds


def ago(epoch: float) -> str:
    s = int(time.time() - epoch)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hours", type=float, default=24,
                    help="only show sessions active in the last N hours (0 = all)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    projects = CLAUDE_DIR / "projects"
    if not projects.is_dir():
        print(f"No {projects} directory found - has Claude Code run on this machine?",
              file=sys.stderr)
        return 1

    cutoff = time.time() - args.hours * 3600 if args.hours > 0 else 0
    sessions = []
    for transcript in projects.glob("*/*.jsonl"):
        if transcript.stat().st_mtime < cutoff:
            continue
        info = summarize_session(transcript)
        if info:
            sessions.append(info)

    sessions.sort(key=lambda s: s["last_active_epoch"], reverse=True)

    live = live_claude_cwds()
    seen_live_cwd = set()
    for s in sessions:  # newest transcript per live cwd is presumed the live one
        if s["cwd"] in live and s["cwd"] not in seen_live_cwd:
            s["status"] = "LIVE"
            seen_live_cwd.add(s["cwd"])
        else:
            s["status"] = "idle"

    if args.json:
        print(json.dumps(sessions, indent=2))
        return 0

    if not sessions:
        print("No sessions in the selected window. Try --hours 0 to see everything.")
        return 0

    home = str(Path.home())
    print(f"{'STATUS':<7} {'LAST ACTIVE':<12} {'CUSTOM NAME':<20} {'AI TITLE':<24} "
          f"{'SESSION':<10} {'CWD':<28} LAST MESSAGE")
    for s in sessions:
        cwd = s["cwd"].replace(home, "~", 1) if s["cwd"] else "?"
        custom = s["custom_name"] or "-"
        ai = s["ai_title"] or "-"
        msg = f"[{s['last_role']}] {s['last_message']}" if s["last_message"] else ""
        print(f"{s['status']:<7} {ago(s['last_active_epoch']):<12} "
              f"{custom[:20]:<20} {ai[:24]:<24} {s['session_id'][:8]:<10} "
              f"{cwd[:28]:<28} {msg[:50]}")
    print(f"\n{len(sessions)} session(s) · {len(live)} live claude process cwd(s) "
          f"· source: {projects}")
    return 0


if __name__ == "__main__":
    sys.exit(main())