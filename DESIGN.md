# Alzheimer: self-balancing hierarchical memory for Claude Code

## Problem

Claude Code's memory system uses a flat index file (MEMORY.md) capped at
200 lines / 25KB. When it overflows, entries at the bottom are silently
lost. Topic files exist but are only loaded on demand — if their pointer
in MEMORY.md is truncated, they become orphaned and effectively forgotten.

Auto Dream (Anthropic's consolidation agent) prunes MEMORY.md to stay
under the limit, but it maintains a flat structure. It does not create
hierarchy or push detail down into sub-indices.

## Solution

Transform the flat index into a self-balancing tree:

```
MEMORY.md                          (root index, ≤150 lines)
├── _index/projects.md             (category index, ≤150 lines)
│   ├── project_alzheimer.md       (leaf: detail file)
│   ├── project_website_details.md (leaf: detail file)
│   └── ...
├── _index/feedback.md             (category index, ≤150 lines)
│   ├── feedback_broken_links.md   (leaf: detail file)
│   ├── feedback_permissions.md    (leaf: detail file)
│   └── ...
├── _index/user.md                 (category index, ≤150 lines)
│   └── user_context.md            (leaf: detail file)
├── reference_directories.md       (leaf: too few to need a category)
└── ...
```

### Design principles

1. **Root stays small.** MEMORY.md must NEVER exceed 150 lines (headroom
   below the 200-line hard cap). When it would exceed this, entries are
   grouped into category indices and replaced with a single pointer.

2. **Detail pushes down.** Each category index follows the same format
   and same size limit as MEMORY.md. If a category grows too large, it
   spawns sub-categories. The tree grows in depth, not width.

3. **Summaries push up.** Each category index's pointer in its parent
   carries a one-line description (~150 chars) that gives Claude enough
   context to decide whether to read deeper.

4. **Self-balancing.** A rebalancer script runs automatically (via hooks)
   and restructures the tree when size thresholds are exceeded. No human
   intervention required.

5. **Recursive discipline.** The same size limit applies at every level.
   No file in the tree may exceed the configured line limit.

6. **Compatible with Auto Dream.** Leaf entries in MEMORY.md still use
   the standard one-line format. Category pointers are also one-line.
   Auto Dream can still prune/consolidate without breaking the tree.

7. **Graceful discovery.** If MEMORY.md is ever truncated despite our
   efforts, the `_index/` directory structure allows rediscovery by
   scanning the filesystem.

## Tree conventions

### Index files vs leaf files

- **Index files** live in `_index/` subdirectory and contain only
  pointers to other files (no detailed content).
- **Leaf files** live in the memory root directory (current behavior).
- `MEMORY.md` is the root index.

### Entry format (same at every level)

```markdown
- [Title](relative/path.md) — one-line description under 150 chars
```

### Category pointer format in MEMORY.md

```markdown
- [Projects (5)](_index/projects.md) — alzheimer, API gateway, auth service, billing integration, search service
```

The count in parentheses tells Claude how many entries are inside without
needing to read the file. The description summarizes the contents.

### Frontmatter for index files

```markdown
---
type: index
parent: MEMORY.md
children: 5
max_lines: 150
---
```

## Rebalancing algorithm

```
rebalance(index_file, max_lines=150):
    entries = parse(index_file)
    if lines(index_file) <= max_lines:
        return  # Nothing to do

    # Group leaf entries by memory type (from frontmatter)
    groups = group_by_type(entries)

    for type, group in groups:
        if len(group) >= 3:  # Minimum group size to justify a category
            child_index = create_or_update_index(type, group)
            replace_entries(index_file, group, category_pointer(child_index))

    # Recurse into child indices
    for child in child_indices(index_file):
        rebalance(child, max_lines)
```

### Grouping strategy

Level 1: Group by memory type (user, feedback, project, reference).
Level 2+: Group by topic prefix or semantic similarity.

Minimum group size: 3 entries. Smaller groups stay as leaf entries in
the parent index. This prevents over-fragmentation.

### When rebalancing runs

1. **After every memory write.** A PostToolUse hook on Write|Edit
   detects writes to the memory directory and triggers the rebalancer.
2. **At session start.** A SessionStart hook runs the rebalancer to
   clean up any drift from the previous session.
3. **Before compaction.** The existing PreCompact hook already saves
   unsaved context; we add a rebalance step.

## Trigger mechanism

The rebalancer is a Python script (`rebalance.py`) invoked by hooks:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '...' | { read -r f; ... && python3 /path/to/rebalance.py --hook --hook-event PostToolUse \"$(dirname \"$f\")\" 2>&1 | head -5; } || true"
      }]
    }]
  }
}
```

The actual hook commands are generated by `setup.py --install` with the
correct absolute path for your machine. The `--hook-event` flag tells
the rebalancer which hook triggered it, enabling event-specific behavior
(e.g., suppressing glossary updates on SessionStart).

## Size budget

With 150-line indices and 3-entry minimum groups:

| Tree depth | Max leaf entries |
|------------|-----------------|
| 1          | 150             |
| 2          | ~7,500          |
| 3          | ~375,000        |

Depth 2 should suffice for any realistic Claude Code project.

## Key terms glossary

Important terms (proper nouns, project names, people) get lost after
Claude Code conversation compaction because the rebalancer treats all
memory entries equally. The glossary fixes this by maintaining a pinned
summary of key terms at the top of MEMORY.md.

### How it works

A `glossary.md` file uses `type: glossary` in its frontmatter — a type
that is NOT in the rebalancer's `CATEGORY_LABELS`, so it is never pushed
into `_index/`. It stays pinned in the root MEMORY.md.

```markdown
---
type: glossary
updated: 2026-03-29
terms: 15
---

