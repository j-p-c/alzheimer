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
├── rebalance.py       (core rebalancer script)
├── test_rebalance.py  (tests)
└── README.md          (usage instructions)
```
