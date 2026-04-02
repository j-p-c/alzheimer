#!/usr/bin/env python3
"""
rebalance.py — Self-balancing hierarchical memory tree for Claude Code.

Maintains a tree of index files rooted at MEMORY.md, ensuring no index
exceeds a configured line limit. When an index grows too large, entries
are grouped by memory type and pushed into child index files.

Usage:
    python3 rebalance.py /path/to/memory/directory [--max-lines N] [--dry-run]

The script is designed to be called from Claude Code hooks (PostToolUse,
SessionStart, PreCompact) but can also be run manually.
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import traceback

VERSION = "0.7.1"
REPO_OWNER = "j-p-c"
REPO_NAME = "alzheimer"


# ── Constants ──────────────────────────────────────────────────────────

# Hard limits imposed by Claude Code (as of March 2026).
# These are defined in Claude Code's system prompt, not in any config
# file we can read. Update here if Anthropic changes them.
HARD_MAX_LINES = 200
HARD_MAX_BYTES = 25600    # 25KB

# Soft limits — what the rebalancer triggers on (headroom below hard cap).
DEFAULT_MAX_LINES = 150
DEFAULT_MAX_BYTES = 20480 # 20KB

MAX_DEPTH = 5             # Safety limit on tree depth
MIN_GROUP_SIZE = 3        # Don't create a category for fewer entries
INDEX_DIR = "_index"      # Subdirectory for category index files
GLOSSARY_FILE = "glossary.md"
GLOSSARY_MAX_TERMS = 20
GLOSSARY_MIN_TERMS = 3    # Don't create glossary with fewer terms
GUARDRAILS_FILE = "guardrails.md"

# Config file name (placed in memory directory to override defaults).
CONFIG_FILE = ".alzheimer.conf"

# Category display names and sort order.
CATEGORY_ORDER = ["user", "reference", "project", "feedback"]
CATEGORY_LABELS = {
    "user":      "User",
    "reference": "Reference",
    "project":   "Projects",
    "feedback":  "Feedback",
}

# Stop words excluded from keyword extraction for topic grouping.
STOP_WORDS = frozenset(
    "a an and are as at be by do for from has have how i if in is it "
    "its not of on or so the this to use was we what when with you "
    "all also any but can don dont each etc get got may need no only "
    "out set should that them then they too very will".split()
)



# ── Configuration ──────────────────────────────────────────────────────

def load_config(memory_dir):
    """Load overrides from .alzheimer.conf in the memory directory.

    Format: simple key=value, one per line. Lines starting with # are
    comments. Recognized keys:
        hard_max_lines, hard_max_bytes, max_lines, max_bytes,
        max_depth, min_group_size

    Returns a dict of overrides (only keys present in the file).
    """
    config = {}
    conf_path = os.path.join(memory_dir, CONFIG_FILE)
    if not os.path.exists(conf_path):
        return config
    try:
        with open(conf_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key in ("hard_max_lines", "hard_max_bytes",
                               "max_lines", "max_bytes",
                               "max_depth", "min_group_size"):
                        config[key] = int(val)
    except (OSError, ValueError):
        pass
    return config


def get_limits(memory_dir, cli_max_lines=None, cli_max_bytes=None):
    """Resolve limits from defaults, config file, and CLI overrides.

    Priority: CLI flags > config file > module defaults.
    Returns (max_lines, max_bytes, hard_max_lines, hard_max_bytes).
    """
    config = load_config(memory_dir)

    hard_lines = config.get("hard_max_lines", HARD_MAX_LINES)
    hard_bytes = config.get("hard_max_bytes", HARD_MAX_BYTES)
    soft_lines = cli_max_lines or config.get("max_lines", DEFAULT_MAX_LINES)
    soft_bytes = cli_max_bytes or config.get("max_bytes", DEFAULT_MAX_BYTES)

    return soft_lines, soft_bytes, hard_lines, hard_bytes


# ── Parsing helpers ────────────────────────────────────────────────────

ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)"
    r"\s*(?:—|--)\s*(?P<desc>.+)$"
)


def count_inline_content(filepath):
    """Count non-blank, non-entry lines after the header in an index file.

    These are lines that the parser cannot handle — inline notes, raw
    commands, multi-line content that should be in separate topic files.
    Returns (inline_count, total_lines).
    """
    inline_count = 0
    total_lines = 0
    in_header = True
    with open(filepath) as f:
        for line in f:
            total_lines += 1
            stripped = line.rstrip("\n")
            if ENTRY_RE.match(stripped):
                in_header = False
            elif in_header:
                pass
            elif stripped:
                inline_count += 1
    return inline_count, total_lines


def parse_index(filepath):
    """Parse an index file into (header_lines, entries).

    Returns:
        header: list of lines before the first entry (title, blanks).
        entries: list of dicts with keys: title, path, desc, raw.
    """
    header = []
    entries = []
    in_header = True

    with open(filepath) as f:
        for line in f:
            stripped = line.rstrip("\n")
            m = ENTRY_RE.match(stripped)
            if m:
                in_header = False
                entries.append({
                    "title": m.group("title"),
                    "path":  m.group("path"),
                    "desc":  m.group("desc").strip(),
                    "raw":   stripped,
                })
            elif in_header:
                header.append(stripped)
            # Non-entry lines after entries (blanks, comments) are kept
            # only if they're trailing blank lines — skip them for now.

    return header, entries


def read_frontmatter_type(filepath):
    """Read the 'type' field from a memory file's YAML frontmatter.

    Parses simple key: value frontmatter without requiring PyYAML.
    """
    try:
        with open(filepath) as f:
            first_line = f.readline().strip()
            if first_line != "---":
                return None
            for line in f:
                if line.strip() == "---":
                    break
                m = re.match(r"^type:\s*(.+)$", line.strip())
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return None


def read_all_frontmatter(filepath):
    """Read all key:value pairs from a memory file's YAML frontmatter.

    Returns a dict (empty if no frontmatter or file unreadable).
    """
    result = {}
    try:
        with open(filepath) as f:
            first_line = f.readline().strip()
            if first_line != "---":
                return result
            for line in f:
                if line.strip() == "---":
                    break
                m = re.match(r"^(\w[\w-]*):\s*(.+)$", line.strip())
                if m:
                    result[m.group(1)] = m.group(2).strip().strip('"\'')
    except OSError:
        pass
    return result


def file_size_bytes(filepath):
    """Return file size in bytes, or 0 if missing."""
    try:
        return os.path.getsize(filepath)
    except OSError:
        return 0


def exceeds_limits(filepath, header, entries, max_lines, max_bytes):
    """Check if an index file exceeds line or byte limits."""
    total_lines = len(header) + len(entries) + 1  # +1 trailing newline
    total_bytes = file_size_bytes(filepath)
    return (total_lines > max_lines or
            (max_bytes and total_bytes > max_bytes))


def is_category_entry(entry):
    """Check if an entry points to an index file (at any depth)."""
    path = entry["path"]
    # Entries starting with "../" are leaves pointing to the parent dir.
    if path.startswith("../"):
        return False
    # Category entries point into _index/ or into subdirectories of it.
    return path.startswith(INDEX_DIR + "/") or "/" in path


def is_sub_index_pointer(entry):
    """Check if an entry points to a sub-index (within a category dir)."""
    # Sub-index pointers look like "feedback/broken.md" (relative to
    # the parent index in _index/).
    path = entry["path"]
    return "/" in path and not path.startswith("../")


# ── Tree operations ────────────────────────────────────────────────────

def count_leaves_in_index(memory_dir, index_path):
    """Count leaf (non-index) entries in a category index file."""
    fullpath = os.path.join(memory_dir, index_path)
    if not os.path.exists(fullpath):
        return 0
    _, entries = parse_index(fullpath)
    return len([e for e in entries if not is_category_entry(e)])


def group_entries_by_type(memory_dir, entries):
    """Group leaf entries by the 'type' field in their target file."""
    groups = {}
    ungrouped = []

    for entry in entries:
        if is_category_entry(entry):
            # Don't regroup existing category pointers.
            ungrouped.append(entry)
            continue

        target = os.path.join(memory_dir, entry["path"])
        mem_type = read_frontmatter_type(target)

        if mem_type and mem_type in CATEGORY_LABELS:
            groups.setdefault(mem_type, []).append(entry)
        else:
            ungrouped.append(entry)

    return groups, ungrouped


def build_category_index(memory_dir, category, entries):
    """Create or update a category index file in _index/.

    Returns the relative path to the category index.
    """
    index_dir = os.path.join(memory_dir, INDEX_DIR)
    os.makedirs(index_dir, exist_ok=True)

    index_path = os.path.join(INDEX_DIR, f"{category}.md")
    full_index_path = os.path.join(memory_dir, index_path)

    # If the index already exists, merge entries (don't duplicate).
    existing_paths = set()
    existing_entries = []
    if os.path.exists(full_index_path):
        _, existing_entries = parse_index(full_index_path)
        existing_paths = {e["path"] for e in existing_entries}

    # Adjust relative paths: entries in MEMORY.md use "file.md" but
    # entries in _index/category.md need "../file.md".
    merged = list(existing_entries)
    for entry in entries:
        # Path from _index/ to the leaf file.
        child_path = "../" + entry["path"] if not entry["path"].startswith("../") else entry["path"]
        if child_path not in existing_paths:
            merged.append({
                "title": entry["title"],
                "path":  child_path,
                "desc":  entry["desc"],
                "raw":   f"- [{entry['title']}]({child_path}) — {entry['desc']}",
            })
            existing_paths.add(child_path)

    label = CATEGORY_LABELS.get(category, category.title())

    lines = [
        "---",
        "type: index",
        f"parent: MEMORY.md",
        f"category: {category}",
        f"children: {len(merged)}",
        f"max_lines: {DEFAULT_MAX_LINES}",
        "---",
        "",
        f"# {label}",
        "",
    ]
    for entry in merged:
        lines.append(entry["raw"])
    lines.append("")  # Trailing newline.

    with open(full_index_path, "w") as f:
        f.write("\n".join(lines))

    return index_path


def summarize_entries(entries, max_len=120):
    """Build a concise summary from entry titles.

    Uses titles (shorter than descriptions) and joins with ", ".
    Truncates at last comma before max_len for clean breaks.
    """
    titles = [e["title"] for e in entries]
    summary = ", ".join(titles)
    if len(summary) <= max_len:
        return summary
    truncated = summary[:max_len]
    last_comma = truncated.rfind(", ")
    if last_comma > 0:
        return truncated[:last_comma] + ", ..."
    return truncated[:max_len - 3] + "..."


def build_category_pointer(memory_dir, category, index_path, entries):
    """Build a one-line MEMORY.md entry pointing to a category index."""
    label = CATEGORY_LABELS.get(category, category.title())
    count = count_leaves_in_index(memory_dir, index_path)
    summary = summarize_entries(entries)
    return f"- [{label} ({count})]({index_path}) — {summary}"


def write_index(filepath, header, entries):
    """Write an index file from header lines and entry dicts."""
    lines = list(header)
    # Ensure blank line between header and entries.
    if lines and lines[-1] != "":
        lines.append("")
    for entry in entries:
        lines.append(entry["raw"])
    lines.append("")  # Trailing newline.

    with open(filepath, "w") as f:
        f.write("\n".join(lines))


# ── Update staleness check ────────────────────────────────────────────

# How often to check for updates (seconds).  One fetch per day keeps
# network overhead negligible while catching staleness within 24 hours.
UPDATE_CHECK_INTERVAL = 86400  # 24 hours

# Cache file storing the last check timestamp and result.
UPDATE_CACHE_FILE = ".alzheimer.lastcheck"


def _alzheimer_dir():
    """Return the directory containing this script (the alzheimer repo)."""
    return os.path.dirname(os.path.abspath(__file__))


def _read_update_cache(cache_path):
    """Read the update cache file.  Returns (timestamp, behind_count) or
    (0, 0) if the cache is missing or unreadable."""
    try:
        with open(cache_path) as f:
            data = json.load(f)
        return data.get("timestamp", 0), data.get("behind", 0)
    except (OSError, json.JSONDecodeError, ValueError):
        return 0, 0


def _write_update_cache(cache_path, behind):
    """Write the update cache file."""
    import time
    try:
        with open(cache_path, "w") as f:
            json.dump({"timestamp": time.time(), "behind": behind}, f)
    except OSError:
        pass


def check_for_updates(alzheimer_dir=None, force=False):
    """Check if the local alzheimer repo is behind origin/main.

    Returns (behind_count, message) where behind_count is the number of
    commits behind, and message is a human-readable string (or None if
    up to date or check was skipped).

    Uses a cache file to avoid fetching more than once per
    UPDATE_CHECK_INTERVAL seconds.  Pass force=True to ignore the cache.
    """
    import time

    if alzheimer_dir is None:
        alzheimer_dir = _alzheimer_dir()

    # Is this a git repo?
    git_dir = os.path.join(alzheimer_dir, ".git")
    if not os.path.isdir(git_dir):
        return 0, None

    cache_path = os.path.join(alzheimer_dir, UPDATE_CACHE_FILE)
    cached_ts, cached_behind = _read_update_cache(cache_path)

    now = time.time()
    if not force and (now - cached_ts) < UPDATE_CHECK_INTERVAL:
        # Cache is fresh — return cached result without fetching.
        if cached_behind > 0:
            return cached_behind, (
                f"alzheimer update available: {cached_behind} new "
                f"commit(s) on origin/main. Tell the user and offer "
                f"to run the update."
            )
        return 0, None

    # Fetch from origin (quiet, timeout-safe).
    try:
        fetch = subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=alzheimer_dir,
            capture_output=True, text=True, timeout=10
        )
        if fetch.returncode != 0:
            # Fetch failed (offline, no remote, etc.) — skip silently.
            _write_update_cache(cache_path, 0)
            return 0, None

        # Count commits we're behind.
        rev_list = subprocess.run(
            ["git", "rev-list", "HEAD..origin/main", "--count"],
            cwd=alzheimer_dir,
            capture_output=True, text=True, timeout=5
        )
        behind = int(rev_list.stdout.strip()) if rev_list.returncode == 0 else 0
    except (subprocess.TimeoutExpired, OSError, ValueError):
        _write_update_cache(cache_path, 0)
        return 0, None

    _write_update_cache(cache_path, behind)

    if behind > 0:
        return behind, (
            f"alzheimer update available: {behind} new commit(s) on "
            f"origin/main. Tell the user and offer to run the update."
        )
    return 0, None


# ── Drift detection ───────────────────────────────────────────────────

# Leaf files over this many lines trigger a warning.  Individual memory
# files shouldn't be enormous — they become hard to maintain and may
# indicate content that should be split or trimmed.
LEAF_MAX_LINES = 150


def check_drift(memory_dir, max_lines=DEFAULT_MAX_LINES, dry_run=False):
    """Check for orphaned files and oversized leaf files.

    Runs after every rebalance (including no-op runs) to catch problems
    that accumulate between --verify runs.

    Orphans with valid frontmatter (name + description) are auto-indexed
    in MEMORY.md.  Orphans without frontmatter are reported as warnings
    for Claude to handle manually.

    Returns (actions, warnings) where actions are auto-fix descriptions
    and warnings are unresolvable issues for additionalContext.
    """
    actions = []
    warnings = []

    # Orphan check (exclude glossary — it's managed by the rebalancer).
    pinned = {GLOSSARY_FILE, GUARDRAILS_FILE}
    orphans = [o for o in find_orphans(memory_dir) if o not in pinned]
    if orphans:
        memory_md = os.path.join(memory_dir, "MEMORY.md")
        header, entries = parse_index(memory_md)
        auto_indexed = []
        manual_needed = []

        for orphan in sorted(orphans):
            filepath = os.path.join(memory_dir, orphan)
            fm = read_all_frontmatter(filepath)
            name = fm.get("name", "")
            desc = fm.get("description", "")
            if name and desc:
                # Auto-index: construct entry from frontmatter.
                entry = {
                    "title": name,
                    "path": orphan,
                    "desc": desc,
                    "raw": f"- [{name}]({orphan}) — {desc}",
                }
                if not dry_run:
                    entries.append(entry)
                auto_indexed.append(orphan)
            else:
                manual_needed.append(orphan)

        if auto_indexed:
            if not dry_run:
                write_index(memory_md, header, entries)
            names = ", ".join(auto_indexed[:10])
            more = (f" (+{len(auto_indexed) - 10} more)"
                    if len(auto_indexed) > 10 else "")
            actions.append(
                f"Auto-indexed {len(auto_indexed)} orphaned file(s): "
                f"{names}{more}"
            )

        if manual_needed:
            orphan_list = ", ".join(manual_needed[:10])
            more = (f" (+{len(manual_needed) - 10} more)"
                    if len(manual_needed) > 10 else "")
            warnings.append(
                f"DRIFT: {len(manual_needed)} orphaned memory file(s) "
                f"without frontmatter: {orphan_list}{more}. "
                f"These files have no name/description metadata, so "
                f"the rebalancer cannot auto-index them. "
                f"IMPORTANT: Read each file, then add a proper "
                f"one-line index entry to MEMORY.md: "
                f"- [Title](filename.md) — short description"
            )

    # Oversized leaf file check.
    skip = {"MEMORY.md", GLOSSARY_FILE, GUARDRAILS_FILE}
    oversized = []
    for name in sorted(os.listdir(memory_dir)):
        if name in skip or name.startswith("_") or not name.endswith(".md"):
            continue
        filepath = os.path.join(memory_dir, name)
        try:
            with open(filepath) as fh:
                line_count = sum(1 for _ in fh)
        except OSError:
            continue
        if line_count > LEAF_MAX_LINES:
            oversized.append((name, line_count))

    if oversized:
        details = ", ".join(
            f"{name} ({lines} lines)" for name, lines in oversized[:5]
        )
        more = (f" (+{len(oversized) - 5} more)"
                if len(oversized) > 5 else "")
        warnings.append(
            f"DRIFT: {len(oversized)} oversized memory file(s): "
            f"{details}{more}. "
            f"These files waste context window space when loaded, "
            f"causing more frequent compactions and potential context "
            f"loss. Read each file, archive completed/historical "
            f"sections into separate files with frontmatter, and keep "
            f"only active/current content in the original."
        )

    return actions, warnings


# ── Main rebalance logic ──────────────────────────────────────────────

def rebalance(memory_dir, max_lines=DEFAULT_MAX_LINES,
              max_bytes=DEFAULT_MAX_BYTES, dry_run=False,
              hook_event=None):
    """Rebalance the memory tree rooted at MEMORY.md.

    Returns (actions, warnings, messages) where:
        actions:  list of action descriptions (for logging / dry-run)
        warnings: list of unresolvable issues (max depth, too few to split)
        messages: list of systemMessage strings for Claude (e.g. glossary update)
    """
    actions = []
    warnings = []
    messages = []
    memory_md = os.path.join(memory_dir, "MEMORY.md")

    if not os.path.exists(memory_md):
        return ["MEMORY.md not found — nothing to do."], [], []

    header, entries = parse_index(memory_md)

    # Check for inline content that the rebalancer can't handle.
    inline_count, actual_lines = count_inline_content(memory_md)
    if inline_count > 0:
        total_bytes = file_size_bytes(memory_md)
        over_limit = actual_lines > max_lines or (
            max_bytes and total_bytes > max_bytes
        )
        if over_limit:
            # File needs rebalancing but has inline content — urgent.
            warnings.append(
                f"MEMORY.md has {inline_count} lines of inline content "
                f"(out of {actual_lines} total) that are not standard "
                f"index entries. The rebalancer cannot compress or move "
                f"inline content and will skip rebalancing to avoid data "
                f"loss. IMPORTANT: Tell the user about this problem "
                f"immediately. If MEMORY.md exceeds 200 lines, Claude "
                f"Code will silently truncate it, losing memories. Ask "
                f"the user whether they would like you to restructure "
                f"the MEMORY.md: move each piece of inline content into "
                f"a separate .md file with frontmatter (name, "
                f"description, type), and replace it with a one-line "
                f"index entry in the format: "
                f"- [Title](filename.md) — short description"
            )
        else:
            # File is under limits — inline content is not urgent.
            actions.append(
                f"MEMORY.md has {inline_count} lines of inline content "
                f"but is within limits ({actual_lines} lines). "
                f"Skipping rebalance (inline content not movable)."
            )
        # Still run glossary and guardrails updates, but skip the rebalance.
        emit_glossary = hook_event != "SessionStart"
        glossary_actions, glossary_entry, glossary_messages = update_glossary(
            memory_dir, dry_run, emit_messages=emit_glossary
        )
        actions.extend(glossary_actions)
        messages.extend(glossary_messages)
        guardrails_actions, guardrails_entry, guardrails_messages = (
            update_guardrails(memory_dir, dry_run, emit_messages=emit_glossary)
        )
        actions.extend(guardrails_actions)
        messages.extend(guardrails_messages)
        # Check for drift (orphans, oversized leaf files).
        drift_actions, drift_warnings = check_drift(
            memory_dir, max_lines, dry_run)
        actions.extend(drift_actions)
        warnings.extend(drift_warnings)
        return actions, warnings, messages

    # Update glossary (always runs, even when tree is within limits).
    # Suppress glossary messages on SessionStart to avoid flooding
    # Claude with stale instructions across many project directories.
    emit_glossary = hook_event != "SessionStart"
    glossary_actions, glossary_entry, glossary_messages = update_glossary(
        memory_dir, dry_run, emit_messages=emit_glossary
    )
    actions.extend(glossary_actions)
    messages.extend(glossary_messages)
    if glossary_entry:
        has_glossary = any(e["path"] == GLOSSARY_FILE for e in entries)
        if not has_glossary:
            entries.insert(0, glossary_entry)
            if not dry_run:
                write_index(memory_md, header, entries)
        else:
            # Update existing glossary entry description (terms may change).
            for i, e in enumerate(entries):
                if e["path"] == GLOSSARY_FILE:
                    if e["raw"] != glossary_entry["raw"]:
                        entries[i] = glossary_entry
                        if not dry_run:
                            write_index(memory_md, header, entries)
                    break

    # Update guardrails (same pattern as glossary — pinned, never moved).
    guardrails_actions, guardrails_entry, guardrails_messages = (
        update_guardrails(memory_dir, dry_run, emit_messages=emit_glossary)
    )
    actions.extend(guardrails_actions)
    messages.extend(guardrails_messages)
    if guardrails_entry:
        has_guardrails = any(e["path"] == GUARDRAILS_FILE for e in entries)
        if not has_guardrails:
            # Insert after glossary (position 1) if glossary exists,
            # else at position 0.
            insert_pos = 1 if any(
                e["path"] == GLOSSARY_FILE for e in entries
            ) else 0
            entries.insert(insert_pos, guardrails_entry)
            if not dry_run:
                write_index(memory_md, header, entries)
        else:
            # Update existing guardrails entry description.
            for i, e in enumerate(entries):
                if e["path"] == GUARDRAILS_FILE:
                    if e["raw"] != guardrails_entry["raw"]:
                        entries[i] = guardrails_entry
                        if not dry_run:
                            write_index(memory_md, header, entries)
                    break

    total_lines = len(header) + len(entries) + 1  # +1 for trailing newline
    total_bytes = file_size_bytes(memory_md)

    # Young trees (no _index/ yet) rebalance at 50% of normal threshold
    # to build structure early, before the first compaction wipes context.
    index_dir = os.path.join(memory_dir, INDEX_DIR)
    is_young = not os.path.isdir(index_dir)
    if is_young:
        early_lines = max(max_lines // 2, 10)
        early_bytes = max(max_bytes // 2, 5120) if max_bytes else 0
        needs_rebalance = exceeds_limits(
            memory_md, header, entries, early_lines, early_bytes
        )
        if needs_rebalance:
            actions.append("Young tree — triggering early rebalance.")
    else:
        needs_rebalance = exceeds_limits(
            memory_md, header, entries, max_lines, max_bytes
        )

    if not needs_rebalance:
        size_info = f"{total_lines} lines, {total_bytes} bytes"
        actions.append(
            f"MEMORY.md is {size_info} (limit {max_lines} lines / "
            f"{max_bytes} bytes) — no rebalancing needed."
        )
        # Still check existing category indices for recursive balancing.
        for entry in entries:
            if is_category_entry(entry):
                child_path = os.path.join(memory_dir, entry["path"])
                if os.path.exists(child_path):
                    sub_actions, sub_warns = rebalance_index(
                        memory_dir, entry["path"], max_lines,
                        max_bytes, dry_run
                    )
                    actions.extend(sub_actions)
                    warnings.extend(sub_warns)
        # Check for drift (orphans, oversized leaf files).
        drift_actions, drift_warnings = check_drift(
            memory_dir, max_lines, dry_run)
        actions.extend(drift_actions)
        warnings.extend(drift_warnings)
        return actions, warnings, messages

    size_info = f"{total_lines} lines, {total_bytes} bytes"
    actions.append(
        f"MEMORY.md is {size_info} (limit {max_lines} lines / "
        f"{max_bytes} bytes) — rebalancing..."
    )

    # Group leaf entries by type.
    groups, ungrouped = group_entries_by_type(memory_dir, entries)

    new_entries = list(ungrouped)

    for category in CATEGORY_ORDER:
        if category not in groups:
            continue
        group = groups[category]
        if len(group) < MIN_GROUP_SIZE:
            # Too few entries — keep them in MEMORY.md.
            new_entries.extend(group)
            actions.append(
                f"  {category}: {len(group)} entries (below minimum "
                f"{MIN_GROUP_SIZE}) — kept in MEMORY.md."
            )
            continue

        # Build category index.
        if not dry_run:
            index_path = build_category_index(
                memory_dir, category, group
            )
            pointer = build_category_pointer(
                memory_dir, category, index_path, group
            )
            new_entries.append({
                "title": CATEGORY_LABELS[category],
                "path":  index_path,
                "desc":  "",
                "raw":   pointer,
            })
        actions.append(
            f"  {category}: {len(group)} entries → {INDEX_DIR}/{category}.md"
        )

    # Handle any types not in CATEGORY_ORDER.
    for category, group in groups.items():
        if category in CATEGORY_ORDER:
            continue
        if len(group) < MIN_GROUP_SIZE:
            new_entries.extend(group)
        elif not dry_run:
            index_path = build_category_index(
                memory_dir, category, group
            )
            pointer = build_category_pointer(
                memory_dir, category, index_path, group
            )
            new_entries.append({
                "title": category.title(),
                "path":  index_path,
                "desc":  "",
                "raw":   pointer,
            })
            actions.append(
                f"  {category}: {len(group)} entries → "
                f"{INDEX_DIR}/{category}.md"
            )

    # Write updated MEMORY.md.
    if not dry_run:
        write_index(memory_md, header, new_entries)

    new_total = len(header) + len(new_entries) + 1
    actions.append(
        f"MEMORY.md: {total_lines} → {new_total} lines."
    )

    # Verify post-rebalance state.
    if not dry_run and new_total > max_lines:
        warnings.append(
            f"MEMORY.md is still over the {max_lines}-line limit "
            f"after rebalancing ({new_total} lines). "
            f"IMPORTANT: Tell the user about this problem immediately. "
            f"If MEMORY.md exceeds 200 lines, Claude Code will silently "
            f"truncate it, losing memories. This usually means the "
            f"header section is too large. Ask the user whether they "
            f"would like you to restructure it: move content from the "
            f"header into individual topic files with one-line index "
            f"entries."
        )

    # Recursively rebalance child indices.
    for entry in new_entries:
        if is_category_entry(entry):
            child_path = os.path.join(memory_dir, entry["path"])
            if os.path.exists(child_path):
                sub_actions, sub_warns = rebalance_index(
                    memory_dir, entry["path"], max_lines,
                    max_bytes, dry_run
                )
                actions.extend(sub_actions)
                warnings.extend(sub_warns)

    # Check for drift (orphans, oversized leaf files).
    drift_actions, drift_warnings = check_drift(
        memory_dir, max_lines, dry_run)
    actions.extend(drift_actions)
    warnings.extend(drift_warnings)

    return actions, warnings, messages


def extract_keywords(text):
    """Extract significant keywords from a description string."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    return [w for w in words if w not in STOP_WORDS]


