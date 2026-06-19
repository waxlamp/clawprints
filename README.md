# clawprints
A live session reporter for Claude Code sessions

## Running

clawprints uses [uv](https://docs.astral.sh/uv/) to manage its dependency (`psutil`) inline, so no virtualenv setup is needed.

## Output

| Column | Description |
|--------|-------------|
| PID | Process ID of the running Claude process, or `-` if none |
| STATUS | Session state (see below) |
| LAST ACTIVE | Time since the transcript file was last written |
| CUSTOM NAME | Name set by `/rename` |
| AI TITLE | Auto-generated session name |
| SESSION | First 8 characters of the session ID |
| CWD | Working directory |
| LAST MESSAGE | Role and snippet of the most recent message |

### Status values

| Status | Color | Meaning |
|--------|-------|---------|
| LIVE | green | Interactive session with a detected running Claude process |
| ENDED | gray | Interactive session whose process is gone — transcript only |
| ↳WORK | yellow | Background agent job actively running |
| ↳WAIT | red | Background agent job alive in the daemon but not actively running — likely blocked on user input |
| ↳DONE | blue | Background agent job that ran to completion. Resumable via `claude --resume <id>`. |
| ↳STALE | red | Background agent job that never finished and whose process is no longer in the daemon roster |

Agent sessions (↳) are sourced from `~/.claude/jobs/` and `~/.claude/daemon/roster.json` in addition to the transcript.

## Default behavior

Running `./clawprints.py` with no flags shows all sessions regardless of age, but only those that are still active: LIVE, ↳WORK, ↳WAIT, and ↳STALE. Sessions that are no longer active — ended interactive sessions (ENDED) and completed agent jobs (↳DONE) — are hidden unless you pass `--all`. Use `--hours N` to further narrow the view to sessions active within the last N hours.

## Usage

```bash
# active sessions only (default)
./clawprints.py

# include ended interactive sessions and completed agent jobs
./clawprints.py --all

# limit to sessions active in the last 2 hours
./clawprints.py --hours 2

# machine-readable JSON output
./clawprints.py --json

# drill down into a session (match by ID prefix, custom name, or AI title)
./clawprints.py --session <prefix>

# drill down showing only the last 5 turns
./clawprints.py --session <prefix> --messages 5
```
