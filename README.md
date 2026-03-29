# Alzheimer: Self-Balancing Hierarchical Memory for Claude Code

Claude Code's auto memory system stores notes in a flat index file
(`MEMORY.md`) capped at 200 lines / 25KB. When it overflows, entries at
the bottom are silently lost. Topic files still exist on disk but become
orphaned — effectively forgotten.

**Alzheimer** fixes this by transforming the flat index into a
self-balancing tree that automatically restructures itself to stay within
limits at every level.

## How it works

```
MEMORY.md                          (root: always under 150 lines)
├── _index/projects.md             (category index)
│   ├── project_api_gateway.md
│   ├── project_alzheimer.md
│   └── ...
├── _index/feedback.md             (category index)
│   ├── _index/feedback/links.md   (sub-index, if needed)
│   │   ├── feedback_broken.md
│   │   └── feedback_trailing.md
│   ├── feedback_permissions.md
│   └── ...
└── reference_github.md            (leaf: too few to categorize)
```

When MEMORY.md grows past the limit:

1. Entries are grouped by type (user, feedback, project, reference)
2. Groups of 3+ entries are pushed into category index files in `_index/`
3. MEMORY.md gets a single-line pointer per category with a count and
   summary
4. If a category index overflows, entries are split by topic keyword
5. This repeats recursively — the tree grows in depth, not width

## Installation

You're running Claude Code — just tell Claude:

> **"Install the alzheimer memory rebalancer from
> github.com/j-p-c/alzheimer"**

Claude will clone the repo, run the setup tool, and verify the hooks
are working. You'll be asked to approve the git clone and the settings
file edit. That's it.

No dependencies beyond Python 3.6+ stdlib. Works with all Claude Code
models (Opus, Sonnet, Haiku).

### What Claude will do

1. Clone this repo to a suitable location on your machine
2. Run `python3 setup.py --install` (merges hooks into your
   `~/.claude/settings.json` without disturbing existing settings)
3. Run `python3 setup.py --check` to verify

After that, the hooks fire automatically on every memory write, session
start, and compaction. No further configuration needed.

### Updating

> **"Update alzheimer"**

Claude will find the existing installation from your settings, pull the
latest changes, and re-verify. Under the hood:

1. Find the install directory by searching `~/.claude/settings.json` for
   the path to `rebalance.py`
2. Run `python3 setup.py --update` from that directory

### Manual installation

If you prefer to do it yourself:

```bash
git clone https://github.com/j-p-c/alzheimer.git ~/alzheimer
cd ~/alzheimer
python3 setup.py --install
python3 setup.py --check
```

## Usage

### Manual

```bash
# Check current state (no changes)
python3 rebalance.py ~/.claude/projects/*/memory/ --dry-run

# Rebalance
python3 rebalance.py ~/.claude/projects/*/memory/

# Find orphaned memory files
python3 rebalance.py ~/.claude/projects/*/memory/ --orphans

# Verify tree integrity
python3 rebalance.py ~/.claude/projects/*/memory/ --verify

# Custom limits
python3 rebalance.py /path/to/memory/ --max-lines 100 --max-bytes 15000

# Diagnose issues (structured report, no file changes)
python3 rebalance.py ~/.claude/projects/*/memory/ --diagnose

# File a bug report as a GitHub issue (requires gh CLI)
python3 rebalance.py ~/.claude/projects/*/memory/ --report
```

### Automatic (recommended)

Use the setup tool to configure hooks automatically:

```bash
# Preview the hook configuration
python3 setup.py

# Install hooks into ~/.claude/settings.json (merges, doesn't replace)
python3 setup.py --install

# Verify hooks are installed correctly
python3 setup.py --check
```

Or add hooks manually to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '(.tool_input.file_path // .tool_response.filePath) // empty' | { read -r f; echo \"$f\" | grep -q '/memory/' && python3 /path/to/rebalance.py \"$(dirname \"$f\")\" 2>&1 | head -5; } || true",
        "timeout": 15,
        "statusMessage": "Checking memory balance..."
      }]
    }],
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "for d in ~/.claude/projects/*/memory; do [ -f \"$d/MEMORY.md\" ] && python3 /path/to/rebalance.py \"$d\" 2>&1; done || true",
        "timeout": 15,
        "statusMessage": "Rebalancing memory tree..."
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "for d in ~/.claude/projects/*/memory; do [ -f \"$d/MEMORY.md\" ] && python3 /path/to/rebalance.py \"$d\" 2>&1; done || true",
        "timeout": 15,
        "statusMessage": "Rebalancing memory tree before compact..."
      }]
    }]
  }
}
```

Replace `/path/to/rebalance.py` with the actual path.

These hooks ensure the tree rebalances:
- **After every memory write** (PostToolUse on Write|Edit)
- **At session start** (including after compaction and /clear)
- **Before compaction** (while there's still context to analyze)

## Design

See [DESIGN.md](DESIGN.md) for the full architecture, including:
- Tree conventions and entry format
- Rebalancing algorithm
- Size budgets (depth 2 handles ~7,500 entries)
- Auto Dream compatibility
- Graceful degradation

## Compatibility

- **Auto Dream**: If Auto Dream flattens the tree during consolidation,
  the rebalancer rebuilds it on the next run. The two complement each
  other: Dream prunes stale entries, Alzheimer maintains structure.
- **Existing memories**: Migration is non-destructive. Leaf files stay
  where they are. Only index files change.
- **Other Claude instances**: The tree degrades gracefully to standard
  flat MEMORY.md. Category pointers are valid markdown links to readable
  files.

## Bug Reporting

If the rebalancer detects an anomaly (broken references, failed
rebalance, unexpected state), it can generate a structured diagnostic
report and optionally file it as a GitHub issue:

```bash
# See what's wrong
python3 rebalance.py /path/to/memory/ --diagnose

# File as a GitHub issue (requires gh CLI, asks for confirmation)
python3 rebalance.py /path/to/memory/ --report
```

Reports include only structural information (file names, line counts,
error messages) — never personal memory content. Filing is always
optional and initiated by the user.

If the rebalancer crashes during a hook run, it outputs a JSON
`systemMessage` suggesting the user run `--diagnose`. This surfaces
in Claude's UI without interrupting the conversation.

## Testing

```bash
cd /path/to/alzheimer/
python3 -m unittest test_rebalance -v
```

50 tests covering:
- Index parsing (standard and edge cases)
- Frontmatter reading
- Keyword extraction and grouping
- Level 1 rebalancing (by type)
- Level 2 rebalancing (by topic keyword)
- Level 3 deep tree construction
- Byte size limit enforcement
- Auto Dream recovery (flattened index rebuilding)
- Tree verification (broken refs, orphans, size violations)
- Depth limiting (no infinite recursion)
- Edge cases (empty dirs, malformed files, unicode, concurrent writes)
- Dry-run safety
- Idempotency
- Config file loading (.alzheimer.conf)
- Limit resolution priority (CLI > config > defaults)

## License

MIT No Attribution (MIT-0)