def collect_memory_files(memory_dir):
    """Return list of leaf memory file paths (excluding indices, glossary, guardrails)."""
    skip = {"MEMORY.md", GLOSSARY_FILE, GUARDRAILS_FILE}
    files = []
    for name in os.listdir(memory_dir):
        if name in skip or name.startswith("_") or not name.endswith(".md"):
            continue
        files.append(os.path.join(memory_dir, name))
    return files


def glossary_is_stale(memory_dir):
    """Check if glossary.md is missing or older than any memory file."""
    glossary_path = os.path.join(memory_dir, GLOSSARY_FILE)
    if not os.path.exists(glossary_path):
        return True
    try:
        glossary_mtime = os.path.getmtime(glossary_path)
    except OSError:
        return True
    for filepath in collect_memory_files(memory_dir):
        try:
            if os.path.getmtime(filepath) > glossary_mtime:
                return True
        except OSError:
            continue
    return False


def parse_glossary(memory_dir):
    """Parse existing glossary.md, return list of term names."""
    glossary_path = os.path.join(memory_dir, GLOSSARY_FILE)
    if not os.path.exists(glossary_path):
        return []
    terms = []
    try:
        with open(glossary_path) as f:
            for line in f:
                m = re.match(r"^- \*\*(.+?)\*\*\s*(?:—|--)\s*", line)
                if m:
                    terms.append(m.group(1))
    except OSError:
        pass
    return terms