# Key terms

- **Project Alpha** — the main deployment target for billing services
- **Server Omega** — CI/CD build server in the staging environment
```

The MEMORY.md entry lists the terms inline for visibility even without
opening the file:

```markdown
- [Key Terms](glossary.md) — Project Alpha, Server Omega, Team Delta, ...
```

### Generation

The glossary is **written by Claude, not by Python code**. When the
rebalancer detects that `glossary.md` is stale (missing, or older than
any memory file), it emits a `systemMessage` instructing Claude to:

1. Read all memory files in the directory
2. Identify the 10-20 most important key terms
3. Write `glossary.md` with proper frontmatter and one-line definitions

This avoids the limitations of regex-based term extraction (noisy
results, wrong definitions, inability to understand context).

### Staleness detection

`glossary_is_stale()` compares the `mtime` of `glossary.md` against all
memory files. If any memory file is newer, the glossary is stale.

### Integration with rebalance()

Glossary processing runs BEFORE the `needs_rebalance` check. This means:
- The glossary is checked every run, not just when limits are exceeded
- Adding the glossary entry may push MEMORY.md over limits, correctly
  triggering a rebalance
- `GLOSSARY_MIN_TERMS = 3` — glossary is skipped for trivial trees
- **Glossary update messages are suppressed on SessionStart.** On machines
  with many project directories, emitting glossary instructions for every
  stale project at startup floods Claude with competing instructions that
  are unlikely to be acted on. Instead, glossary update instructions are
  only emitted on PostToolUse (after memory writes), when Claude is already
  acting on a specific project and there is only one instruction to follow.

### Inline content detection

MEMORY.md files that contain inline content (notes, commands, multi-line
blocks mixed between standard index entries) cannot be safely rebalanced.
The parser only recognizes `- [Title](file.md) — desc` entries; any other
non-blank lines after the header would be silently dropped by `write_index`.

When `count_inline_content()` finds such lines, the rebalancer:
1. Skips rebalancing entirely to avoid data loss
2. Leaves the file completely untouched
3. If the file is **over the limit**: emits an urgent warning instructing
   Claude to restructure the MEMORY.md into standard index format
4. If the file is **under the limit**: logs an informational note (no
   warning) — small amounts of inline content in a healthy file are not
   a data loss risk

A post-rebalance verification also checks whether the file is still over
limits after a successful rebalance (e.g., due to an oversized header).
If so, it warns with restructuring instructions.

## Early rebalancing

Young trees (no `_index/` directory yet) are at higher risk of
overflowing before the first rebalance. When `is_young_tree()` detects
there is no `_index/` directory, the rebalancer triggers at 50% of the
normal threshold. This prevents a burst of new memories from pushing
MEMORY.md past the limit.

## Drift detection

Problems that the rebalancer can't fix itself used to accumulate silently
between `--verify` runs. Now `check_drift()` runs on every invocation
(including no-op runs where MEMORY.md is within limits).

**Orphan auto-indexing:** When `check_drift()` finds memory files not
referenced by any index, it reads their frontmatter (`name` and
`description` fields) and automatically adds index entries to MEMORY.md.
This means Claude only needs to write the memory file — the rebalancer
handles the indexing. Orphans without valid frontmatter are reported as
warnings for Claude to handle manually.

**Oversized leaf detection:** Leaf files over 150 lines are reported as
warnings via `additionalContext` so Claude can trim or split them.

`check_drift()` is intentionally lightweight: one directory listing plus
entry comparison. It excludes `glossary.md` (managed by the rebalancer)
and files in `_index/` (already checked by `rebalance_index()`).

## Update staleness check

On SessionStart and PreCompact hooks, the rebalancer checks whether the
local Alzheimer repo is behind `origin/main`. If updates are available,
it appends `(update available)` to the user-visible status line and
sends instructions to Claude via `additionalContext`.

The check does a `git fetch` and `git rev-list HEAD..origin/main --count`,
but caches the result in `.alzheimer.lastcheck` (gitignored). The cache
expires after 24 hours, so network overhead is at most one fetch per day.
If the fetch fails (offline, no remote), the check is silently skipped.

The check runs on PreCompact (not just SessionStart) because long-lived
sessions may run for days without restarting.

## Hook mode (`--hook`)

When invoked with `--hook`, the rebalancer produces a single JSON object
on stdout. The optional `--hook-event` flag specifies which Claude Code
hook triggered the invocation (SessionStart, PostToolUse, PreCompact).

```json
{"systemMessage": "alzheimer: 27/150 lines, 3/20 KB — balanced"}
```

The `systemMessage` field is displayed to the user in the Claude Code UI.
When the rebalancer has additional context for Claude (glossary update
instructions, warning details), it uses `hookSpecificOutput.additionalContext`
— this reaches Claude's context without cluttering the user's terminal:

```json
{
  "systemMessage": "alzheimer: 27/150 lines, 3/20 KB — balanced",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "GLOSSARY UPDATE NEEDED: ..."
  }
}
```

This is how the rebalancer communicates:
- **Status** (user-visible via `systemMessage`): line count, byte count, whether rebalancing was needed
- **Glossary updates** (Claude-only via `additionalContext`): instructions to rewrite glossary.md
- **Warnings** (Claude-only via `additionalContext`): unresolvable issues (suggests running `--diagnose`)

Without `--hook`, output is plain text suitable for manual use.

## Reference memory seeding

On install or update, `setup.py` writes a `reference_alzheimer.md` file
into each project memory directory (`~/.claude/projects/*/memory/`).
This ensures every Claude instance knows:
- What "alzheimer" is
- How to update it (`setup.py --update`)
- How to diagnose issues (`rebalance.py --diagnose`)
- How to report bugs (`rebalance.py --report`)

The seeded file uses `type: reference` frontmatter and includes the
install path and version, so Claude can run commands without guessing
paths.

## Compatibility

- **Auto Dream**: Leaf entries use standard format. Auto Dream can still
  consolidate leaves. Category pointers look like normal entries.
  Auto Dream may try to flatten categories back — we detect and re-tree
  on the next rebalance pass.
- **Existing memories**: Migration is non-destructive. Leaf files don't
  move. Only MEMORY.md changes (entries replaced with category pointers).
- **Other Claude instances**: The tree structure degrades gracefully to
  the standard flat system. An instance that doesn't understand
  categories will see them as normal entries pointing to readable files.

## Guardrails (in development)

### Problem

Claude Code suffers from **permission drift**: after a user approves
several consecutive actions ("yes", "yes", "yes"), Claude begins to
assume future actions are also approved and stops asking. This is an
emergent behavioral pattern, not a deliberate feature — and it can
lead to destructive or irreversible actions being taken without
confirmation. This is arguably the most dangerous failure mode in
Claude's operational model, raising policy and compliance risk to
potentially unacceptable levels for many organizations.

Beyond permission drift, users need a way to express durable rules
("never push to main without asking", "never delete production
branches") that persist across conversations and survive compaction.
Currently, such rules can only be expressed in `CLAUDE.md` files or
memory entries — both of which are attention-based (Claude must notice
and follow them) with no mechanical enforcement.

### Solution: two-layer guardrails

Guardrails uses two complementary layers:

1. **Soft layer (attention-based):** A `guardrails.md` file pinned in
   `MEMORY.md`, similar to the glossary. Claude writes and maintains it.
   Contains user-stated rules in natural language.
2. **Hard layer (hook-based):** A `PreToolUse` hook that mechanically
   blocks dangerous operations by pattern-matching tool names and
   arguments. Returns non-zero to prevent execution — Claude cannot
   override this.

The soft layer catches nuanced, context-dependent rules. The hard
layer catches clear-cut violations that should never happen regardless
of context.

### Soft layer: guardrails.md

#### Format

```markdown
---
type: guardrails
updated: 2026-03-31
rules: 5
---

# Guardrails

- **Never push without asking** — always confirm before `git push`
  to any remote, every time, regardless of prior approvals
- **Never delete branches without asking** — confirm before
  `git branch -D` or `git push --delete`
- **No force-push** — `git push --force` is blocked; use
  `--force-with-lease` if absolutely necessary, and confirm first
- **No secrets in public repos** — never commit .env, credentials,
  or personal memory content to public repositories
- **Update docs with code** — when pushing code changes, review and
  update README/DESIGN docs in the same commit
```

#### Lifecycle

When the user says something like "NEVER do X without asking me" or
"always confirm before Y", Claude recognizes this as a guardrail and
adds it to `guardrails.md`. This is analogous to how Claude adds
memory entries, but guardrails are specifically about **behavioral
constraints**.

The rebalancer treats `type: guardrails` the same way it treats
`type: glossary` — it is never moved to `_index/`, always stays
pinned in the root `MEMORY.md`. Staleness detection works identically:
if the file's `mtime` is older than any memory file that references
behavioral rules, Claude is instructed to review and update it.

#### Relationship to memory

Some guardrails originate as feedback memories (e.g.,
`feedback_no_push_without_asking.md`). The guardrails file is a
**consolidated, authoritative list** — the single place Claude checks
before taking any action. Individual feedback memories provide the
*why* (context, history); `guardrails.md` provides the *what* (the
rule itself, in imperative form).

### Hard layer: PreToolUse hook

#### Mechanism

Claude Code's `PreToolUse` hook fires before every tool invocation.
The hook receives the tool name and input as JSON on stdin. If the
hook exits with a non-zero status, the tool call is **blocked** —
Claude cannot proceed with that action.

The guardrails hook (`guardrails.py`) pattern-matches against a
configurable set of rules:

```python
HARD_RULES = [
    {
        "tool": "Bash",
        "pattern": r"git\s+push\b",
        "action": "block",
        "message": "git push blocked by guardrails — ask the user first"
    },
    {
        "tool": "Bash",
        "pattern": r"git\s+push\s+.*--force\b",
        "action": "block",
        "message": "force-push blocked by guardrails"
    },
    {
        "tool": "Bash",
        "pattern": r"rm\s+-rf\s+/",
        "action": "block",
        "message": "recursive delete of root blocked by guardrails"
    },
]
```

#### Hook configuration

The hook is installed by `setup.py` alongside the existing rebalancer
hooks:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/guardrails.py"
      }]
    }]
  }
}
```

The hook reads tool input from stdin, checks it against `HARD_RULES`,
and either exits 0 (allow) or exits 1 with a JSON error message
(block). Blocked actions surface in Claude's context as hook failures,
prompting Claude to ask the user for explicit confirmation.

#### Configurable rules

Hard rules are loaded from a `.guardrails.conf` file (if present) in
the Alzheimer install directory, allowing users to customize which
operations are blocked. The default set covers the most dangerous
patterns identified in practice.

```json
{
  "rules": [
    {"tool": "Bash", "pattern": "git\\s+push\\b", "action": "block"},
    {"tool": "Bash", "pattern": "rm\\s+-rf", "action": "block"}
  ]
}
```

### Interaction between the layers

The two layers serve different purposes and do not overlap:

| | Soft (`guardrails.md`) | Hard (`PreToolUse` hook) |
|---|---|---|
| **Enforcement** | Attention-based (Claude reads) | Mechanical (hook blocks) |
| **Scope** | Nuanced, context-dependent rules | Clear-cut, always-block patterns |
| **Bypassable** | Yes (permission drift risk) | No (exits non-zero); confirm mode uses deterministic wrapper |
| **Examples** | "Update docs when pushing code" | Block `git push` without prior user message containing "push" |
| **Written by** | Claude | `setup.py` / config file |
| **Failure mode** | Claude forgets or deprioritizes | False positive (blocks legitimate action) |

The hard layer is the safety net for when the soft layer fails. The
soft layer handles everything the hard layer can't express as a regex.

### Integration with the rebalancer

- `type: guardrails` is added to the rebalancer's pinned types
  (alongside `type: glossary`), so it is never moved to `_index/`.
- Staleness detection follows the same pattern as glossary: compare
  `mtime` against memory files, emit update instructions when stale.
- The guardrails entry in `MEMORY.md` summarizes the active rules:

```markdown
- [Guardrails](guardrails.md) — no push without asking, no force-push, no secrets in public repos, ...
```

### Confirm mode: deterministic temporary allow

The two original action types — `"block"` (always reject) and the
implicit allow (no rule matches) — leave a gap. Many real guardrails
are neither "always block" nor "always allow" but **"block unless the
user has explicitly approved."** For example: "never run bash commands
without my permission."

A naive implementation would have the soft layer manage a
remove-rule → run-command → re-add-rule cycle. But this relies on
Claude faithfully performing three steps in sequence — exactly the
kind of behavioral promise that permission drift erodes. After several
approved commands, Claude might skip the re-add step.

The solution is to delegate the cycle to **deterministic Python code**:

```python
# guardrails.py --exec "git push origin main"
def exec_with_temporary_allow(command, rule):
    """Remove rule, run command, re-add rule. Guaranteed by try/finally."""
    remove_rule(rule)
    try:
        result = subprocess.run(command, shell=True, capture_output=True)
    finally:
        add_rule(rule)  # deterministic — not subject to permission drift
    return result
```

The flow for a `"confirm"` rule:

1. Hook fires, matches a `"confirm"` rule, **blocks** the command
2. Block message tells Claude: "this requires user confirmation"
3. Claude asks the user; user approves
4. Claude calls `guardrails.py --exec "<command>"` via the Bash tool
5. The hook recognizes `guardrails.py --exec` as a **self-whitelist**
   pattern and lets it through
6. Python removes the rule, runs the command, re-adds the rule in a
   `try/finally` — the re-add is guaranteed regardless of command
   success or failure
7. Command output is returned to Claude normally

The critical insight: Python is not subject to permission drift. The
safety-critical step (restoring the guardrail) is deterministic code,
not a behavioral promise. This gives the hard layer a third action
type:

| Action | Behavior |
|---|---|
| `"allow"` | No rule matched — tool call proceeds |
| `"block"` | Always rejected; user must edit config to remove rule |
| `"confirm"` | Rejected on first attempt; approved execution via Python wrapper with guaranteed rule restoration |

The self-whitelist is a simple pattern match: the hook checks whether
the Bash command is an invocation of `guardrails.py --exec` and skips
rule checking for that specific pattern. This is a narrow, predictable
exception — not a general bypass mechanism.

Broad `"confirm"` rules (e.g., matching all Bash commands) are an edge
case but must be handled gracefully. A user might set one intentionally
or accidentally. The self-whitelist ensures the system remains
functional: the only Bash command that bypasses the rule is the
guardrails wrapper itself.

### Guardrails as behavioral nudges

An unexpected property: guardrails don't just prevent bad actions, they
redirect toward better ones. When a soft guardrail says "never run bash
without asking," the friction of needing permission causes Claude to
pause and reconsider whether bash is even necessary — often discovering
that a dedicated tool (Edit, Grep, Read) is the better choice. This
mirrors the human experience of "are you sure?" dialogs: the
interruption itself improves decision-making, independent of the
user's answer.

This means guardrails have two modes of action:

1. **Blocking:** preventing a dangerous operation (the intended effect)
2. **Redirecting:** nudging toward better tool selection (an emergent
   side effect)

The redirecting effect is especially valuable because it is
self-reinforcing: Claude learns to reach for the right tool first,
reducing the frequency of guardrail activations over time.

### Open questions (guardrails)

- **Self-protection:** The hard layer can't protect itself from being
  edited — Claude can use the Edit tool to modify `guardrails.py` or
  `.guardrails.conf`. The soft layer (`guardrails.md`) must include a
  rule like "never edit guardrails files without explicit user
  permission." This is the two layers reinforcing each other: neither
  is sufficient alone.
- **Imperative detection:** When a user says "NEVER do X without
  permission," the system should recognize this as a hard-block
  imperative and write the rule to `.guardrails.conf` automatically,
  not just save it as a soft memory. The boundary between soft
  (behavioral) and hard (mechanical) guardrails needs a clear
  heuristic.
- **Rule discovery:** Should Claude proactively suggest guardrails
  when it observes risky patterns, or only add them when the user
  explicitly states a rule?
- **Per-project rules:** Some guardrails are global (never push
  without asking), others are project-specific (this repo is public,
  no secrets). How to handle the distinction.

## Historical memory (in development)

### Problem

Claude Code's conversation compaction is a cliff-edge: when the context
window fills, older messages are summarized by a model call that the user
and Alzheimer have no control over. The summary is lossy — it tends to
preserve the "main work thread" but drop side conversations, active
discussion topics, stated preferences, and reasoning context. Each
successive compaction compounds the loss.

Meanwhile, full conversation transcripts are preserved on disk as JSONL
files in `~/.claude/projects/<project-path>/`. This data never expires,
but it's never used — Claude doesn't read old transcripts unless
explicitly asked.

### Insight

Two key capabilities make a workaround possible:

1. **Claude can read transcript files** — they're regular JSONL on the
   local filesystem.
2. **Processing files injects content into context** — the act of
   reading and summarizing a file leaves the summary in Claude's working
   context.

These two facts mean we can build a structured, persistent conversation
history that Claude maintains incrementally — **before** the compaction
cliff is reached.

### Biological analogy

The algorithm below closely parallels **sleep-dependent memory
consolidation** in neuroscience. During sleep, the hippocampus
replays recent experiences and progressively transfers them into
neocortical long-term storage at decreasing resolution — recent
memories are vivid and detailed, older memories are compressed into
gist and schema. This is a logarithmic process: last night's events
are replayed in near-full fidelity, last month's are abstracted into
broader patterns.

Claude Code's built-in Auto Dream feature takes its name from this
analogy, but its implementation is closer to someone panic-cleaning a
desk by throwing things in drawers — a periodic bulk operation that
prunes and flattens. Historical memory aims for actual consolidation
with structure preservation: incremental, continuous, and with
resolution that degrades gracefully with age rather than falling off
a cliff.

### Solution: log-structured merge summarization

Borrow the merge strategy from LSM trees (Log-Structured Merge trees,
O'Neil et al. 1996) to build a logarithmically-compressed history of
all conversations.

The core algorithm:

1. Divide conversation transcripts into fixed-size **chunks** of C bytes
   (e.g., C = 100 KB).
2. **Summarize** each chunk into a markdown file of approximately S bytes
   (e.g., S = 50 KB). This is the fundamental summary task.
3. When two summaries exist at the **same level**, **merge** them into a
   single summary of the same target size S.
4. The active historical memory at any point is the set of **un-merged
   summaries** — at most $\log_2(n)$ files for n chunks.

### Binary counting correspondence

The algorithm mirrors binary arithmetic. The number of chunks processed,
written in binary, tells you exactly which summary levels are active.

| Chunks | Binary | Active summaries                              |
|--------|--------|-----------------------------------------------|
| 1      | 1      | summary-0                                     |
| 2      | 10     | summary-0-1                                   |
| 3      | 11     | summary-0-1, summary-2                        |
| 4      | 100    | summary-0-3                                   |
| 5      | 101    | summary-0-3, summary-4                        |
| 6      | 110    | summary-0-3, summary-4-5                      |
| 7      | 111    | summary-0-3, summary-4-5, summary-6           |
| 8      | 1000   | summary-0-7                                   |

Each merge doubles the volume of logs covered while keeping the file size
constant at ~S bytes. The result: recent history at high resolution,
older history at progressively lower resolution. This matches the actual
utility curve — what you discussed yesterday matters more than what you
discussed last month.

### File structure

```
~/.claude/projects/<project-path>/
├── memory/                    (existing Alzheimer tree)
│   ├── MEMORY.md
│   ├── glossary.md
│   └── ...
└── history/                   (historical memory)
    ├── state.json             (chunk counter, calibration data)
    ├── summary-0-7.md         (merged: chunks 0-7)
    ├── summary-8-11.md        (merged: chunks 8-11)
    ├── summary-12-13.md       (merged: chunks 12-13)
    └── summary-14.md          (chunk 14, not yet merged)
```

Summary files use a naming convention that encodes the chunk range:
`summary-{first}-{last}.md` for merged files, `summary-{n}.md` for
unmerged single-chunk files. This makes the merge tree structure
self-evident from a directory listing.

### Key properties

- **Logarithmic total size.** At most $\log_2(n)$ summary files, each ~S
  bytes. For 1000 chunks (~100 MB of conversation), that's ~10 files
  totaling ~500 KB.
- **Amortized O(1) merges per chunk.** Each chunk participates in at
  most $\log_2(n)$ merges over its entire lifetime, spread across future
  chunk arrivals. The expected merge cost per new chunk is constant.
- **Never re-process old data.** After chunk $2^k$ is processed, chunks
  $0$ through $2^k - 1$ are never read again. All their information is
  captured in `summary-0-{2^k-1}.md`.
- **Incremental injection.** Every merge operation requires Claude to
  read two summaries and produce a synthesis. This naturally injects
  historical context into the active conversation.
- **Persistent artifacts.** Summary files survive across sessions,
  compactions, and restarts. A new session can bootstrap historical
  awareness by reading the current top-level summaries.

### Initialization

For an existing Claude installation with extensive conversation history,
the full history must be processed once. For 81 MB of transcripts
(~810 chunks at C = 100 KB), this is a significant but one-time cost.

Initialization walks **backwards** through conversation history (newest
first). This means:
- The most valuable (recent) history is summarized first
- The process can be interrupted at any point and still yield a useful
  partial result — recent summaries are complete, older history simply
  isn't indexed yet
- Merges are performed as they become available during the backward walk

For new installations, initialization is essentially free — there's no
history to process yet.

### Steady-state operation

After initialization, historical memory updates incrementally:

1. A hook monitors total JSONL transcript size (a single `stat()` call,
   essentially free).
2. When the accumulated new data since the last summary crosses the
   chunk threshold C, a new summarization is triggered.
3. Claude summarizes the new chunk into `summary-{n}.md`.
4. If the chunk count triggers a merge (i.e., a power-of-two boundary
   in the binary representation changes), the merge cascade runs.

The hook can run on any event (PostToolUse, SessionStart, PreCompact).
Only the threshold check is frequent; actual summarization only happens
when a new chunk boundary is crossed.

### Session start / post-compaction bootstrap

On a cold start (new session or after compaction), Claude has no
historical context beyond what `MEMORY.md` provides. The SessionStart
hook loads the top-level summary pointers from `state.json` and
instructs Claude to read them.

For 810 chunks, the active set is at most $\log_2(810) \approx 10$ summary files.
At ~50 KB each, that's ~500 KB of historical context injected on startup
— covering the entire conversation history at varying resolution.

### Context window budget

A portion of the context window is allocated to historical memory.
The exact budget depends on the model's window size, which is not
directly observable at runtime.

**Empirical calibration:** A calibration routine fills context with
known amounts of summary text and observes when compaction triggers.
This directly measures the effective window size without needing to
know the theoretical number. The calibration result is cached in
`state.json` keyed by model name. If the model changes between
sessions (e.g., switching from Sonnet to Opus), the cached result is
invalid and calibration re-runs automatically.

The target summary size S and chunk size C can then be tuned so that
the full summary tree fits within budget while maximizing coverage
and resolution.

### Relationship to existing memory

Historical memory and the existing Alzheimer memory tree serve
different purposes:

| | Memory tree (`MEMORY.md`) | Historical memory |
|---|---|---|
| **Contains** | Facts, preferences, decisions | Narrative context, discussion flow |
| **Retention** | Indefinite, full fidelity | Logarithmic decay with age |
| **Written by** | Claude (explicit saves) | Automated summarization |
| **Updated** | On each memory write | On chunk threshold |
| **Survives** | Across all sessions | Across all sessions |

The two systems complement each other. Memory files capture *what was
decided*. Historical memory captures *how we got there* — the reasoning,
the side conversations, the exploratory discussions that inform future
work but don't warrant a permanent memory entry.

### Open questions (historical memory)

- **Subagent log weaving.** Conversation directories contain subagent
  transcripts in subdirectories. These need to be woven into the main
  timeline (by wall-clock timestamp) for coherent summarization.
- **Summarization quality.** The fundamental summary task (JSONL →
  markdown) needs careful prompt engineering. It must preserve active
  threads, stated preferences, and reasoning — exactly the things that
  standard compaction drops.
- **Cross-conversation boundaries.** Transcript files represent separate
  sessions. Summaries should note session boundaries but not treat each
  session as fully independent — ongoing work spans sessions.
- **Background processing.** Both initialization and steady-state merges
  consume context. Large merge cascades (e.g., at chunk 512) may benefit
  from running as background agents to avoid displacing the user's active
  work.

## Post-mortem (in development)

### Problem

When something goes wrong in a Claude Code collaboration — a promised
action never happened, a decision was made for unclear reasons, or a
chain of delegated tasks broke silently — there is no structured way
to investigate. The user can scroll through their terminal, but
compaction may have destroyed the relevant context. The raw JSONL
conversation logs contain everything, but they are large, interleaved
with subagent transcripts, and not human-readable.

The result: accountability gaps. "What did we agree to do?" and "Where
did the chain break?" become unanswerable questions once the
conversation context is gone.

### Solution: structured incident review

Post-mortem provides a structured process for investigating past
conversations. It builds on historical memory's infrastructure —
summary files for fast scanning, raw JSONL logs for precise
reconstruction — and adds a focused investigation workflow.

### How it works

1. **Start from historical memory.** The LSM-tree summaries provide a
   timeline of all past conversations at varying resolution. Claude
   scans these to locate the relevant time period and narrow down which
   sessions contain the incident.

2. **Drill into raw logs.** Once the relevant sessions are identified,
   Claude reads the JSONL transcript files directly. These contain
   every message, tool call, and tool result — a complete record of
   what happened.

3. **Weave subagent timelines.** Conversation directories contain
   subagent transcripts in subdirectories. The post-mortem process
   interleaves these by wall-clock timestamp to reconstruct the full
   picture, including work that was delegated to background agents.

4. **Produce a structured report.** The output is a timeline showing:
   - What was discussed and decided
   - What actions were promised
   - What actions were actually taken (tool calls)
   - Where the chain broke (promised but not done, or done incorrectly)
   - Root cause analysis (compaction loss, permission drift, missed
     handoff, etc.)

### Invocation

The user asks Claude to investigate:

> **"Do a post-mortem on why the payment branches got merged together"**
> **"What happened with the scheduled job we set up last week?"**
> **"When did we decide to change the API format, and why?"**

Claude uses the historical memory summaries to find the right time
window, then drills into raw logs as needed. No special syntax or
commands required — just a natural-language request.

### Relationship to other features

| Feature | Role in post-mortem |
|---|---|
| **Historical memory** | Fast timeline scanning — locate the incident |
| **Raw JSONL logs** | Precise reconstruction — exact messages and tool calls |
| **Memory files** | Cross-reference — what was saved vs. what was discussed |
| **Guardrails** | Prevention — post-mortems may identify new guardrail rules |

Post-mortem is a read-only operation: it investigates but does not
modify any files. However, its findings may lead to new guardrails
("we should block X without confirmation") or new memory entries
("save the decision about Y so it survives compaction").

### Open questions (post-mortem)

- **Privacy boundaries.** Post-mortem reads raw conversation logs,
  which may contain sensitive content. Should there be scoping
  controls ("only look at conversations about topic X")?
- **Cross-instance investigation.** If the incident spans Personal
  Claude and Work Claude (different machines, different logs), the
  post-mortem can only see one side. How to handle this?
- **Efficiency.** Raw JSONL logs are large. For an 81 MB history,
  reading the relevant sessions directly may consume significant
  context. Historical memory summaries help narrow the search, but
  the drill-down step needs careful budgeting.

## File layout

```
alzheimer/
├── DESIGN.md          (this file)
├── README.md          (usage instructions)
├── rebalance.py       (core rebalancer script)
├── setup.py           (installer, updater, reference memory seeder)
└── test_rebalance.py  (tests)
```
