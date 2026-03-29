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

VERSION = "0.1.0"
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


# ── Main rebalance logic ──────────────────────────────────────────────

def rebalance(memory_dir, max_lines=DEFAULT_MAX_LINES,
              max_bytes=DEFAULT_MAX_BYTES, dry_run=False):
    """Rebalance the memory tree rooted at MEMORY.md.

    Returns (actions, warnings) where:
        actions:  list of action descriptions (for logging / dry-run)
        warnings: list of unresolvable issues (max depth, too few to split)
    """
    actions = []
    warnings = []
    memory_md = os.path.join(memory_dir, "MEMORY.md")

    if not os.path.exists(memory_md):
        return ["MEMORY.md not found — nothing to do."], []

    header, entries = parse_index(memory_md)
    total_lines = len(header) + len(entries) + 1  # +1 for trailing newline
    total_bytes = file_size_bytes(memory_md)
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
        return actions, warnings

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

    return actions, warnings


def extract_keywords(text):
    """Extract significant keywords from a description string."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    return [w for w in words if w not in STOP_WORDS]


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
            # If it's a sub-index, recurse into it.
            full = os.path.join(memory_dir, resolved)
            if os.path.exists(full) and not e["path"].startswith("../"):
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
            elif not e["path"].startswith("../"):
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


def format_bug_report(anomalies, memory_dir, exception_info=None):
    """Format anomalies into a GitHub issue body.

    Never includes personal memory content — only structural information
    (file names, line counts, error messages).
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
        lines.append(f"- **{a.severity}**: {a.message}")

    # Tree structure snapshot (filenames only, no content).
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
            for root, dirs, files in os.walk(index_dir):
                for f in sorted(files):
                    if f.endswith(".md"):
                        fpath = os.path.join(root, f)
                        rel = os.path.relpath(fpath, memory_dir)
                        _, fe = parse_index(fpath)
                        lines.append(
                            f"  {rel}: {len(fe)} entries, "
                            f"{file_size_bytes(fpath)} bytes"
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
            # Recurse into sub-indices.
            if not e["path"].startswith("../") and os.path.exists(target):
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
            if not entry["path"].startswith("../"):
                child = os.path.normpath(
                    os.path.join(os.path.dirname(index_path),
                                 entry["path"])
                )
                if os.path.exists(child):
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
        "--verify", action="store_true",
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
        actions, warnings = rebalance(memory_dir, max_lines, max_bytes,
                                      args.dry_run)
    except Exception:
        exception_info = traceback.format_exc()
        actions = [f"ERROR: rebalance failed: {exception_info}"]
        warnings = []

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
        # Build a brief summary for the user's UI.
        memory_md = os.path.join(memory_dir, "MEMORY.md")
        if os.path.exists(memory_md):
            line_count = sum(1 for _ in open(memory_md))
            byte_count = os.path.getsize(memory_md)
            kb = f"{byte_count / 1024:.0f}"

            # Count how many actions were real work (not just status).
            work = [a for a in actions if "rebalancing" not in a.lower()
                    or "rebalancing..." in a.lower()]
            rebalanced = any("rebalancing..." in a for a in actions)

            if warnings:
                warn_summary = "; ".join(warnings)
                summary = (f"alzheimer: {line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB — "
                           f"warnings: {warn_summary}")
            elif rebalanced:
                summary = (f"alzheimer: rebalanced to "
                           f"{line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB")
            else:
                summary = (f"alzheimer: {line_count}/{max_lines} lines, "
                           f"{kb}/{max_bytes // 1024} KB — balanced")

        else:
            summary = "alzheimer: no MEMORY.md found"

        print(json.dumps({"systemMessage": summary}))

        if warnings:
            # Also emit the detailed warning for Claude's context.
            warn_summary = "; ".join(warnings)
            print(json.dumps({
                "systemMessage": (
                    f"The alzheimer memory rebalancer completed but "
                    f"found issues it could not resolve: {warn_summary}. "
                    f"Run `python3 {rebalancer_path} "
                    f"{memory_dir} --diagnose` for a full report, "
                    f"then explain the situation to the user."
                ),
            }))
    else:
        for action in actions:
            print(f"{prefix}{action}")

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