def glossary_system_message(memory_dir):
    """Build a systemMessage instructing Claude to write the glossary."""
    files = collect_memory_files(memory_dir)
    glossary_path = os.path.join(memory_dir, GLOSSARY_FILE)
    file_list = ", ".join(os.path.basename(f) for f in sorted(files))
    return (
        f"GLOSSARY UPDATE NEEDED: The key-terms glossary at "
        f"{glossary_path} is stale or missing. "
        f"Before responding to the user, silently update it:\n"
        f"1. Read all memory files in {memory_dir} "
        f"({file_list})\n"
        f"2. Identify the 10-20 most important key terms — proper nouns, "
        f"project names, people, tools, concepts that would help a new "
        f"Claude instance understand this user's context.\n"
        f"3. Write {glossary_path} using this exact format:\n"
        f"---\n"
        f"type: glossary\n"
        f"updated: YYYY-MM-DD\n"
        f"terms: N\n"
        f"---\n\n"
        f"# Key Terms\n\n"
        f"- **Term** — one-line definition\n"
        f"- **Term** — one-line definition\n\n"
        f"Focus on terms the user uses frequently or has flagged as "
        f"important. Exclude generic words, code identifiers, and "
        f"common English terms. Keep definitions concise (under 80 chars)."
    )


def build_glossary_entry(terms):
    """Build the MEMORY.md entry line for the glossary.

    Args:
        terms: list of term name strings.
    """
    term_list = ", ".join(terms)
    if len(term_list) > 120:
        truncated = term_list[:120]
        last_comma = truncated.rfind(", ")
        if last_comma > 0:
            term_list = truncated[:last_comma] + ", ..."
        else:
            term_list = truncated[:117] + "..."
    raw = f"- [Key Terms]({GLOSSARY_FILE}) — {term_list}"
    return {
        "title": "Key Terms",
        "path": GLOSSARY_FILE,
        "desc": term_list,
        "raw": raw,
    }


