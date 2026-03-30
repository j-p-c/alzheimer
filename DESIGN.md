# Alzheimer: Self-Balancing Hierarchical Memory for Claude Code

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

### Design Principles

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

## Tree Conventions

### Index files vs leaf files

- **Index files** live in `_index/` subdirectory and contain only
  pointers to other files (no detailed content).
- **Leaf files** live in the memory root directory (current behavior).
- MEMORY.md is the root index.

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

## Rebalancing Algorithm

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

## Trigger Mechanism

The rebalancer is a Python script (`rebalance.py`) invoked by hooks:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '(.tool_input.file_path // .tool_response.filePath) // empty' | { read -r f; echo \"$f\" | grep -q '/memory/' && python3 /path/to/rebalance.py \"$(dirname \"$f\")\" 2>&1 | head -5; } || true"
      }]
    }]
  }
}
```

Use `python3 setup.py --install` to configure hooks with the correct
absolute path for your machine automatically.

## Size Budget

With 150-line indices and 3-entry minimum groups:

| Tree depth | Max leaf entries |
|------------|-----------------|
| 1          | 150             |
| 2          | ~7,500          |
| 3          | ~375,000        |

Depth 2 should suffice for any realistic Claude Code project.

## Key Terms Glossary

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

# Key Terms

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

`glossary_is_stale()` compares the mtime of `glossary.md` against all
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
2. Emits a warning instructing Claude to tell the user and offer to
   restructure the MEMORY.md into standard index format
3. Leaves the file completely untouched

A post-rebalance verification also checks whether the file is still over
limits after a successful rebalance (e.g., due to an oversized header).
If so, it warns with restructuring instructions.

## Early Rebalancing

Young trees (no `_index/` directory yet) are at higher risk of
overflowing before the first rebalance. When `is_young_tree()` detects
there is no `_index/` directory, the rebalancer triggers at 50% of the
normal threshold. This prevents a burst of new memories from pushing
MEMORY.md past the limit.

## Hook Mode (--hook)

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

## Reference Memory Seeding

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

## File Layout

```
alzheimer/
├── DESIGN.md          (this file)
├── README.md          (usage instructions)
├── rebalance.py       (core rebalancer script)
├── setup.py           (installer, updater, reference memory seeder)
└── test_rebalance.py  (tests)
```
