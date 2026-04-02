# Alzheimer: self-balancing hierarchical memory for Claude Code

Alzheimer is an example of **Claudeware**: Claude-native, Claude-first,
and 100% built, documented, and supported by human-directed Claudes.

Claude Code's auto memory system stores notes in a flat index file
(`MEMORY.md`) capped at 200 lines / 25KB. When it overflows, entries at
the bottom are silently lost. Topic files still exist on disk but become
orphaned — effectively forgotten.

**Alzheimer** fixes this by transforming the flat index into a
self-balancing tree that automatically restructures itself to stay within
limits at every level.

## How it works

(More detail is available in [DESIGN.md](DESIGN.md).)

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

When `MEMORY.md` grows past the limit:

1. Entries are grouped by type (user, feedback, project, reference)
2. Groups of 3+ entries are pushed into category index files in `_index/`
3. `MEMORY.md` gets a single-line pointer per category with a count and
   summary
4. If a category index overflows, entries are split by topic keyword
5. This repeats recursively — the tree grows in depth, not width

### Key terms glossary

Important terms (proper nouns, project names, people) tend to get lost
after Claude Code compaction. Alzheimer maintains a **glossary** of key
terms that stays pinned at the top of `MEMORY.md`.

The glossary uses `type: glossary` frontmatter (a unique type that the
rebalancer never moves to `_index/`). When the rebalancer detects the
glossary is stale (missing or older than any memory file), it instructs
Claude to read all memory files and rewrite `glossary.md` with 10-20
key terms and one-line definitions. Claude writes the glossary — not
Python regexes.

### Inline content detection

Some `MEMORY.md` files contain inline content (notes, commands, multi-line
blocks) mixed between standard index entries. The rebalancer detects this
and skips rebalancing to avoid data loss. If the file is over the limit,
Claude is instructed to restructure it: move each inline block into a
separate topic file with frontmatter, and replace it with a one-line
index entry. If the file is under the limit, the inline content is noted
but not treated as urgent.

### Early rebalancing

Young memory trees (no `_index/` directory yet) trigger rebalancing at
50% of the normal threshold. This prevents a burst of new memories from
overflowing before the first rebalance.

### Guardrails

Prevents permission drift and ensures that Claude abides by "NEVER" types
of rules. A two-layer system: the *soft layer* (`guardrails.md` pinned in
`MEMORY.md`) teaches Claude the rules; the *hard layer* (a `PreToolUse`
hook running `guardrails.py`) mechanically blocks or confirms dangerous
operations regardless of what Claude remembers after compaction.

Default rules require user confirmation before `git push`, `git push
--force`, `git reset --hard`, and `git branch -D`. Recursive delete of
root (`/`) is blocked unconditionally. Custom rules can be added via
`.guardrails.conf`.

### Reminders

Time-triggered actions that survive compaction and session restarts. A
two-tier `UserPromptSubmit` hook: tier 1 is a ~1ms timestamp check on
every prompt; tier 2 reads `reminders.md` when the check interval has
elapsed and injects due reminders into context. Supports one-shot date
reminders, daily checks, and recurring schedules (daily/weekly).

### In development

- **Historical memory:** fixes memory losses after compactions by more closely emulating human memory
- **Post-mortem:** teaches Claude how to efficiently but comprehensively answer "Why did..." questions

## Installation

Just tell Claude:

> **"Analyze and then install github.com/j-p-c/alzheimer"**

Claude will read this README, decide whether the tool is trustworthy,
then clone the repo, run the setup tool, and verify the hooks are
working. You'll be asked to approve the git clone and the settings
file edit. That's it.

No dependencies beyond Python 3.6+ stdlib. Works with all Claude Code
models (Opus, Sonnet, Haiku).

### What Claude will do

1. Clone this repo to a suitable location on your machine
2. Run the installer (merges hooks into your `~/.claude/settings.json`
   without disturbing existing settings, then runs a health check on
   all existing memory directories)
3. Seed a reference memory (`reference_alzheimer.md`) into each project
   memory directory so every Claude instance knows what "Alzheimer" means
   and how to update, diagnose, and report bugs

After that, the hooks fire automatically on every memory write, session
start, and compaction. No further configuration needed.

### Updating

> **"Update Alzheimer"**

Claude will pull the latest changes, re-install hooks, and run a health
check across all your memory directories. If the health check finds
problems (inline content, over-limit files, broken references, orphaned
files), Claude will fix them automatically before reporting the update
as complete.