def update_glossary(memory_dir, dry_run=False, emit_messages=True):
    """Check glossary freshness, return (actions, entry_or_None, messages).

    If glossary is stale, emits a systemMessage for Claude to rewrite it.
    If glossary exists and is fresh, parses it for the MEMORY.md entry.

    Args:
        memory_dir: path to the memory directory.
        dry_run: if True, don't write anything.
        emit_messages: if False, suppress glossary update messages
            (used on SessionStart to avoid flooding Claude with
            stale glossary instructions across many projects).

    Returns:
        actions: list of log strings
        entry: MEMORY.md entry dict, or None
        messages: list of systemMessage strings for Claude
    """
    actions = []
    messages = []
    glossary_path = os.path.join(memory_dir, GLOSSARY_FILE)

    if glossary_is_stale(memory_dir):
        files = collect_memory_files(memory_dir)
        if len(files) < GLOSSARY_MIN_TERMS:
            actions.append(
                f"Glossary: {len(files)} memory files "
                f"(need {GLOSSARY_MIN_TERMS}) — skipped."
            )
            return actions, None, messages

        if not dry_run and emit_messages:
            messages.append(glossary_system_message(memory_dir))
        actions.append("Glossary: stale or missing — requesting update.")

    # If glossary exists (possibly from a previous run), parse it
    # and build the MEMORY.md entry from its contents.
    terms = parse_glossary(memory_dir)
    if terms:
        entry = build_glossary_entry(terms)
        actions.append(
            f"Glossary: {len(terms)} terms in {GLOSSARY_FILE}."
        )
        return actions, entry, messages

    return actions, None, messages


