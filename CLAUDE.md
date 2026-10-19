# simkit

The state map in `docs/state/` is the reference for how the pipeline fits
together; start at `docs/state/README.md`. Before changing behaviour, find
the page that owns the file (`grep -rl <path> docs/state`), read it, and
update it in the same commit. Check pages with
`python3 ~/.claude/skills/state-map/check.py`.
