# clawprints
A live session reporter for Claude Code sessions

## Running

clawprints uses [uv](https://docs.astral.sh/uv/) to manage its dependency (`psutil`) inline, so no virtualenv setup is needed.

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