# ── Guardrails (soft layer) ───────────────────────────────────────────

def guardrails_is_stale(memory_dir):
    """Check if guardrails.md is missing or older than any feedback memory."""
    guardrails_path = os.path.join(memory_dir, GUARDRAILS_FILE)
    if not os.path.exists(guardrails_path):
        # No guardrails file yet — only stale if there are feedback memories
        # with behavioral rules.  For now, we don't create guardrails
        # automatically; the user must express a rule first.
        return False
    try:
        guardrails_mtime = os.path.getmtime(guardrails_path)
    except OSError:
        return False
    # Check if any feedback memory is newer than guardrails.md.
    for filepath in collect_memory_files(memory_dir):
        basename = os.path.basename(filepath)
        if not basename.startswith("feedback_"):
            continue
        try:
            if os.path.getmtime(filepath) > guardrails_mtime:
                return True
        except OSError:
            continue
    return False


def parse_guardrails(memory_dir):
    """Parse existing guardrails.md, return list of rule summaries."""
    guardrails_path = os.path.join(memory_dir, GUARDRAILS_FILE)
    if not os.path.exists(guardrails_path):
        return []
    rules = []
    try:
        with open(guardrails_path) as f:
            for line in f:
                m = re.match(r"^- \*\*(.+?)\*\*\s*(?:—|--)\s*", line)
                if m:
                    rules.append(m.group(1))
    except OSError:
        pass
    return rules


def guardrails_system_message(memory_dir):
    """Build a systemMessage instructing Claude to update guardrails.md."""
    guardrails_path = os.path.join(memory_dir, GUARDRAILS_FILE)
    feedback_files = [
        os.path.basename(f)
        for f in collect_memory_files(memory_dir)
        if os.path.basename(f).startswith("feedback_")
    ]
    file_list = ", ".join(sorted(feedback_files))
    return (
        f"GUARDRAILS UPDATE NEEDED: The guardrails file at "
        f"{guardrails_path} is stale — newer feedback memories exist. "
        f"Before responding to the user, silently update it:\n"
        f"1. Read all feedback memory files in the memory directory "
        f"({file_list})\n"
        f"2. Identify all behavioral rules — constraints, prohibitions, "
        f"and required confirmations.\n"
        f"3. Write {guardrails_path} using this exact format:\n"
        f"---\n"
        f"type: guardrails\n"
        f"updated: YYYY-MM-DD\n"
        f"rules: N\n"
        f"---\n\n"
        f"# Guardrails\n\n"
        f"- **Rule name** — imperative description of the constraint\n"
        f"- **Rule name** — imperative description of the constraint\n\n"
        f"Focus on rules about actions Claude must NEVER take without "
        f"explicit user confirmation. Each rule should be actionable "
        f"and unambiguous."
    )


def build_guardrails_entry(rules):
    """Build the MEMORY.md entry line for guardrails.

    Args:
        rules: list of rule name strings.
    """
    rule_list = ", ".join(r.lower() for r in rules)
    if len(rule_list) > 120:
        truncated = rule_list[:120]
        last_comma = truncated.rfind(", ")
        if last_comma > 0:
            rule_list = truncated[:last_comma] + ", ..."
        else:
            rule_list = truncated[:117] + "..."
    raw = f"- [Guardrails]({GUARDRAILS_FILE}) — {rule_list}"
    return {
        "title": "Guardrails",
        "path": GUARDRAILS_FILE,
        "desc": rule_list,
        "raw": raw,
    }


def update_guardrails(memory_dir, dry_run=False, emit_messages=True):
    """Check guardrails freshness, return (actions, entry_or_None, messages).

    If guardrails.md is stale, emits a systemMessage for Claude to rewrite it.
    If guardrails.md exists and is fresh, parses it for the MEMORY.md entry.

    Returns:
        actions: list of log strings
        entry: MEMORY.md entry dict, or None
        messages: list of systemMessage strings for Claude
    """
    actions = []
    messages = []
    guardrails_path = os.path.join(memory_dir, GUARDRAILS_FILE)

    if guardrails_is_stale(memory_dir):
        if not dry_run and emit_messages:
            messages.append(guardrails_system_message(memory_dir))
        actions.append("Guardrails: stale — requesting update.")

    # If guardrails.md exists, parse it and build the MEMORY.md entry.
    rules = parse_guardrails(memory_dir)
    if rules:
        entry = build_guardrails_entry(rules)
        actions.append(
            f"Guardrails: {len(rules)} rules in {GUARDRAILS_FILE}."
        )
        return actions, entry, messages

    return actions, None, messages


def group_entries_by_keyword(entries):
    """Group entries by their most common shared keyword.

    Strategy: find the keyword that appears in the most entries (but
    not ALL entries — that wouldn't help split). Group entries sharing
    that keyword, then recurse on the remainder.
    """
    if len(entries) <= MIN_GROUP_SIZE:
        return {"_ungrouped": entries}

    # Count keyword frequency across entries.
    keyword_to_entries = {}
    for entry in entries:
        keywords = set(extract_keywords(entry["desc"]))
        # Also extract from title.
        keywords.update(extract_keywords(entry["title"]))
        for kw in keywords:
            keyword_to_entries.setdefault(kw, []).append(entry)

    # Find the best splitting keyword: appears in >= MIN_GROUP_SIZE
    # entries but not in all of them (that wouldn't help).
    best_kw = None
    best_score = 0
    n = len(entries)
    for kw, kw_entries in keyword_to_entries.items():
        count = len(kw_entries)
        if count < MIN_GROUP_SIZE or count >= n:
            continue
        # Score: prefer keywords that split roughly in half.
        balance = min(count, n - count)
        if balance > best_score:
            best_score = balance
            best_kw = kw

    if best_kw is None:
        # No good split found — fall back to even halves.
        mid = len(entries) // 2
        return {
            "group-a": entries[:mid],
            "group-b": entries[mid:],
        }

    # Split on the best keyword.
    matched = []
    rest = []
    matched_set = set(id(e) for e in keyword_to_entries[best_kw])
    for entry in entries:
        if id(entry) in matched_set:
            matched.append(entry)
        else:
            rest.append(entry)

    groups = {best_kw: matched}
    if len(rest) >= MIN_GROUP_SIZE:
        # Name the rest group by ITS most common keyword.
        rest_kw_counts = {}
        for entry in rest:
            for kw in set(extract_keywords(entry["desc"])
                          + extract_keywords(entry["title"])):
                if kw != best_kw:
                    rest_kw_counts[kw] = rest_kw_counts.get(kw, 0) + 1
        if rest_kw_counts:
            rest_name = max(rest_kw_counts, key=rest_kw_counts.get)
        else:
            rest_name = "other"
        groups[rest_name] = rest
    else:
        groups.setdefault("_ungrouped", []).extend(rest)

    return groups


