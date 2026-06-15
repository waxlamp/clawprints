# clawprints
A live session reporter for Claude Code sessions

## Running

clawprints uses [uv](https://docs.astral.sh/uv/) to manage its dependency (`psutil`) inline, so no virtualenv setup is needed.

## Output

| Column | Description |
|--------|-------------|
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
| ENDED | gray | Interactive session whose process is gone — transcript only, not resumable |
| ↳WORK | yellow | Background agent job actively running |
| ↳WAIT | red | Background agent job alive in the daemon but not actively running — likely blocked on user input |
| ↳DONE | blue | Background agent job that ran to completion |
| ↳STALE | red | Background agent job that never finished and whose process is no longer in the daemon roster |

Agent sessions (↳) are sourced from `~/.claude/jobs/` and `~/.claude/daemon/roster.json` in addition to the transcript.

## Usage

```bash
# sessions active in the last 24 hours (default)
./clawprints.py

# all sessions ever recorded
./clawprints.py --hours 0

# machine-readable JSON output
./clawprints.py --json

# drill down into a session (by ID prefix, custom name, or AI title)
./clawprints.py --session <prefix>

# show more or fewer turns in drill-down mode (default: 20)
./clawprints.py --session <prefix> --messages 10
```
