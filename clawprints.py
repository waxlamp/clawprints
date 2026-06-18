#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
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


def read_session_pids(sessions_dir: Path) -> dict[str, int]:
    """Return sessionId → pid for all registered interactive sessions."""
    result = {}
    if not sessions_dir.is_dir():
        return result
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sid = data.get("sessionId")
            pid = data.get("pid")
            if sid and pid:
                result[sid] = pid
        except (OSError, json.JSONDecodeError):
            continue
    return result


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


_STATUS_COLORS = {
    "ENDED": "\033[90m",
    "LIVE":  "\033[32m",
    "↳DONE":  "\033[34m",
    "↳WORK":  "\033[33m",
    "↳WAIT":  "\033[31m",
    "↳STALE": "\033[31m",
}
_RESET = "\033[0m"
_VISUAL_STATUS_WIDTH = 7  # "STATUS" header + 1


def status_cell(status: str) -> str:
    color = _STATUS_COLORS.get(status, "")
    cell = f"{color}{status}{_RESET}"
    return cell + " " * max(0, _VISUAL_STATUS_WIDTH - len(status))


def read_jobs(jobs_dir: Path) -> dict[str, dict]:
    """Read state.json for each background job. Keyed by daemonShort (8-char ID)."""
    jobs = {}
    if not jobs_dir.is_dir():
        return jobs
    for state_file in jobs_dir.glob("*/state.json"):
        try:
            state = json.loads(state_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        short = state.get("daemonShort") or state_file.parent.name
        jobs[short] = state
    return jobs


def read_roster(daemon_dir: Path) -> dict[str, int]:
    """Return short_id → pid for workers currently listed in the daemon roster."""
    roster_file = daemon_dir / "roster.json"
    try:
        roster = json.loads(roster_file.read_text())
        return {short: w["pid"] for short, w in roster.get("workers", {}).items()
                if "pid" in w}
    except (OSError, json.JSONDecodeError):
        return {}


def ago(epoch: float) -> str:
    s = int(time.time() - epoch)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def find_session_transcripts(projects: Path, needle: str) -> list[tuple[Path, dict]]:
    """Return (path, info) pairs whose session ID, custom name, or AI title match needle."""
    needle = needle.lower()
    results: dict[Path, dict] = {}
    for transcript in projects.glob("*/*.jsonl"):
        if transcript.stem.lower().startswith(needle):
            results[transcript] = {"session_id": transcript.stem,
                                   "custom_name": "", "ai_title": ""}
            continue
        info = summarize_session(transcript)
        if info and (needle in info["custom_name"].lower()
                     or needle in info["ai_title"].lower()):
            results[transcript] = info
    return list(results.items())


def show_session(path: Path, max_messages: int) -> int:
    """Print the last max_messages user/assistant turns from a transcript."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"Cannot read {path}: {e}", file=sys.stderr)
        return 1

    turns = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        text = extract_text(rec.get("message") or {})
        if not text.strip():
            continue
        turns.append((rec["type"], rec.get("timestamp", ""), text))

    for role, ts, text in turns[-max_messages:]:
        label = f"[{role}]"
        time_str = ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                time_str = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                time_str = ts
        print(f"\033[1m{label}\033[0m {time_str}")
        print(text.strip())
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hours", type=float, default=0,
                    help="only show sessions active in the last N hours (default 0 = all)")
    ap.add_argument("--all", action="store_true",
                    help="include ended and completed sessions (hidden by default)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--session", metavar="PREFIX",
                    help="drill down into a session: print its last --messages turns")
    ap.add_argument("--messages", type=int, default=20, metavar="N",
                    help="number of turns to show with --session (default 20)")
    args = ap.parse_args()

    projects = CLAUDE_DIR / "projects"
    if not projects.is_dir():
        print(f"No {projects} directory found - has Claude Code run on this machine?",
              file=sys.stderr)
        return 1

    if args.session:
        matched = find_session_transcripts(projects, args.session)
        if not matched:
            print(f"No session found matching '{args.session}'.", file=sys.stderr)
            return 1
        if len(matched) > 1:
            print(f"Ambiguous: '{args.session}' matches {len(matched)} sessions:",
                  file=sys.stderr)
            for path, info in sorted(matched):
                name = info["custom_name"] or info["ai_title"] or info["session_id"]
                print(f"  {info['session_id'][:8]}  {name}", file=sys.stderr)
            return 1
        return show_session(matched[0][0], args.messages)

    jobs = read_jobs(CLAUDE_DIR / "jobs")
    active_workers = read_roster(CLAUDE_DIR / "daemon")

    cutoff = time.time() - args.hours * 3600 if args.hours > 0 else 0
    sessions = []
    for transcript in projects.glob("*/*.jsonl"):
        if transcript.stat().st_mtime < cutoff:
            continue
        info = summarize_session(transcript)
        if not info:
            continue
        short = info["session_id"][:8]
        if short in jobs:
            job = jobs[short]
            info["custom_name"] = info["custom_name"] or job.get("name", "")
            info["ai_title"] = info["ai_title"] or job.get("detail", "")
            if not info["last_message"]:
                info["last_message"] = job.get("intent", "")[:160]
                info["last_role"] = ""
            info["_job_state"] = job.get("state", "")
            info["_job_tempo"] = job.get("tempo", "")
            info["_job_has_output"] = job.get("output") is not None
            info["_is_agent"] = True
        sessions.append(info)

    sessions.sort(key=lambda s: s["last_active_epoch"], reverse=True)

    session_pids = read_session_pids(CLAUDE_DIR / "sessions")
    for s in sessions:
        short = s["session_id"][:8]
        s["pid"] = None
        if s.get("_is_agent"):
            if s["_job_state"] == "done" and s["_job_has_output"]:
                s["status"] = "↳DONE"
            elif short in active_workers:
                s["status"] = "↳WORK" if s["_job_tempo"] == "working" else "↳WAIT"
                s["pid"] = active_workers[short]
            else:
                s["status"] = "↳STALE"
        else:
            pid = session_pids.get(s["session_id"])
            if pid and is_pid_alive(pid):
                s["status"] = "LIVE"
                s["pid"] = pid
            else:
                s["status"] = "ENDED"

    if not args.all:
        sessions = [s for s in sessions if s["status"] not in ("ENDED", "↳DONE")]

    if args.json:
        print(json.dumps(sessions, indent=2))
        return 0

    if not sessions:
        msg = "No active sessions."
        if not args.all:
            msg += " Try --all to include ended and completed sessions."
        print(msg)
        return 0

    _NAME_WIDTH = 28
    home = str(Path.home())
    print(f"{'PID':<8} {'STATUS':<{_VISUAL_STATUS_WIDTH}} {'LAST ACTIVE':<12} "
          f"{'NAME':<{_NAME_WIDTH}} {'SESSION':<37} {'CWD':<28} LAST MESSAGE")
    for s in sessions:
        cwd = s["cwd"].replace(home, "~", 1) if s["cwd"] else "?"
        pid_str = str(s["pid"]) if s.get("pid") else "-"
        if s["custom_name"]:
            raw = s["custom_name"][:_NAME_WIDTH]
            name_cell = f"\033[35m{raw}\033[0m" + " " * (_NAME_WIDTH - len(raw))
        else:
            raw = (s["ai_title"] or "-")[:_NAME_WIDTH]
            name_cell = raw + " " * (_NAME_WIDTH - len(raw))
        if s["last_message"]:
            role_prefix = f"[{s['last_role']}] " if s["last_role"] else ""
            msg = role_prefix + s["last_message"]
        else:
            msg = ""
        print(f"{pid_str:<8} {status_cell(s['status'])} {ago(s['last_active_epoch']):<12} "
              f"{name_cell} {s['session_id']:<37} "
              f"{cwd[:28]:<28} {msg[:50]}")
    agent_count = sum(1 for s in sessions if s.get("_is_agent"))
    live_count = sum(1 for s in sessions if s["status"] == "LIVE")
    print(f"\n{len(sessions)} session(s) · {agent_count} agent · "
          f"{live_count} live · source: {projects}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