def build_sub_index(memory_dir, parent_rel_path, group_name, entries,
                    max_lines):
    """Create a sub-index under an existing category index.

    For _index/feedback.md, creates _index/feedback/topic.md.
    Returns the relative path (from memory_dir) to the sub-index.
    """
    parent_dir = os.path.dirname(parent_rel_path)
    parent_stem = os.path.splitext(os.path.basename(parent_rel_path))[0]
    sub_dir_rel = os.path.join(parent_dir, parent_stem)
    sub_dir_abs = os.path.join(memory_dir, sub_dir_rel)
    os.makedirs(sub_dir_abs, exist_ok=True)

    safe_name = re.sub(r"[^a-z0-9_-]", "_", group_name.lower())
    sub_index_rel = os.path.join(sub_dir_rel, f"{safe_name}.md")
    sub_index_abs = os.path.join(memory_dir, sub_index_rel)

    # Compute relative paths from sub-index to leaf files.
    # Entries coming from _index/foo.md have paths like "../leaf.md".
    # From _index/foo/topic.md, we need "../../leaf.md".
    adjusted = []
    for entry in entries:
        p = entry["path"]
        if p.startswith("../"):
            adj_path = "../" + p  # One more level up.
        else:
            adj_path = "../../" + p
        adjusted.append({
            "title": entry["title"],
            "path":  adj_path,
            "desc":  entry["desc"],
            "raw":   f"- [{entry['title']}]({adj_path}) — {entry['desc']}",
        })

    # Relative path from sub-index back to parent.
    parent_from_sub = "../" + os.path.basename(parent_rel_path)

    lines = [
        "---",
        "type: index",
        f"parent: {parent_from_sub}",
        f"topic: {group_name}",
        f"children: {len(adjusted)}",
        f"max_lines: {max_lines}",
        "---",
        "",
        f"# {group_name.title()}",
        "",
    ]
    for entry in adjusted:
        lines.append(entry["raw"])
    lines.append("")

    with open(sub_index_abs, "w") as f:
        f.write("\n".join(lines))

    return sub_index_rel


def resolve_child_path(parent_rel_path, child_entry_path):
    """Resolve a child entry's path to be relative to memory_dir.

    parent_rel_path: e.g. "_index/feedback.md"
    child_entry_path: e.g. "feedback/broken.md" (relative to parent)
    Returns: e.g. "_index/feedback/broken.md" (relative to memory_dir)
    """
    parent_dir = os.path.dirname(parent_rel_path)
    return os.path.normpath(os.path.join(parent_dir, child_entry_path))


def rebalance_index(memory_dir, rel_path, max_lines, max_bytes,
                    dry_run, depth=0):
    """Rebalance a child index file (recursive at arbitrary depth).

    At level 2+, groups entries by shared keywords extracted from
    their titles and descriptions.

    Returns (actions, warnings).
    """
    actions = []
    warnings = []
    full_path = os.path.join(memory_dir, rel_path)
    indent = "  " * (depth + 1)

    if not os.path.exists(full_path):
        return actions, warnings

    if depth >= MAX_DEPTH:
        actions.append(f"{indent}{rel_path}: max depth {MAX_DEPTH} reached.")
        warnings.append(
            f"{rel_path}: max tree depth ({MAX_DEPTH}) reached — "
            f"file may exceed size limits"
        )
        return actions, warnings

    header, entries = parse_index(full_path)
    total_lines = len(header) + len(entries) + 1

    if not exceeds_limits(full_path, header, entries, max_lines, max_bytes):
        return [f"{indent}{rel_path}: {total_lines} lines — OK."], warnings

    # Separate leaf entries (point to ../) from sub-index pointers.
    leaves = [e for e in entries if e["path"].startswith("../")]
    sub_pointers = [e for e in entries if not e["path"].startswith("../")]

    if len(leaves) < MIN_GROUP_SIZE * 2:
        warnings.append(
            f"{rel_path}: {total_lines} lines but only {len(leaves)} "
            f"leaves — too few to split further"
        )
        return ([f"{indent}{rel_path}: {total_lines} lines — "
                 f"only {len(leaves)} leaves, keeping flat."], warnings)

    actions.append(
        f"{indent}{rel_path}: {total_lines} lines — "
        f"splitting {len(leaves)} entries by topic..."
    )

    groups = group_entries_by_keyword(leaves)

    new_entries = list(sub_pointers)

    for group_name, group in sorted(groups.items()):
        if group_name == "_ungrouped" or len(group) < MIN_GROUP_SIZE:
            new_entries.extend(group)
            continue

        if not dry_run:
            sub_path = build_sub_index(
                memory_dir, rel_path, group_name, group, max_lines
            )
            count = len(group)
            summary = summarize_entries(group, max_len=100)

            # Relative path from this index to the sub-index.
            parent_stem = os.path.splitext(
                os.path.basename(rel_path)
            )[0]
            child_rel = f"{parent_stem}/{os.path.basename(sub_path)}"

            pointer = (f"- [{group_name.title()} ({count})]"
                       f"({child_rel}) — {summary}")
            new_entries.append({
                "title": group_name.title(),
                "path":  child_rel,
                "desc":  summary,
                "raw":   pointer,
            })

        actions.append(
            f"{indent}  {group_name}: {len(group)} entries → sub-index"
        )

    if not dry_run:
        write_index(full_path, header, new_entries)

    new_total = len(header) + len(new_entries) + 1
    actions.append(
        f"{indent}{rel_path}: {total_lines} → {new_total} lines."
    )

    # Recurse into sub-indices we just created (or pre-existing ones).
    for entry in new_entries:
        if not entry["path"].startswith("../"):
            child_abs_rel = resolve_child_path(rel_path, entry["path"])
            child_full = os.path.join(memory_dir, child_abs_rel)
            if os.path.exists(child_full):
                sub_actions, sub_warns = rebalance_index(
                    memory_dir, child_abs_rel, max_lines,
                    max_bytes, dry_run, depth + 1
                )
                actions.extend(sub_actions)
                warnings.extend(sub_warns)

    return actions, warnings


# ── Orphan detection ──────────────────────────────────────────────────

def find_orphans(memory_dir):
    """Find memory files not referenced by any index in the tree.

    Walks the entire _index/ tree recursively to collect all references.
    """
    referenced = set()

    def collect_refs(index_path):
        """Collect all leaf paths referenced by an index file."""
        if not os.path.exists(index_path):
            return
        _, entries = parse_index(index_path)
        index_rel_dir = os.path.relpath(
            os.path.dirname(index_path), memory_dir
        )
        for e in entries:
            resolved = os.path.normpath(
                os.path.join(index_rel_dir, e["path"])
            )
            referenced.add(resolved)
            # Only recurse into sub-indices (files inside _index/),
            # not leaf files (which may contain markdown links that
            # would be false-positive references).
            full = os.path.join(memory_dir, resolved)
            is_subindex = resolved.startswith("_index/") or (
                resolved.startswith("_index" + os.sep))
            if os.path.exists(full) and is_subindex:
                collect_refs(full)

    # Start from MEMORY.md.
    memory_md = os.path.join(memory_dir, "MEMORY.md")
    if os.path.exists(memory_md):
        collect_refs(memory_md)

    # Find all leaf .md files not in the reference set.
    orphans = []
    for fname in os.listdir(memory_dir):
        if fname == "MEMORY.md":
            continue
        if fname.endswith(".md"):
            if fname not in referenced:
                orphans.append(fname)

    return orphans


# ── Anomaly detection ─────────────────────────────────────────────────

class Anomaly:
    """Represents a detected anomaly during rebalancing."""

    def __init__(self, severity, message, context=None):
        self.severity = severity   # "error" or "warning"
        self.message = message
        self.context = context or {}

    def __repr__(self):
        return f"Anomaly({self.severity}: {self.message})"