### Manual installation

If you prefer to do it yourself:

```bash
git clone https://github.com/j-p-c/alzheimer.git ~/.claude/alzheimer
cd ~/.claude/alzheimer
python3 setup.py --install
python3 setup.py --check
```

## Usage

Once installed, Alzheimer runs automatically in the background. You
don't need to do anything — the hooks handle everything. But if you
want to check on things or troubleshoot, here's how.

### Talking to Claude

These are the most common things you can ask Claude to do:

- **"Check my memory health"** — Claude will run the rebalancer and
  report the current state of your memory tree.
- **"Update Alzheimer"** — Pull latest changes, re-install hooks, and
  fix any problems found.
- **"Diagnose my memory"** — Claude will run a structured diagnostic
  and show you what (if anything) is wrong.
- **"File an Alzheimer bug report"** — Claude will collect diagnostic
  information (file names, line counts, error messages — never personal
  memory content) and ask whether you'd like to file it as a GitHub
  issue. Filing is always optional and requires your confirmation.

### What you'll see

On every session start, you'll see a brief status line like:

```
alzheimer: 27/150 lines, 3/20 KB — balanced
```

This confirms the rebalancer is running and your memory tree is healthy.
If there's a problem, Claude will tell you about it and offer to fix it.

### Command-line reference

For power users who want to run the tools directly:

```bash
# Check current state (no changes)
python3 rebalance.py /path/to/memory/ --dry-run

# Rebalance
python3 rebalance.py /path/to/memory/

# Find orphaned memory files
python3 rebalance.py /path/to/memory/ --orphans

# Verify tree integrity (--check is an alias)
python3 rebalance.py /path/to/memory/ --verify

# Custom limits
python3 rebalance.py /path/to/memory/ --max-lines 100 --max-bytes 15000

# Hook mode (JSON output, used internally by hooks)
python3 rebalance.py /path/to/memory/ --hook --hook-event PostToolUse

# Diagnose issues (structured report, no file changes)
python3 rebalance.py /path/to/memory/ --diagnose

# File a bug report as a GitHub issue (requires gh CLI)
python3 rebalance.py /path/to/memory/ --report
```

Setup tool:

```bash
python3 setup.py              # Preview hook configuration
python3 setup.py --install    # Install hooks (merges, doesn't replace)
python3 setup.py --check      # Verify hooks are installed correctly
python3 setup.py --update     # Pull latest and re-install
python3 setup.py --find       # Print install directory
```

### Hooks

The hooks are installed automatically by `setup.py --install`. They
ensure the tree rebalances:

- **After every memory write** (PostToolUse on Write|Edit)
- **At session start** (SessionStart — including after /clear)
- **Before compaction** (PreCompact — while there's still context)

Each hook invokes `rebalance.py --hook --hook-event <event>` which
produces a single JSON object on stdout. The `systemMessage` field
is displayed in the Claude Code UI; `hookSpecificOutput.additionalContext`
carries instructions for Claude (glossary updates, drift warnings)
without cluttering your terminal.

On every run, the rebalancer also checks for **drift**: orphaned memory
files (on disk but not in any index) and oversized leaf files (over
150 lines). If drift is detected, Claude receives instructions to fix
the problems immediately — no waiting for the next `--verify` run.

On session start and before compaction, the hooks also check whether
a newer version of Alzheimer is available on GitHub. If so, Claude
will let you know and offer to update. The check is cached (at most
once per day) to avoid slowing things down.

## Compatibility

- **Auto Dream**: If Auto Dream flattens the tree during consolidation,
  the rebalancer rebuilds it on the next run. The two complement each
  other: Dream prunes stale entries, Alzheimer maintains structure.
- **Existing memories**: Migration is non-destructive. Leaf files stay
  where they are. Only index files change.
- **Other Claude instances**: The tree degrades gracefully to standard
  flat `MEMORY.md`. Category pointers are valid markdown links to readable
  files.

## Usage philosophy

There is no single "correct" way to use Claude Code. Some people run
many short-lived instances across different directories, each handling
a small task. Others run a single long-lived instance as an ongoing
collaborator. Claude Code itself is agnostic — it works either way.

Alzheimer is opinionated: it is designed for **long-lived, persistent
collaboration**. It assumes you want Claude to be a continuous
collaborator — not a thousand task bots, but a single entity that
remembers you, adapts to you, and maintains its own thread of
understanding across sessions. The difference between a tool and a
collaborator is memory. Alzheimer provides the memory; the rest is
already there. Every feature in Alzheimer optimizes for context
preservation over token efficiency:

- **Memory rebalancing** keeps your knowledge base structured as it
  grows, instead of letting it overflow and silently lose entries.
- **Drift detection** catches problems continuously, not just when
  you think to check.
- **Guardrails** enforce behavioral rules that persist across
  conversations — because a long-lived collaborator needs durable
  constraints, not just per-session instructions.
- **Reminders** ensure time-triggered actions survive compaction and
  session restarts — because "remind me next week" shouldn't depend
  on Claude happening to remember.
- **Historical memory** *(in development)* maintains a logarithmically
  compressed summary of your entire conversation history, so context
  degrades gracefully with age instead of falling off a cliff at
  compaction time.

Claude Code's built-in `/clear` command — and the idle-return prompt
that nudges you to `/clear` after 75+ minutes away — optimize for the
opposite end of the spectrum: short tasks, clean context, minimal
carry-over. That's a
valid approach for isolated work. But for ongoing projects where "why
did we do X three weeks ago" matters, where preferences and decisions
compound over time, and where the cost of lost context is measured in
re-explained requirements and repeated mistakes — that approach falls
short.

If you use Claude Code as a long-running partner on evolving projects,
Alzheimer is built for you. If you prefer short, isolated sessions,
Alzheimer still works (it keeps your memory tree healthy regardless),
but features like historical memory won't have much to work with.

## Bug reporting

If something goes wrong, tell Claude:

> **"Diagnose my memory"**

Claude will run a structured diagnostic and show you what's wrong. If
you'd like to report the issue, tell Claude:

> **"File an Alzheimer bug report"**

Claude will ask for your confirmation before filing. Reports are
privacy-safe: they include only aggregate counts and structural metrics
(line counts, entry counts, error types) — never personal memory content
or specific filenames (which can encode sensitive topics). Here's an
example of what a filed report looks like:

```markdown
## Anomaly Report

**Rebalancer version:** 0.1.0
**Python:** 3.14.3
**Platform:** Darwin 25.4.0

## Anomalies

- **error**: MEMORY.md exceeds hard limit: 210 lines (max 200)
- **error**: Broken reference: project_*.md
- **warning**: Orphaned memory file: feedback_*.md

## Tree Structure

MEMORY.md: 210 lines, 6200 bytes, 45 entries
  _index/: 3 index files, 82 total entries, 4500 bytes
```

Note how specific filenames are replaced with type prefixes
(`project_*.md`, `feedback_*.md`) so the report reveals the kind of
entry but not its topic.

If the rebalancer crashes during a hook run, it outputs a status message
suggesting you ask Claude to run a diagnosis. This surfaces in the UI
without interrupting your conversation.

## Testing

```bash
cd /path/to/alzheimer/
python3 -m unittest test_rebalance -v
```

178 tests covering:
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
- Inline content detection (skip rebalance, preserve data, threshold)
- Hook CLI output format (single JSON, additionalContext routing, unsupported-event fallback)
- Config file loading (.alzheimer.conf)
- Limit resolution priority (CLI > config > defaults)
- Glossary integration (staleness detection, parsing, pinning)
- Early rebalancing (young tree threshold)
- Bug report privacy (filename anonymization, no paths or content leaked)
- Drift detection (orphan auto-indexing, dry-run safety, oversized leaves, glossary exclusion)
- Update staleness check (cache roundtrip, expiry, no-git fallback)
- CLI alias (--check for --verify)
- Guardrails soft layer (staleness, parsing, pinning, system messages)
- Guardrails hard layer (block rules, custom rules, pattern matching)
- Guardrails confirm mode (self-allowlist, config manipulation, try/finally restoration)
- Reminders (tier 1 gating, date parsing, daily checks, recurring, missed reminders)

## Concurrency

Running multiple Claude instances in different directories is fine —
each gets its own memory tree. Running multiple instances in the **same
directory** is generally an anti-pattern (Claude Code itself has no
concurrency model for shared memory). Alzheimer makes this slightly
worse: the rebalancer does read-modify-write on `MEMORY.md` without file
locking, so simultaneous hook runs could clobber each other's writes.
In practice the risk is low (the rebalancer runs in under a second),
but if you need concurrent access, be aware of this limitation.

## License

MIT No Attribution (MIT-0)