def collect_anomalies(memory_dir, max_lines=DEFAULT_MAX_LINES,
                      max_bytes=DEFAULT_MAX_BYTES,
                      hard_max_lines=HARD_MAX_LINES,
                      hard_max_bytes=HARD_MAX_BYTES):
    """Run a diagnostic pass and collect all anomalies.

    Unlike verify_tree (which prints), this returns structured data
    suitable for bug reporting.
    """
    anomalies = []
    memory_md = os.path.join(memory_dir, "MEMORY.md")

    if not os.path.exists(memory_md):
        anomalies.append(Anomaly("error", "MEMORY.md not found"))
        return anomalies

    header, entries = parse_index(memory_md)
    total_lines = len(header) + len(entries) + 1
    total_bytes = file_size_bytes(memory_md)

    if total_lines > hard_max_lines:
        anomalies.append(Anomaly(
            "error",
            f"MEMORY.md exceeds hard limit: {total_lines} lines "
            f"(max {hard_max_lines})",
            {"lines": total_lines, "limit": hard_max_lines}
        ))
    elif total_lines > max_lines:
        anomalies.append(Anomaly(
            "warning",
            f"MEMORY.md exceeds soft limit: {total_lines} lines "
            f"(max {max_lines})",
            {"lines": total_lines, "limit": max_lines}
        ))

    if total_bytes > hard_max_bytes:
        anomalies.append(Anomaly(
            "error",
            f"MEMORY.md exceeds hard limit: {total_bytes} bytes "
            f"(max {hard_max_bytes})",
            {"bytes": total_bytes, "limit": hard_max_bytes}
        ))

    # Check broken references.
    def check_refs(index_path):
        if not os.path.exists(index_path):
            return
        idx_dir = os.path.dirname(index_path)
        _, idx_entries = parse_index(index_path)
        for e in idx_entries:
            target = os.path.normpath(os.path.join(idx_dir, e["path"]))
            if not os.path.exists(target):
                anomalies.append(Anomaly(
                    "error",
                    f"Broken reference: {e['path']}",
                    {"source": os.path.relpath(index_path, memory_dir),
                     "target": e["path"]}
                ))
            # Only recurse into sub-indices (inside _index/).
            else:
                rel = os.path.relpath(target, memory_dir)
                if rel.startswith("_index/") or rel.startswith(
                        "_index" + os.sep):
                    check_refs(target)

    check_refs(memory_md)

    # Check orphans.
    orphans = find_orphans(memory_dir)
    for o in orphans:
        anomalies.append(Anomaly(
            "warning",
            f"Orphaned memory file: {o}",
            {"file": o}
        ))

    return anomalies


def _anonymize_anomaly(message):
    """Strip memory filenames from an anomaly message for public reports.

    Filenames can encode sensitive topics (e.g. project_secret_deal.md).
    Replaces specific filenames with type-based placeholders.
    """
    # Replace "some_name.md" with just the type prefix or "*.md"
    return re.sub(
        r'\b([a-z]+)_[a-zA-Z0-9_-]+\.md\b',
        r'\1_*.md',
        message
    )


def format_bug_report(anomalies, memory_dir, exception_info=None):
    """Format anomalies into a GitHub issue body.

    Privacy-safe: never includes personal memory content or specific
    filenames (which can encode sensitive topics). Only includes
    aggregate counts, type prefixes, and structural metrics.
    """
    lines = [
        "## Anomaly Report",
        "",
        f"**Rebalancer version:** {VERSION}",
        f"**Python:** {platform.python_version()}",
        f"**Platform:** {platform.system()} {platform.release()}",
        "",
    ]

    if exception_info:
        lines.extend([
            "## Exception",
            "",
            "```",
            exception_info,
            "```",
            "",
        ])

    lines.extend([
        "## Anomalies",
        "",
    ])

    for a in anomalies:
        lines.append(f"- **{a.severity}**: {_anonymize_anomaly(a.message)}")

    # Tree structure snapshot — aggregate counts only, no filenames.
    lines.extend([
        "",
        "## Tree Structure",
        "",
        "```",
    ])
    memory_md = os.path.join(memory_dir, "MEMORY.md")
    if os.path.exists(memory_md):
        h, e = parse_index(memory_md)
        lines.append(f"MEMORY.md: {len(h) + len(e) + 1} lines, "
                     f"{file_size_bytes(memory_md)} bytes, "
                     f"{len(e)} entries")
        index_dir = os.path.join(memory_dir, "_index")
        if os.path.isdir(index_dir):
            index_count = 0
            total_entries = 0
            total_bytes = 0
            for root, dirs, files in os.walk(index_dir):
                for f in sorted(files):
                    if f.endswith(".md"):
                        fpath = os.path.join(root, f)
                        _, fe = parse_index(fpath)
                        index_count += 1
                        total_entries += len(fe)
                        total_bytes += file_size_bytes(fpath)
            lines.append(
                f"  _index/: {index_count} index files, "
                f"{total_entries} total entries, "
                f"{total_bytes} bytes"
            )
    lines.extend([
        "```",
        "",
        "---",
        f"*Filed automatically by alzheimer v{VERSION}*",
    ])

    return "\n".join(lines)


def file_github_issue(title, body):
    """File a GitHub issue using the gh CLI. Returns the issue URL or
    None if gh is not available or the user declines."""
    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--repo", f"{REPO_OWNER}/{REPO_NAME}",
             "--title", title,
             "--body", body,
             "--label", "bug,automated"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ── Verification ──────────────────────────────────────────────────────

def verify_tree(memory_dir, max_lines=DEFAULT_MAX_LINES,
                max_bytes=DEFAULT_MAX_BYTES,
                hard_max_lines=HARD_MAX_LINES,
                hard_max_bytes=HARD_MAX_BYTES):
    """Verify the integrity of the memory tree.

    Checks for: orphans, broken references, size violations, and
    structural issues. Returns True if everything is OK.
    """
    ok = True
    memory_md = os.path.join(memory_dir, "MEMORY.md")

    if not os.path.exists(memory_md):
        print("FAIL: MEMORY.md not found.")
        return False

    # Check MEMORY.md size.
    header, entries = parse_index(memory_md)
    total_lines = len(header) + len(entries) + 1
    total_bytes = file_size_bytes(memory_md)
    if total_lines > hard_max_lines:
        print(f"FAIL: MEMORY.md is {total_lines} lines "
              f"(hard limit {hard_max_lines}).")
        ok = False
    elif total_lines > max_lines:
        print(f"WARN: MEMORY.md is {total_lines} lines "
              f"(soft limit {max_lines}).")
    if total_bytes > hard_max_bytes:
        print(f"FAIL: MEMORY.md is {total_bytes} bytes "
              f"(hard limit {hard_max_bytes}).")
        ok = False
    elif total_bytes > max_bytes:
        print(f"WARN: MEMORY.md is {total_bytes} bytes "
              f"(soft limit {max_bytes}).")
    print(f"MEMORY.md: {total_lines} lines, {total_bytes} bytes — "
          f"{'OK' if total_lines <= max_lines and total_bytes <= max_bytes else 'over limit'}.")

    # Check for broken references.
    broken = []

    def check_refs(index_path, rel_prefix=""):
        idx_dir = os.path.dirname(index_path)
        _, idx_entries = parse_index(index_path)
        for e in idx_entries:
            target = os.path.normpath(os.path.join(idx_dir, e["path"]))
            if not os.path.exists(target):
                broken.append((os.path.relpath(index_path, memory_dir),
                                e["path"]))
            # Only recurse into sub-indices (inside _index/).
            else:
                rel = os.path.relpath(target, memory_dir)
                if rel.startswith("_index/") or rel.startswith(
                        "_index" + os.sep):
                    check_refs(target)

    check_refs(memory_md)
    if broken:
        print(f"FAIL: {len(broken)} broken reference(s):")
        for src, ref in broken:
            print(f"  {src} -> {ref}")
        ok = False
    else:
        print("References: all OK.")

    # Check orphans.
    orphans = find_orphans(memory_dir)
    if orphans:
        print(f"WARN: {len(orphans)} orphaned file(s):")
        for o in sorted(orphans):
            print(f"  {o}")
    else:
        print("Orphans: none.")

    # Check all index files in tree for size.
    def check_sizes(index_path):
        nonlocal ok
        h, e = parse_index(index_path)
        lines = len(h) + len(e) + 1
        nbytes = file_size_bytes(index_path)
        name = os.path.relpath(index_path, memory_dir)
        if lines > max_lines or nbytes > max_bytes:
            print(f"WARN: {name}: {lines} lines, {nbytes} bytes — "
                  f"over limit.")
        for entry in e:
            child = os.path.normpath(
                os.path.join(os.path.dirname(index_path),
                             entry["path"])
            )
            # Only recurse into sub-indices (inside _index/).
            if os.path.exists(child):
                rel = os.path.relpath(child, memory_dir)
                if rel.startswith("_index/") or rel.startswith(
                        "_index" + os.sep):
                    check_sizes(child)

    check_sizes(memory_md)

    if ok:
        print("\nAll checks passed.")
    else:
        print("\nSome checks failed.")
    return ok


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Rebalance Claude Code's hierarchical memory tree."
    )
    parser.add_argument(
        "memory_dir",
        help="Path to the memory directory containing MEMORY.md.",
    )
    parser.add_argument(
        "--max-lines", type=int, default=None,
        help=f"Maximum lines per index file (default: {DEFAULT_MAX_LINES}, "
             f"or from .alzheimer.conf).",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=None,
        help=f"Maximum bytes per index file (default: {DEFAULT_MAX_BYTES}, "
             f"or from .alzheimer.conf).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without modifying files.",
    )
    parser.add_argument(
        "--orphans", action="store_true",
        help="Report memory files not referenced by any index.",
    )
    parser.add_argument(
        "--verify", "--check", action="store_true",
        help="Verify tree integrity: check for orphans, broken refs, "
             "and size violations.",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="Collect anomalies and output a structured report.",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Collect anomalies and file a GitHub issue (requires gh CLI).",
    )
    parser.add_argument(
        "--hook", action="store_true",
        help="Output a brief JSON systemMessage (for use from hooks).",
    )
    parser.add_argument(
        "--hook-event", default=None,
        help="Hook event name (SessionStart, PostToolUse, PreCompact).",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"alzheimer {VERSION}",
    )
    args = parser.parse_args()

    memory_dir = os.path.abspath(args.memory_dir)

    if not os.path.isdir(memory_dir):
        print(f"Error: {memory_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Resolve limits: CLI flags > .alzheimer.conf > module defaults.
    max_lines, max_bytes, hard_lines, hard_bytes = get_limits(
        memory_dir, args.max_lines, args.max_bytes
    )

    if args.orphans:
        orphans = find_orphans(memory_dir)
        if orphans:
            print("Orphaned memory files (not in any index):")
            for o in sorted(orphans):
                print(f"  {o}")
        else:
            print("No orphans found.")
        return

    if args.verify:
        ok = verify_tree(memory_dir, max_lines, max_bytes,
                         hard_lines, hard_bytes)
        sys.exit(0 if ok else 1)

    if args.diagnose or args.report:
        anomalies = collect_anomalies(memory_dir, max_lines, max_bytes,
                                      hard_lines, hard_bytes)
        if not anomalies:
            print("No anomalies detected.")
            sys.exit(0)

        report = format_bug_report(anomalies, memory_dir)
        if args.diagnose:
            print(report)
            sys.exit(1 if any(a.severity == "error" for a in anomalies)
                     else 0)

        # --report: file a GitHub issue.
        errors = [a for a in anomalies if a.severity == "error"]
        if not errors:
            print("Only warnings found — not filing an issue.")
            print(report)
            sys.exit(0)

        title = f"[auto] {errors[0].message}"
        if len(errors) > 1:
            title += f" (+{len(errors) - 1} more)"
        url = file_github_issue(title, report)
        if url:
            print(f"Issue filed: {url}")
        else:
            print("Could not file issue (gh CLI not available or "
                  "not authenticated).")
            print("Report:")
            print(report)
        sys.exit(1)

    prefix = "[DRY RUN] " if args.dry_run else ""
    exception_info = None
    rebalancer_path = os.path.abspath(__file__)
    try:
        actions, warnings, messages = rebalance(
            memory_dir, max_lines, max_bytes, args.dry_run,
            hook_event=args.hook_event
        )
    except Exception:
        exception_info = traceback.format_exc()
        actions = [f"ERROR: rebalance failed: {exception_info}"]
        warnings = []
        messages = []

    if exception_info:
        # Rebalance crashed — tell Claude to investigate and report.
        anomalies = collect_anomalies(memory_dir, max_lines, max_bytes,
                                      hard_lines, hard_bytes)
        report = format_bug_report(anomalies, memory_dir, exception_info)
        print(json.dumps({
            "systemMessage": (
                f"The alzheimer memory rebalancer crashed. "
                f"Run `python3 {rebalancer_path} "
                f"{memory_dir} --diagnose` to investigate, "
                f"then tell the user what went wrong. If it looks "
                f"like a bug, ask the user if they'd like you to "
                f"file an issue by running `python3 {rebalancer_path} "
                f"{memory_dir} --report`."
            ),
        }))
        sys.exit(1)

    if args.hook:
        # Self-healing: ensure reference_alzheimer.md exists.  On fresh
        # installs the memory directory may not have existed at install
        # time, so the seed was missed.  One stat() per hook run; only
        # subprocesses when the file is actually missing.
        ref_file = os.path.join(memory_dir, "reference_alzheimer.md")
        if not os.path.exists(ref_file):
            setup_py = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "setup.py")
            if os.path.exists(setup_py):
                subprocess.run(
                    [sys.executable, setup_py, "--seed-one", memory_dir],
                    capture_output=True)

        # Build a brief summary for the user's UI.
        memory_md = os.path.join(memory_dir, "MEMORY.md")
        if os.path.exists(memory_md):
            with open(memory_md) as fh:
                line_count = sum(1 for _ in fh)
            byte_count = os.path.getsize(memory_md)
            kb = f"{byte_count / 1024:.0f}"

            # Count how many actions were real work (not just status).
            work = [a for a in actions if "rebalancing" not in a.lower()
                    or "rebalancing..." in a.lower()]
            rebalanced = any("rebalancing..." in a for a in actions)

            if warnings:
                # Short summary for the status line.  Full details go
                # to additionalContext where Claude can act on them.
                summary = (f"alzheimer: {line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB — "
                           f"{len(warnings)} issue(s) need attention")
            elif rebalanced:
                summary = (f"alzheimer: rebalanced to "
                           f"{line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB")
            else:
                summary = (f"alzheimer: {line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB — balanced")

        else:
            summary = "alzheimer: no MEMORY.md found"

        # Check for alzheimer updates on SessionStart and PreCompact.
        update_msg = None
        if args.hook_event in ("SessionStart", "PreCompact"):
            behind, update_msg = check_for_updates()
            if behind > 0:
                summary += " (update available)"

        # Build single JSON output: systemMessage for user's UI,
        # hookSpecificOutput.additionalContext for Claude's context.
        output = {"systemMessage": summary}

        additional = list(messages)
        if update_msg:
            additional.append(update_msg)
        if warnings:
            warn_summary = "; ".join(warnings)
            additional.append(
                f"The alzheimer memory rebalancer completed but "
                f"found issues it could not resolve: {warn_summary}. "
                f"Run `python3 {rebalancer_path} "
                f"{memory_dir} --diagnose` for a full report, "
                f"then explain the situation to the user."
            )
        if additional:
            additional.append(
                "When narrating actions triggered by these instructions, "
                "use [Alzheimer: ...] format so the user can visually "
                "distinguish housekeeping from the conversation."
            )
            additional_text = "\n\n".join(additional)
            # hookSpecificOutput with additionalContext is only valid
            # for PostToolUse and UserPromptSubmit events.  For other
            # events (SessionStart, PreCompact), fold into systemMessage
            # so the instructions reach Claude instead of being silently
            # dropped by hook output validation.
            hso_supported = ("PostToolUse", "UserPromptSubmit")
            if args.hook_event and args.hook_event in hso_supported:
                output["hookSpecificOutput"] = {
                    "additionalContext": additional_text,
                    "hookEventName": args.hook_event,
                }
            else:
                output["systemMessage"] += "\n" + additional_text

        print(json.dumps(output))
    else:
        for action in actions:
            print(f"{prefix}{action}")

        # Emit glossary systemMessages.
        for msg in messages:
            print(json.dumps({"systemMessage": msg}))

        if warnings:
            # Unresolvable issues — tell Claude to inform the user.
            warn_summary = "; ".join(warnings)
            print(json.dumps({
                "systemMessage": (
                    f"The alzheimer memory rebalancer completed but "
                    f"found issues it could not resolve: {warn_summary}. "
                    f"Run `python3 {rebalancer_path} "
                    f"{memory_dir} --diagnose` for a full report, "
                    f"then explain the situation to the user. If it "
                    f"looks like a bug, ask the user if they'd like "
                    f"you to file an issue by running "
                    f"`python3 {rebalancer_path} {memory_dir} --report`."
                ),
            }))


if __name__ == "__main__":
    main()
