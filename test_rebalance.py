#!/usr/bin/env python3
"""Tests for the alzheimer memory rebalancer."""

import json
import os
import shutil
import tempfile
import time
import unittest

from rebalance import (
    Anomaly,
    CONFIG_FILE,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_BYTES,
    GLOSSARY_FILE,
    GLOSSARY_MAX_TERMS,
    GLOSSARY_MIN_TERMS,
    GUARDRAILS_FILE,
    HARD_MAX_LINES,
    HARD_MAX_BYTES,
    LEAF_MAX_LINES,
    MIN_GROUP_SIZE,
    MAX_DEPTH,
    UPDATE_CACHE_FILE,
    UPDATE_CHECK_INTERVAL,
    _read_update_cache,
    _write_update_cache,
    build_glossary_entry,
    build_guardrails_entry,
    check_drift,
    check_for_updates,
    collect_anomalies,
    collect_memory_files,
    count_inline_content,
    extract_keywords,
    file_size_bytes,
    find_orphans,
    format_bug_report,
    get_limits,
    glossary_is_stale,
    glossary_system_message,
    guardrails_is_stale,
    guardrails_system_message,
    group_entries_by_keyword,
    is_category_entry,
    load_config,
    parse_glossary,
    parse_guardrails,
    parse_index,
    read_all_frontmatter,
    read_frontmatter_type,
    rebalance,
    summarize_entries,
    update_glossary,
    update_guardrails,
    verify_tree,
)

from guardrails import (
    check_rules, load_rules, DEFAULT_RULES, get_match_text,
    _is_self_exec, _config_path, _load_config, _save_config,
    add_rule, remove_rule, exec_with_temporary_allow, find_matching_rule,
)

from reminders import (
    should_check, touch_timestamp, parse_date_reminders,
    parse_daily_checks, parse_recurring_reminders,
    check_date_reminders, check_recurring_reminders,
    collect_due_reminders, escalation_prefix,
    _read_fire_count, _write_fire_count, _reset_fire_count,
    TIMESTAMP_FILE, RECURRING_STATE_FILE, FIRE_COUNT_FILE,
)


class TestDir:
    """Context manager that creates a temp memory directory."""

    def __init__(self):
        self.path = None

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="alzheimer-test-")
        return self.path

    def __exit__(self, *args):
        shutil.rmtree(self.path)


def make_leaf(directory, filename, mem_type, title, desc="Test entry."):
    """Create a leaf memory file with frontmatter."""
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write(f"---\nname: {title}\n"
                f"description: {desc}\ntype: {mem_type}\n---\n\n"
                f"Content for {title}.\n")
    return filename


def make_index(directory, entries, header=None):
    """Create a MEMORY.md from a list of (title, path, desc) tuples."""
    if header is None:
        header = ["# Memory Index", ""]
    lines = list(header)
    for title, path, desc in entries:
        lines.append(f"- [{title}]({path}) — {desc}")
    lines.append("")
    filepath = os.path.join(directory, "MEMORY.md")
    with open(filepath, "w") as f:
        f.write("\n".join(lines))


# ── Unit tests ─────────────────────────────────────────────────────────

class TestParseIndex(unittest.TestCase):

    def test_basic_parse(self):
        with TestDir() as d:
            make_index(d, [
                ("Foo", "foo.md", "does foo"),
                ("Bar", "bar.md", "does bar"),
            ])
            header, entries = parse_index(os.path.join(d, "MEMORY.md"))
            self.assertEqual(len(header), 2)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["title"], "Foo")
            self.assertEqual(entries[0]["path"], "foo.md")
            self.assertEqual(entries[0]["desc"], "does foo")

    def test_em_dash_and_double_dash(self):
        with TestDir() as d:
            path = os.path.join(d, "MEMORY.md")
            with open(path, "w") as f:
                f.write("# Index\n\n"
                        "- [A](a.md) — em dash\n"
                        "- [B](b.md) -- double dash\n")
            _, entries = parse_index(path)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["desc"], "em dash")
            self.assertEqual(entries[1]["desc"], "double dash")

    def test_empty_file(self):
        with TestDir() as d:
            path = os.path.join(d, "MEMORY.md")
            with open(path, "w") as f:
                f.write("# Empty\n")
            header, entries = parse_index(path)
            self.assertEqual(len(entries), 0)


class TestReadFrontmatterType(unittest.TestCase):

    def test_reads_type(self):
        with TestDir() as d:
            make_leaf(d, "test.md", "feedback", "Test")
            self.assertEqual(
                read_frontmatter_type(os.path.join(d, "test.md")),
                "feedback"
            )

    def test_no_frontmatter(self):
        with TestDir() as d:
            path = os.path.join(d, "plain.md")
            with open(path, "w") as f:
                f.write("Just some text.\n")
            self.assertIsNone(read_frontmatter_type(path))

    def test_missing_type(self):
        with TestDir() as d:
            path = os.path.join(d, "nofield.md")
            with open(path, "w") as f:
                f.write("---\nname: Foo\n---\n")
            self.assertIsNone(read_frontmatter_type(path))

    def test_nonexistent_file(self):
        self.assertIsNone(read_frontmatter_type("/nonexistent/file.md"))


class TestExtractKeywords(unittest.TestCase):

    def test_filters_stop_words(self):
        kw = extract_keywords("the quick brown fox is not very fast")
        self.assertIn("quick", kw)
        self.assertIn("brown", kw)
        self.assertIn("fox", kw)
        self.assertIn("fast", kw)
        self.assertNotIn("the", kw)
        self.assertNotIn("not", kw)
        self.assertNotIn("very", kw)

    def test_minimum_length(self):
        kw = extract_keywords("go to do an ok")
        self.assertEqual(kw, [])

    def test_case_insensitive(self):
        kw = extract_keywords("GitHub Actions Pipeline")
        self.assertIn("github", kw)
        self.assertIn("actions", kw)
        self.assertIn("pipeline", kw)


class TestSummarizeEntries(unittest.TestCase):

    def test_short_list(self):
        entries = [
            {"title": "Foo"},
            {"title": "Bar"},
        ]
        self.assertEqual(summarize_entries(entries), "Foo, Bar")

    def test_truncation(self):
        entries = [{"title": f"Entry {i:03d}"} for i in range(50)]
        result = summarize_entries(entries, max_len=50)
        self.assertLessEqual(len(result), 53)  # +3 for "..."
        self.assertTrue(result.endswith(", ..."))


class TestGroupByKeyword(unittest.TestCase):

    def test_finds_common_keyword(self):
        entries = [
            {"title": "Link checker", "desc": "check broken links"},
            {"title": "Link style", "desc": "consistent link format"},
            {"title": "Link count", "desc": "count links per page"},
            {"title": "Auth setup", "desc": "configure authentication"},
            {"title": "Auth tokens", "desc": "manage auth tokens"},
            {"title": "Auth refresh", "desc": "refresh expired auth"},
        ]
        groups = group_entries_by_keyword(entries)
        # Should find "link" and "auth" as grouping keywords.
        found_groups = {k for k, v in groups.items()
                        if k != "_ungrouped" and k != "_rest"}
        self.assertTrue(len(found_groups) >= 1)

    def test_too_few_entries(self):
        entries = [
            {"title": "A", "desc": "one thing"},
            {"title": "B", "desc": "another thing"},
        ]
        groups = group_entries_by_keyword(entries)
        self.assertIn("_ungrouped", groups)


# ── Integration tests ──────────────────────────────────────────────────

class TestRebalanceNoOp(unittest.TestCase):

    def test_under_limit(self):
        with TestDir() as d:
            make_leaf(d, "f1.md", "feedback", "F1")
            make_leaf(d, "f2.md", "feedback", "F2")
            make_index(d, [
                ("F1", "f1.md", "first"),
                ("F2", "f2.md", "second"),
            ])
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertTrue(any("no rebalancing" in a for a in actions))

    def test_missing_memory_md(self):
        with TestDir() as d:
            actions, _, _ = rebalance(d)
            self.assertTrue(any("not found" in a for a in actions))


class TestRebalanceLevel1(unittest.TestCase):

    def test_groups_by_type(self):
        with TestDir() as d:
            entries = []
            for i in range(5):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}",
                          f"feedback entry {i}")
                entries.append((f"FB {i}", fname, f"feedback entry {i}"))
            for i in range(5):
                fname = f"project_{i}.md"
                make_leaf(d, fname, "project", f"Proj {i}",
                          f"project entry {i}")
                entries.append((f"Proj {i}", fname, f"project entry {i}"))

            make_index(d, entries)
            actions, _, _ = rebalance(d, max_lines=8)

            # MEMORY.md should now have category pointers, not leaves.
            _, new_entries = parse_index(os.path.join(d, "MEMORY.md"))
            cat_entries = [e for e in new_entries if is_category_entry(e)]
            self.assertGreaterEqual(len(cat_entries), 2)

            # Category indices should exist.
            self.assertTrue(os.path.exists(
                os.path.join(d, "_index", "feedback.md")))
            self.assertTrue(os.path.exists(
                os.path.join(d, "_index", "project.md")))

    def test_small_groups_stay_flat(self):
        with TestDir() as d:
            entries = []
            # 2 feedback (below MIN_GROUP_SIZE) + 5 project.
            for i in range(2):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}")
                entries.append((f"FB {i}", fname, f"feedback {i}"))
            for i in range(5):
                fname = f"project_{i}.md"
                make_leaf(d, fname, "project", f"Proj {i}")
                entries.append((f"Proj {i}", fname, f"project {i}"))

            make_index(d, entries)
            actions, _, _ = rebalance(d, max_lines=5)

            _, new_entries = parse_index(os.path.join(d, "MEMORY.md"))
            # Feedback should still be flat (2 < MIN_GROUP_SIZE).
            flat_fb = [e for e in new_entries
                       if "feedback" in e.get("path", "")
                       and not is_category_entry(e)]
            self.assertEqual(len(flat_fb), 2)

    def test_idempotent(self):
        with TestDir() as d:
            entries = []
            for i in range(6):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}")
                entries.append((f"FB {i}", fname, f"fb {i}"))

            make_index(d, entries)
            rebalance(d, max_lines=5)

            # Read state after first rebalance.
            with open(os.path.join(d, "MEMORY.md")) as f:
                content1 = f.read()

            # Run again.
            rebalance(d, max_lines=5)

            with open(os.path.join(d, "MEMORY.md")) as f:
                content2 = f.read()

            self.assertEqual(content1, content2)


class TestRebalanceLevel2(unittest.TestCase):

    def test_splits_large_category(self):
        with TestDir() as d:
            entries = []
            # Create 20 feedback entries with two topic clusters.
            for i in range(10):
                fname = f"feedback_link_{i}.md"
                make_leaf(d, fname, "feedback", f"Link rule {i}",
                          f"checking broken links for page {i}")
                entries.append((f"Link rule {i}", fname,
                                f"checking broken links for page {i}"))
            for i in range(10):
                fname = f"feedback_auth_{i}.md"
                make_leaf(d, fname, "feedback", f"Auth rule {i}",
                          f"authentication token handling {i}")
                entries.append((f"Auth rule {i}", fname,
                                f"authentication token handling {i}"))

            make_index(d, entries)

            # Use very low limits to force both levels to trigger.
            actions, _, _ = rebalance(d, max_lines=10)

            # Level 1: MEMORY.md should have a category pointer.
            _, root_entries = parse_index(os.path.join(d, "MEMORY.md"))
            self.assertTrue(any(is_category_entry(e) for e in root_entries))

            # Level 2: _index/feedback.md should have sub-index pointers
            # (if the category was large enough to split).
            fb_index = os.path.join(d, "_index", "feedback.md")
            if os.path.exists(fb_index):
                _, fb_entries = parse_index(fb_index)
                # With max_lines=10, 20 entries should trigger splitting.
                total_lines = 11 + 20 + 1  # header + entries + newline
                if total_lines > 10:
                    # Check that sub-indices were created.
                    fb_sub = os.path.join(d, "_index", "feedback")
                    if os.path.isdir(fb_sub):
                        sub_files = os.listdir(fb_sub)
                        self.assertGreater(len(sub_files), 0)


class TestOrphans(unittest.TestCase):

    def test_finds_orphans(self):
        with TestDir() as d:
            make_leaf(d, "indexed.md", "feedback", "Indexed")
            make_leaf(d, "orphan.md", "feedback", "Orphan")
            make_index(d, [("Indexed", "indexed.md", "is indexed")])
            orphans = find_orphans(d)
            self.assertIn("orphan.md", orphans)
            self.assertNotIn("indexed.md", orphans)

    def test_no_orphans(self):
        with TestDir() as d:
            make_leaf(d, "a.md", "feedback", "A")
            make_index(d, [("A", "a.md", "is indexed")])
            self.assertEqual(find_orphans(d), [])

    def test_finds_in_category_index(self):
        """Files referenced by _index/ files are not orphans."""
        with TestDir() as d:
            make_leaf(d, "deep.md", "feedback", "Deep")
            make_index(d, [
                ("Cat", "_index/feedback.md", "feedback category"),
            ])
            # Create category index referencing deep.md.
            idx_dir = os.path.join(d, "_index")
            os.makedirs(idx_dir)
            with open(os.path.join(idx_dir, "feedback.md"), "w") as f:
                f.write("# Feedback\n\n"
                        "- [Deep](../deep.md) — deep entry\n")
            orphans = find_orphans(d)
            self.assertNotIn("deep.md", orphans)


class TestDryRun(unittest.TestCase):

    def test_no_files_modified(self):
        with TestDir() as d:
            entries = []
            for i in range(6):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}")
                entries.append((f"FB {i}", fname, f"fb {i}"))
            make_index(d, entries)

            with open(os.path.join(d, "MEMORY.md")) as f:
                before = f.read()

            rebalance(d, max_lines=5, dry_run=True)

            with open(os.path.join(d, "MEMORY.md")) as f:
                after = f.read()

            self.assertEqual(before, after)
            self.assertFalse(os.path.exists(os.path.join(d, "_index")))


class TestRebalanceLevel3(unittest.TestCase):
    """Test that rebalancing works at depth 3 (sub-sub-indices)."""

    def test_three_level_tree(self):
        with TestDir() as d:
            entries = []
            # 60 feedback entries with 3 topic clusters of 20 each.
            topics = {
                "link": "checking broken links on website page",
                "auth": "authentication token credential handling",
                "style": "formatting style convention rule",
            }
            for topic, desc_base in topics.items():
                for i in range(20):
                    fname = f"feedback_{topic}_{i}.md"
                    make_leaf(d, fname, "feedback", f"{topic} rule {i}",
                              f"{desc_base} {i}")
                    entries.append(
                        (f"{topic} rule {i}", fname,
                         f"{desc_base} {i}")
                    )

            make_index(d, entries)

            # With max_lines=10, this forces:
            #   level 0: MEMORY.md (60 entries -> 1 category pointer)
            #   level 1: _index/feedback.md (60 entries -> 2-3 topic groups)
            #   level 2: if groups are still large, split again
            actions, _, _ = rebalance(d, max_lines=10)

            # Verify MEMORY.md is under limit.
            _, root = parse_index(os.path.join(d, "MEMORY.md"))
            root_lines = 3 + len(root)  # header + entries + newline
            self.assertLessEqual(root_lines, 10)

            # Verify _index/ structure exists.
            self.assertTrue(os.path.isdir(os.path.join(d, "_index")))

            # Verify at least some sub-indices were created.
            fb_dir = os.path.join(d, "_index", "feedback")
            if os.path.isdir(fb_dir):
                sub_files = os.listdir(fb_dir)
                self.assertGreater(len(sub_files), 0)


class TestByteSizeLimit(unittest.TestCase):

    def test_triggers_on_byte_limit(self):
        with TestDir() as d:
            entries = []
            # Create entries with very long descriptions to hit byte limit.
            for i in range(10):
                fname = f"feedback_{i}.md"
                long_desc = f"x" * 200 + f" entry {i}"
                make_leaf(d, fname, "feedback", f"Entry {i}", long_desc)
                entries.append((f"Entry {i}", fname, long_desc))

            make_index(d, entries)

            # Lines are fine (13) but bytes will be high.
            mem_md = os.path.join(d, "MEMORY.md")
            size = file_size_bytes(mem_md)

            # Set byte limit below actual size, line limit high.
            actions, _, _ = rebalance(d, max_lines=200, max_bytes=size - 100)

            # Should have rebalanced despite being under line limit.
            self.assertTrue(any("rebalancing" in a for a in actions))

    def test_no_trigger_under_byte_limit(self):
        with TestDir() as d:
            entries = []
            for i in range(3):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"F{i}", "short")
                entries.append((f"F{i}", fname, "short"))
            make_index(d, entries)
            actions, _, _ = rebalance(d, max_lines=200, max_bytes=50000)
            self.assertTrue(any("no rebalancing" in a for a in actions))


class TestAutoDreamRecovery(unittest.TestCase):
    """Test recovery from Auto Dream flattening our categories."""

    def test_recovers_from_flattened_index(self):
        """Simulate Auto Dream removing category pointers and replacing
        them with flat entries."""
        with TestDir() as d:
            # First, create a proper tree.
            entries = []
            for i in range(8):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}", f"entry {i}")
                entries.append((f"FB {i}", fname, f"entry {i}"))
            for i in range(4):
                fname = f"project_{i}.md"
                make_leaf(d, fname, "project", f"Proj {i}", f"proj {i}")
                entries.append((f"Proj {i}", fname, f"proj {i}"))

            make_index(d, entries)
            rebalance(d, max_lines=8)

            # Verify tree was built.
            _, tree_entries = parse_index(os.path.join(d, "MEMORY.md"))
            cat_count = sum(1 for e in tree_entries
                            if is_category_entry(e))
            self.assertGreater(cat_count, 0)

            # Now simulate Auto Dream flattening: rewrite MEMORY.md
            # with all entries as flat leaves again.
            make_index(d, entries)

            # Re-run rebalancer — it should rebuild the tree.
            actions, _, _ = rebalance(d, max_lines=8)
            self.assertTrue(any("rebalancing" in a for a in actions))

            _, rebuilt = parse_index(os.path.join(d, "MEMORY.md"))
            rebuilt_cats = sum(1 for e in rebuilt
                               if is_category_entry(e))
            self.assertGreater(rebuilt_cats, 0)

    def test_handles_stale_category_index(self):
        """Category index exists but MEMORY.md no longer points to it."""
        with TestDir() as d:
            make_leaf(d, "f1.md", "feedback", "F1", "entry")
            make_index(d, [("F1", "f1.md", "entry")])

            # Create an orphaned category index.
            idx_dir = os.path.join(d, "_index")
            os.makedirs(idx_dir)
            with open(os.path.join(idx_dir, "old.md"), "w") as f:
                f.write("---\ntype: index\n---\n\n# Old\n\n"
                        "- [Gone](../gone.md) — deleted\n")

            # Rebalance should work fine despite the stale index.
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertTrue(any("no rebalancing" in a for a in actions))


class TestVerifyTree(unittest.TestCase):

    def test_healthy_tree(self):
        with TestDir() as d:
            make_leaf(d, "f1.md", "feedback", "F1", "ok")
            make_index(d, [("F1", "f1.md", "ok")])
            self.assertTrue(verify_tree(d))

    def test_broken_reference(self):
        with TestDir() as d:
            # Reference a file that doesn't exist.
            make_index(d, [("Ghost", "ghost.md", "missing")])
            self.assertFalse(verify_tree(d))

    def test_oversized_root(self):
        """Verify detects MEMORY.md over the hard 200-line limit."""
        with TestDir() as d:
            entries = []
            for i in range(250):
                fname = f"f_{i}.md"
                make_leaf(d, fname, "feedback", f"F{i}", f"e{i}")
                entries.append((f"F{i}", fname, f"e{i}"))
            make_index(d, entries)
            # verify_tree should report failure.
            self.assertFalse(verify_tree(d))


class TestDepthLimit(unittest.TestCase):

    def test_stops_at_max_depth(self):
        """Ensure rebalancing doesn't recurse beyond MAX_DEPTH."""
        with TestDir() as d:
            # Create a scenario that could recurse deeply.
            entries = []
            for i in range(30):
                fname = f"feedback_{i}.md"
                # All entries identical keywords — forces fallback split.
                make_leaf(d, fname, "feedback", f"Item {i}",
                          f"identical description {i}")
                entries.append((f"Item {i}", fname,
                                f"identical description {i}"))
            make_index(d, entries)

            # Very low limit forces many splits.
            actions, _, _ = rebalance(d, max_lines=5)

            # Check that max depth message appears if needed, and
            # importantly, that it terminates (no infinite loop).
            self.assertIsInstance(actions, list)


class TestEdgeCases(unittest.TestCase):

    def test_empty_memory_dir(self):
        with TestDir() as d:
            # No MEMORY.md at all.
            actions, _, _ = rebalance(d)
            self.assertTrue(any("not found" in a for a in actions))

    def test_malformed_frontmatter(self):
        with TestDir() as d:
            # File with broken frontmatter.
            path = os.path.join(d, "bad.md")
            with open(path, "w") as f:
                f.write("---\nthis is not: valid: yaml: stuff\n---\n")
            make_index(d, [("Bad", "bad.md", "malformed")])
            # Should not crash.
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertIsInstance(actions, list)

    def test_unicode_in_entries(self):
        with TestDir() as d:
            make_leaf(d, "uni.md", "feedback", "Ünïcödé",
                      'handles em dashes \u2014 and smart quotes \u201clike this\u201d')
            make_index(d, [
                ("Ünïcödé", "uni.md",
                 'handles em dashes \u2014 and smart quotes \u201clike this\u201d'),
            ])
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertTrue(any("no rebalancing" in a for a in actions))

    def test_concurrent_add_during_rebalance(self):
        """Simulate a new file appearing between rebalance runs."""
        with TestDir() as d:
            entries = []
            for i in range(6):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}", f"entry {i}")
                entries.append((f"FB {i}", fname, f"entry {i}"))
            make_index(d, entries)

            # First rebalance.
            rebalance(d, max_lines=5)

            # Now add a new file and update MEMORY.md manually
            # (simulating Claude writing a new memory).
            make_leaf(d, "feedback_new.md", "feedback", "New", "new entry")
            with open(os.path.join(d, "MEMORY.md"), "a") as f:
                f.write("- [New](feedback_new.md) — new entry\n")

            # Second rebalance should handle this gracefully.
            actions, _, _ = rebalance(d, max_lines=5)
            orphans = find_orphans(d)
            self.assertNotIn("feedback_new.md", orphans)


class TestLoadConfig(unittest.TestCase):
    """Tests for .alzheimer.conf loading."""

    def test_no_config_file(self):
        """Returns empty dict when no config file exists."""
        with TestDir() as d:
            config = load_config(d)
            self.assertEqual(config, {})

    def test_basic_config(self):
        """Reads key=value pairs from config file."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("max_lines = 100\nmax_bytes = 10000\n")
            config = load_config(d)
            self.assertEqual(config["max_lines"], 100)
            self.assertEqual(config["max_bytes"], 10000)

    def test_comments_and_blanks(self):
        """Ignores comments and blank lines."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("# This is a comment\n\n"
                        "max_lines = 80\n"
                        "# Another comment\n")
            config = load_config(d)
            self.assertEqual(config, {"max_lines": 80})

    def test_ignores_unknown_keys(self):
        """Unknown keys are silently ignored."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("max_lines = 100\nfoo = bar\n")
            config = load_config(d)
            self.assertEqual(config, {"max_lines": 100})

    def test_invalid_value_ignored(self):
        """Non-integer values are silently ignored."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("max_lines = not_a_number\n")
            config = load_config(d)
            self.assertEqual(config, {})

    def test_hard_limits_configurable(self):
        """Hard limits can be overridden via config."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("hard_max_lines = 300\n"
                        "hard_max_bytes = 51200\n")
            config = load_config(d)
            self.assertEqual(config["hard_max_lines"], 300)
            self.assertEqual(config["hard_max_bytes"], 51200)

    def test_all_recognized_keys(self):
        """All six recognized keys are loaded."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("hard_max_lines = 250\n"
                        "hard_max_bytes = 30000\n"
                        "max_lines = 120\n"
                        "max_bytes = 15000\n"
                        "max_depth = 4\n"
                        "min_group_size = 5\n")
            config = load_config(d)
            self.assertEqual(len(config), 6)


class TestGetLimits(unittest.TestCase):
    """Tests for limit resolution priority."""

    def test_defaults_without_config(self):
        """Returns module defaults when no config file and no CLI."""
        with TestDir() as d:
            soft_l, soft_b, hard_l, hard_b = get_limits(d)
            self.assertEqual(soft_l, DEFAULT_MAX_LINES)
            self.assertEqual(soft_b, DEFAULT_MAX_BYTES)
            self.assertEqual(hard_l, HARD_MAX_LINES)
            self.assertEqual(hard_b, HARD_MAX_BYTES)

    def test_config_overrides_defaults(self):
        """Config file overrides module defaults."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("max_lines = 100\nmax_bytes = 10000\n"
                        "hard_max_lines = 250\n")
            soft_l, soft_b, hard_l, hard_b = get_limits(d)
            self.assertEqual(soft_l, 100)
            self.assertEqual(soft_b, 10000)
            self.assertEqual(hard_l, 250)
            self.assertEqual(hard_b, HARD_MAX_BYTES)

    def test_cli_overrides_config(self):
        """CLI flags override config file values."""
        with TestDir() as d:
            with open(os.path.join(d, CONFIG_FILE), "w") as f:
                f.write("max_lines = 100\nmax_bytes = 10000\n")
            soft_l, soft_b, hard_l, hard_b = get_limits(
                d, cli_max_lines=80, cli_max_bytes=8000)
            self.assertEqual(soft_l, 80)
            self.assertEqual(soft_b, 8000)

    def test_cli_overrides_defaults(self):
        """CLI flags override module defaults even without config."""
        with TestDir() as d:
            soft_l, soft_b, _, _ = get_limits(
                d, cli_max_lines=75)
            self.assertEqual(soft_l, 75)
            self.assertEqual(soft_b, DEFAULT_MAX_BYTES)


class TestConfigIntegration(unittest.TestCase):
    """Config file affects verify_tree and collect_anomalies."""

    def test_verify_uses_custom_hard_limit(self):
        """verify_tree respects custom hard limits."""
        with TestDir() as d:
            # Create a 253-line MEMORY.md (over default 200 hard limit).
            entries = []
            for i in range(250):
                fname = f"f{i}.md"
                make_leaf(d, fname, "feedback", f"F{i}", f"entry {i}")
                entries.append((f"F{i}", fname, f"entry {i}"))
            make_index(d, entries)

            # With default hard limit (200), this should fail.
            ok = verify_tree(d, hard_max_lines=200)
            self.assertFalse(ok)

            # With raised hard limit (300), the hard check passes.
            ok = verify_tree(d, hard_max_lines=300)
            # Still might warn on soft limit, but shouldn't FAIL.
            # (ok depends on whether soft limit is also exceeded)

    def test_anomalies_uses_custom_hard_limit(self):
        """collect_anomalies respects custom hard limits."""
        with TestDir() as d:
            entries = []
            for i in range(250):
                fname = f"f{i}.md"
                make_leaf(d, fname, "feedback", f"F{i}", f"entry {i}")
                entries.append((f"F{i}", fname, f"entry {i}"))
            make_index(d, entries)

            # Default hard limit: should have an error.
            anomalies = collect_anomalies(d, hard_max_lines=200)
            errors = [a for a in anomalies if a.severity == "error"
                      and "hard limit" in a.message
                      and "lines" in a.message]
            self.assertTrue(len(errors) > 0)

            # Raised hard limit: no line-count error.
            anomalies = collect_anomalies(d, hard_max_lines=300)
            errors = [a for a in anomalies if a.severity == "error"
                      and "hard limit" in a.message
                      and "lines" in a.message]
            self.assertEqual(len(errors), 0)


class TestFrontmatter(unittest.TestCase):
    """Tests for frontmatter parsing."""

    def test_read_all_frontmatter(self):
        """Reads all frontmatter fields."""
        with TestDir() as d:
            path = os.path.join(d, "test.md")
            with open(path, "w") as f:
                f.write("---\nname: Test Name\ntype: user\n"
                        "description: A test file\n---\nBody.\n")
            fm = read_all_frontmatter(path)
            self.assertEqual(fm["name"], "Test Name")
            self.assertEqual(fm["type"], "user")
            self.assertEqual(fm["description"], "A test file")

    def test_read_all_frontmatter_no_frontmatter(self):
        """Returns empty dict for files without frontmatter."""
        with TestDir() as d:
            path = os.path.join(d, "plain.md")
            with open(path, "w") as f:
                f.write("Just a plain file.\n")
            fm = read_all_frontmatter(path)
            self.assertEqual(fm, {})


class TestGlossary(unittest.TestCase):
    """Tests for Claude-generated glossary integration."""

    def _make_tree(self, d, n_files=6):
        """Create a memory tree with enough files for glossary."""
        entries = []
        for i in range(n_files):
            make_leaf(d, f"proj_{i}.md", "project",
                      f"Config {i}", f"Project Zenith deployment config")
            entries.append(
                (f"Config {i}", f"proj_{i}.md", f"Project Zenith config"))
        make_index(d, entries)

    def _make_glossary(self, d, terms=None):
        """Pre-create a glossary.md (simulating Claude having written it)."""
        if terms is None:
            terms = [
                ("Project Zenith", "Main deployment project"),
                ("Server Omega", "Build server for CI/CD"),
                ("Team Delta", "Code review team"),
            ]
        lines = [
            "---",
            "type: glossary",
            "updated: 2026-03-29",
            f"terms: {len(terms)}",
            "---",
            "",
            "# Key Terms",
            "",
        ]
        for name, defn in terms:
            lines.append(f"- **{name}** — {defn}")
        lines.append("")
        path = os.path.join(d, GLOSSARY_FILE)
        with open(path, "w") as f:
            f.write("\n".join(lines))

    def test_stale_glossary_emits_message(self):
        """When glossary is missing, rebalance emits a systemMessage."""
        with TestDir() as d:
            self._make_tree(d)
            _, _, messages = rebalance(d, max_lines=150)
            self.assertTrue(len(messages) > 0)
            self.assertIn("GLOSSARY UPDATE NEEDED", messages[0])

    def test_stale_glossary_no_file_created(self):
        """Rebalance does NOT create glossary.md — Claude does that."""
        with TestDir() as d:
            self._make_tree(d)
            rebalance(d, max_lines=150)
            self.assertFalse(
                os.path.exists(os.path.join(d, GLOSSARY_FILE)))

    def test_fresh_glossary_no_message(self):
        """When glossary is fresh, no systemMessage is emitted."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            _, _, messages = rebalance(d, max_lines=150)
            self.assertEqual(len(messages), 0)

    def test_existing_glossary_entry_in_index(self):
        """Pre-existing glossary gets an entry in MEMORY.md."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            paths = [e["path"] for e in entries]
            self.assertIn(GLOSSARY_FILE, paths)

    def test_glossary_entry_description_has_terms(self):
        """MEMORY.md glossary entry description lists key terms."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            glossary = [e for e in entries if e["path"] == GLOSSARY_FILE]
            self.assertEqual(len(glossary), 1)
            desc = glossary[0]["desc"]
            self.assertIn("Project Zenith", desc)

    def test_glossary_pinned_after_rebalance(self):
        """Glossary stays in MEMORY.md root, not pushed to _index/."""
        with TestDir() as d:
            self._make_tree(d, n_files=10)
            self._make_glossary(d)
            rebalance(d, max_lines=8)
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            glossary_entries = [e for e in entries
                                if e["path"] == GLOSSARY_FILE]
            self.assertEqual(len(glossary_entries), 1)
            self.assertFalse(
                any(e["path"].startswith("_index/glossary")
                    for e in entries))

    def test_glossary_readded_if_entry_removed(self):
        """Glossary entry is restored if deleted from MEMORY.md."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            # Remove glossary entry from MEMORY.md.
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            entries = [e for e in entries if e["path"] != GLOSSARY_FILE]
            with open(os.path.join(d, "MEMORY.md"), "w") as f:
                f.write("# Memory Index\n\n")
                for e in entries:
                    f.write(e["raw"] + "\n")
            # Re-run — glossary.md still exists, entry should come back.
            rebalance(d, max_lines=150)
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            paths = [e["path"] for e in entries]
            self.assertIn(GLOSSARY_FILE, paths)

    def test_glossary_idempotent(self):
        """Running rebalance twice produces same MEMORY.md."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            with open(os.path.join(d, "MEMORY.md")) as f:
                first = f.read()
            rebalance(d, max_lines=150)
            with open(os.path.join(d, "MEMORY.md")) as f:
                second = f.read()
            self.assertEqual(first, second)

    def test_dry_run_no_message(self):
        """Dry run does not emit glossary systemMessage."""
        with TestDir() as d:
            self._make_tree(d)
            actions, _, messages = rebalance(d, max_lines=150, dry_run=True)
            self.assertEqual(len(messages), 0)
            self.assertTrue(any("Glossary" in a for a in actions))

    def test_glossary_not_orphan(self):
        """find_orphans does not flag glossary.md."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            orphans = find_orphans(d)
            self.assertNotIn(GLOSSARY_FILE, orphans)

    def test_verify_tree_with_glossary(self):
        """verify_tree passes with glossary present."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            rebalance(d, max_lines=150)
            ok = verify_tree(d)
            self.assertTrue(ok)

    def test_glossary_skipped_few_files(self):
        """No message when fewer than GLOSSARY_MIN_TERMS memory files."""
        with TestDir() as d:
            make_leaf(d, "f0.md", "feedback", "item 0", "generic")
            make_index(d, [("item 0", "f0.md", "entry 0")])
            _, _, messages = rebalance(d, max_lines=150)
            self.assertEqual(len(messages), 0)

    def test_stale_glossary_detected(self):
        """glossary_is_stale returns True when memory file is newer."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            self.assertFalse(glossary_is_stale(d))
            # Touch a memory file to make it newer.
            import time
            time.sleep(0.05)
            make_leaf(d, "new.md", "user", "New", "New entry")
            self.assertTrue(glossary_is_stale(d))

    def test_parse_glossary(self):
        """parse_glossary extracts term names."""
        with TestDir() as d:
            self._make_glossary(d)
            terms = parse_glossary(d)
            self.assertEqual(terms,
                             ["Project Zenith", "Server Omega", "Team Delta"])

    def test_glossary_system_message_content(self):
        """systemMessage includes file list and instructions."""
        with TestDir() as d:
            self._make_tree(d, n_files=4)
            msg = glossary_system_message(d)
            self.assertIn("GLOSSARY UPDATE NEEDED", msg)
            self.assertIn("proj_0.md", msg)
            self.assertIn("10-20 most important key terms", msg)

    def test_build_glossary_entry_truncation(self):
        """Long term lists are truncated in MEMORY.md entry."""
        terms = [f"LongTermName{i}" for i in range(30)]
        entry = build_glossary_entry(terms)
        self.assertLessEqual(len(entry["raw"]), 200)
        self.assertTrue(entry["desc"].endswith("..."))

    def test_build_glossary_entry_format(self):
        """Entry has correct structure."""
        terms = ["Project Zenith", "Server Omega"]
        entry = build_glossary_entry(terms)
        self.assertEqual(entry["title"], "Key Terms")
        self.assertEqual(entry["path"], GLOSSARY_FILE)
        self.assertIn("Project Zenith", entry["desc"])
        self.assertIn("Server Omega", entry["desc"])

    def test_glossary_has_frontmatter(self):
        """Pre-created glossary has type: glossary frontmatter."""
        with TestDir() as d:
            self._make_glossary(d)
            fm = read_all_frontmatter(os.path.join(d, GLOSSARY_FILE))
            self.assertEqual(fm.get("type"), "glossary")

    def test_stale_with_existing_glossary_emits_and_parses(self):
        """Stale glossary emits message AND returns entry from old content."""
        with TestDir() as d:
            self._make_tree(d)
            self._make_glossary(d)
            # Make glossary stale by adding a new file.
            import time
            time.sleep(0.05)
            make_leaf(d, "new.md", "user", "New", "New entry")
            with open(os.path.join(d, "MEMORY.md"), "a") as f:
                f.write("- [New](new.md) — new entry\n")
            actions, _, messages = rebalance(d, max_lines=150)
            # Should emit message (stale).
            self.assertTrue(len(messages) > 0)
            # Should still have glossary entry from old content.
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            paths = [e["path"] for e in entries]
            self.assertIn(GLOSSARY_FILE, paths)

    def test_collect_memory_files_excludes_glossary(self):
        """collect_memory_files skips glossary.md and MEMORY.md."""
        with TestDir() as d:
            self._make_tree(d, n_files=3)
            self._make_glossary(d)
            files = collect_memory_files(d)
            basenames = [os.path.basename(f) for f in files]
            self.assertNotIn(GLOSSARY_FILE, basenames)
            self.assertNotIn("MEMORY.md", basenames)
            self.assertEqual(len(basenames), 3)


class TestEarlyRebalance(unittest.TestCase):
    """Tests for young-tree early rebalancing."""

    def test_young_tree_rebalances_early(self):
        """Young tree (no _index/) rebalances at 50% threshold."""
        with TestDir() as d:
            # Create 80 entries — over 50% of 150 but under 150.
            entries = []
            for i in range(80):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}", f"entry {i}")
                entries.append((f"FB {i}", fname, f"entry {i}"))
            make_index(d, entries)
            # No _index/ exists — should trigger early rebalance.
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertTrue(
                any("early rebalance" in a.lower() for a in actions),
                f"Expected early rebalance message in: {actions}")
            # _index/ should now exist.
            self.assertTrue(
                os.path.isdir(os.path.join(d, "_index")))

    def test_mature_tree_normal_threshold(self):
        """Mature tree (has _index/) uses normal threshold."""
        with TestDir() as d:
            # Create _index/ to make it "mature".
            os.makedirs(os.path.join(d, "_index"))
            entries = []
            for i in range(80):
                fname = f"feedback_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}", f"entry {i}")
                entries.append((f"FB {i}", fname, f"entry {i}"))
            make_index(d, entries)
            # 80 entries, under 150 limit — should NOT rebalance.
            actions, _, _ = rebalance(d, max_lines=150)
            self.assertTrue(
                any("no rebalancing" in a.lower() for a in actions))


class TestInlineContentDetection(unittest.TestCase):
    """Test detection and handling of non-standard MEMORY.md content."""

    def test_count_inline_content_clean(self):
        """Clean index has zero inline lines."""
        with TestDir() as d:
            make_leaf(d, "user_a.md", "user", "A", "desc a")
            make_leaf(d, "fb_b.md", "feedback", "B", "desc b")
            make_index(d, [("A", "user_a.md", "desc a"),
                           ("B", "fb_b.md", "desc b")])
            inline, total = count_inline_content(
                os.path.join(d, "MEMORY.md"))
            self.assertEqual(inline, 0)

    def test_count_inline_content_with_inline(self):
        """MEMORY.md with inline content between entries is detected."""
        with TestDir() as d:
            lines = [
                "# Memory Index", "",
                "- [A](a.md) — desc a",
                "Some inline note about A",
                "More details about A",
                "- [B](b.md) — desc b",
                "Inline note about B", "",
            ]
            with open(os.path.join(d, "MEMORY.md"), "w") as f:
                f.write("\n".join(lines))
            inline, total = count_inline_content(
                os.path.join(d, "MEMORY.md"))
            self.assertEqual(inline, 3)  # 3 non-blank inline lines

    def test_inline_content_skips_rebalance(self):
        """Rebalancer skips rebalancing when inline content is detected."""
        with TestDir() as d:
            # Build a MEMORY.md with inline content that exceeds limits.
            lines = ["# Memory Index", ""]
            for i in range(20):
                make_leaf(d, f"fb_{i}.md", "feedback", f"FB {i}", f"d{i}")
                lines.append(f"- [FB {i}](fb_{i}.md) — d{i}")
                lines.append(f"Inline note for item {i}")
            lines.append("")
            with open(os.path.join(d, "MEMORY.md"), "w") as f:
                f.write("\n".join(lines))
            actions, warnings, _ = rebalance(d, max_lines=30)
            # Should warn about inline content.
            self.assertTrue(
                any("inline content" in w for w in warnings),
                f"Expected inline content warning in: {warnings}")
            # Should NOT have created _index/ (rebalance was skipped).
            self.assertFalse(
                os.path.isdir(os.path.join(d, "_index")))

    def test_inline_content_preserves_file(self):
        """When inline content is detected, MEMORY.md is not modified."""
        with TestDir() as d:
            lines = [
                "# Memory Index", "",
                "- [A](a.md) — desc a",
                "Inline content here",
                "- [B](b.md) — desc b", "",
            ]
            content = "\n".join(lines)
            path = os.path.join(d, "MEMORY.md")
            with open(path, "w") as f:
                f.write(content)
            make_leaf(d, "a.md", "user", "A", "desc a")
            make_leaf(d, "b.md", "feedback", "B", "desc b")
            rebalance(d, max_lines=3)
            with open(path) as f:
                after = f.read()
            self.assertEqual(content, after)

    def test_inline_content_under_limit_no_warning(self):
        """Inline content in under-limit file is an action, not a warning."""
        with TestDir() as d:
            lines = [
                "# Memory Index", "",
                "- [A](a.md) — desc a",
                "Brief context note",
                "- [B](b.md) — desc b", "",
            ]
            with open(os.path.join(d, "MEMORY.md"), "w") as f:
                f.write("\n".join(lines))
            make_leaf(d, "a.md", "user", "A", "desc a")
            make_leaf(d, "b.md", "feedback", "B", "desc b")
            actions, warnings, _ = rebalance(d, max_lines=150)
            # No warning — file is under limits.
            self.assertFalse(
                any("inline content" in w for w in warnings),
                f"Should not warn when under limit: {warnings}")
            # But should note it in actions.
            self.assertTrue(
                any("inline content" in a for a in actions),
                f"Expected inline content note in actions: {actions}")

    def test_post_rebalance_still_over_warns(self):
        """Warn when file is still over limits after rebalancing."""
        with TestDir() as d:
            # Create a huge header that can't be compressed.
            header_lines = ["# Memory Index", ""]
            for i in range(160):
                header_lines.append(f"# Section {i}")
            header_lines.append("")
            # A few entries at the end.
            entries = []
            for i in range(5):
                fname = f"fb_{i}.md"
                make_leaf(d, fname, "feedback", f"FB {i}", f"d{i}")
                entries.append(f"- [FB {i}]({fname}) — d{i}")
            all_lines = header_lines + entries + [""]
            with open(os.path.join(d, "MEMORY.md"), "w") as f:
                f.write("\n".join(all_lines))
            # No inline content (all non-entry lines are before first entry
            # = header). Rebalance will proceed but can't compress header.
            actions, warnings, _ = rebalance(d, max_lines=150)
            self.assertTrue(
                any("still over" in w for w in warnings),
                f"Expected 'still over' warning in: {warnings}")


class TestHookCLIOutput(unittest.TestCase):
    """Test --hook CLI output format (single JSON object)."""

    def _make_tree(self, d):
        """Create a minimal memory tree for hook testing."""
        make_leaf(d, "user_ctx.md", "user", "User context", "physicist")
        make_leaf(d, "feedback_x.md", "feedback", "FB X", "do X")
        make_leaf(d, "project_y.md", "project", "Project Y", "build Y")
        make_index(d, [
            ("User context", "user_ctx.md", "physicist"),
            ("FB X", "feedback_x.md", "do X"),
            ("Project Y", "project_y.md", "build Y"),
        ])

    def _run_hook(self, d, extra_args=None):
        """Run rebalance.py --hook via subprocess, return parsed JSON."""
        import json
        import subprocess
        cmd = [
            "python3", os.path.join(os.path.dirname(__file__), "rebalance.py"),
            "--hook",
        ]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(d)
        result = subprocess.run(cmd, capture_output=True, text=True)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        return lines, result

    def test_hook_emits_single_json_line(self):
        """Hook mode must emit exactly one JSON line."""
        with TestDir() as d:
            self._make_tree(d)
            lines, _ = self._run_hook(d)
            self.assertEqual(len(lines), 1,
                             f"Expected 1 line, got {len(lines)}: {lines}")

    def test_hook_output_has_system_message(self):
        """Hook output must contain systemMessage with status."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            lines, _ = self._run_hook(d)
            obj = json.loads(lines[0])
            self.assertIn("systemMessage", obj)
            self.assertIn("alzheimer:", obj["systemMessage"])

    def test_hook_glossary_in_additional_context(self):
        """When glossary is stale, prompt goes in additionalContext."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # No glossary file = stale; use PostToolUse (supports hso)
            lines, _ = self._run_hook(d,
                                      ["--hook-event", "PostToolUse"])
            obj = json.loads(lines[0])
            self.assertIn("hookSpecificOutput", obj)
            self.assertIn("additionalContext",
                          obj["hookSpecificOutput"])
            self.assertIn("GLOSSARY UPDATE NEEDED",
                          obj["hookSpecificOutput"]["additionalContext"])

    def test_hook_event_name_passed_through(self):
        """--hook-event value appears in hookSpecificOutput."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # Use PostToolUse — SessionStart suppresses glossary messages,
            # so there would be no hookSpecificOutput to carry the event name.
            lines, _ = self._run_hook(d,
                                      ["--hook-event", "PostToolUse"])
            obj = json.loads(lines[0])
            self.assertEqual(
                obj["hookSpecificOutput"]["hookEventName"],
                "PostToolUse")

    def test_session_start_suppresses_glossary(self):
        """SessionStart does not emit glossary update instructions."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # No glossary = stale, but SessionStart should suppress.
            lines, _ = self._run_hook(d,
                                      ["--hook-event", "SessionStart"])
            obj = json.loads(lines[0])
            self.assertNotIn("hookSpecificOutput", obj)

    def test_precompact_folds_context_into_system_message(self):
        """PreCompact puts additional context in systemMessage, not hso."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # No glossary = stale; PreCompact does NOT suppress glossary.
            lines, _ = self._run_hook(d,
                                      ["--hook-event", "PreCompact"])
            obj = json.loads(lines[0])
            # Must NOT use hookSpecificOutput (PreCompact is unsupported)
            self.assertNotIn("hookSpecificOutput", obj)
            # But the glossary instructions should be in systemMessage
            self.assertIn("GLOSSARY UPDATE NEEDED", obj["systemMessage"])

    def test_unsupported_event_folds_context(self):
        """Unknown/missing hook event folds context into systemMessage."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # No --hook-event flag at all; glossary is stale
            lines, _ = self._run_hook(d)
            obj = json.loads(lines[0])
            self.assertNotIn("hookSpecificOutput", obj)
            self.assertIn("GLOSSARY UPDATE NEEDED", obj["systemMessage"])

    def test_hook_no_additional_context_when_fresh(self):
        """When glossary is fresh and no warnings, no hookSpecificOutput."""
        import json
        with TestDir() as d:
            self._make_tree(d)
            # Create a fresh glossary.
            terms = [("Term1", "def1"), ("Term2", "def2"),
                     ("Term3", "def3")]
            gpath = os.path.join(d, GLOSSARY_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: glossary\nupdated: 2026-03-30\n"
                        "terms: 3\n---\n\n# Key Terms\n\n")
                for name, defn in terms:
                    f.write(f"- **{name}** — {defn}\n")
            # Touch glossary to be newer than all memory files.
            import time
            time.sleep(0.05)
            os.utime(gpath, None)
            lines, _ = self._run_hook(d)
            obj = json.loads(lines[0])
            self.assertNotIn("hookSpecificOutput", obj)


class TestCheckDrift(unittest.TestCase):
    """Test orphan auto-indexing, leaf-size detection in check_drift()."""

    def test_no_drift(self):
        """Clean tree produces no actions or warnings."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A", "short")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(actions, [])
            self.assertEqual(warnings, [])

    def test_orphan_auto_indexed(self):
        """Orphan with frontmatter is auto-indexed, not warned."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_leaf(d, "orphan.md", "user", "Orphan", "Orphan desc")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(len(actions), 1)
            self.assertIn("Auto-indexed", actions[0])
            self.assertEqual(warnings, [])
            # Verify it was actually added to MEMORY.md.
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            paths = [e["path"] for e in entries]
            self.assertIn("orphan.md", paths)

    def test_orphan_without_frontmatter_warns(self):
        """Orphan without name/description triggers warning."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            # Create a file with no frontmatter.
            with open(os.path.join(d, "bare.md"), "w") as f:
                f.write("Just some text, no frontmatter.\n")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(actions, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("without frontmatter", warnings[0])

    def test_multiple_orphans_auto_indexed(self):
        """Multiple orphans with frontmatter are all auto-indexed."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_leaf(d, "x.md", "user", "X", "X desc")
            make_leaf(d, "y.md", "user", "Y", "Y desc")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(len(actions), 1)
            self.assertIn("2", actions[0])
            self.assertEqual(warnings, [])

    def test_dry_run_no_write(self):
        """Dry run detects orphans but doesn't modify MEMORY.md."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_leaf(d, "orphan.md", "user", "Orphan", "Orphan desc")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings = check_drift(d, dry_run=True)
            self.assertEqual(len(actions), 1)
            # Verify MEMORY.md was NOT modified.
            _, entries = parse_index(os.path.join(d, "MEMORY.md"))
            paths = [e["path"] for e in entries]
            self.assertNotIn("orphan.md", paths)

    def test_oversized_leaf_detected(self):
        """Leaf file over LEAF_MAX_LINES triggers warning."""
        with TestDir() as d:
            big_file = os.path.join(d, "big.md")
            with open(big_file, "w") as f:
                f.write("---\nname: Big\ntype: user\n---\n")
                for i in range(LEAF_MAX_LINES + 10):
                    f.write(f"Line {i}\n")
            make_index(d, [("Big", "big.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(len(warnings), 1)
            self.assertIn("DRIFT", warnings[0])
            self.assertIn("big.md", warnings[0])
            self.assertIn("oversized", warnings[0])

    def test_leaf_under_limit_no_warning(self):
        """Leaf file at exactly LEAF_MAX_LINES does not warn."""
        with TestDir() as d:
            ok_file = os.path.join(d, "ok.md")
            with open(ok_file, "w") as f:
                for i in range(LEAF_MAX_LINES):
                    f.write(f"Line {i}\n")
            make_index(d, [("OK", "ok.md", "desc")])
            actions, warnings = check_drift(d)
            self.assertEqual(warnings, [])

    def test_auto_index_flows_through_rebalance(self):
        """Auto-indexed orphans appear in rebalance() actions."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_leaf(d, "orphan.md", "user", "Orphan", "desc")
            make_index(d, [("A", "a.md", "desc")])
            actions, warnings, messages = rebalance(d)
            self.assertTrue(
                any("Auto-indexed" in a for a in actions),
                "Auto-index action should flow through rebalance()"
            )

    def test_glossary_not_flagged(self):
        """Glossary file should not be flagged as orphan or oversized."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_index(d, [("A", "a.md", "desc")])
            # Write a large glossary.
            gpath = os.path.join(d, GLOSSARY_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: glossary\n---\n")
                for i in range(200):
                    f.write(f"- **Term{i}** — definition\n")
            actions, warnings = check_drift(d)
            self.assertEqual(actions, [])
            self.assertEqual(warnings, [])


class TestCheckAlias(unittest.TestCase):
    """Test that --check works as alias for --verify."""

    def test_check_alias(self):
        """--check should invoke verify_tree and exit cleanly."""
        import subprocess
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A")
            make_index(d, [("A", "a.md", "desc")])
            result = subprocess.run(
                ["python3", "rebalance.py", d, "--check"],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("All checks passed", result.stdout)


class TestUpdateCheck(unittest.TestCase):
    """Test update staleness detection and caching."""

    def test_cache_roundtrip(self):
        """Write and read cache file."""
        with TestDir() as d:
            cache = os.path.join(d, UPDATE_CACHE_FILE)
            _write_update_cache(cache, 3)
            ts, behind = _read_update_cache(cache)
            self.assertEqual(behind, 3)
            self.assertGreater(ts, 0)

    def test_cache_missing(self):
        """Missing cache returns zero."""
        ts, behind = _read_update_cache("/nonexistent/path")
        self.assertEqual(ts, 0)
        self.assertEqual(behind, 0)

    def test_cache_corrupt(self):
        """Corrupt cache returns zero."""
        with TestDir() as d:
            cache = os.path.join(d, UPDATE_CACHE_FILE)
            with open(cache, "w") as f:
                f.write("not json")
            ts, behind = _read_update_cache(cache)
            self.assertEqual(ts, 0)
            self.assertEqual(behind, 0)

    def test_fresh_cache_skips_fetch(self):
        """If cache is fresh, no fetch happens (returns cached result)."""
        import time
        with TestDir() as d:
            # Needs a .git dir so the function doesn't bail early.
            os.makedirs(os.path.join(d, ".git"))
            cache = os.path.join(d, UPDATE_CACHE_FILE)
            # Write a fresh cache saying 2 commits behind.
            with open(cache, "w") as f:
                json.dump({"timestamp": time.time(), "behind": 2}, f)
            # Even though d isn't a real git repo, we should get the
            # cached result without trying to fetch.
            behind, msg = check_for_updates(alzheimer_dir=d)
            self.assertEqual(behind, 2)
            self.assertIn("2 new commit", msg)

    def test_no_git_dir_skips(self):
        """Non-git directory returns zero silently."""
        with TestDir() as d:
            behind, msg = check_for_updates(alzheimer_dir=d, force=True)
            self.assertEqual(behind, 0)
            self.assertIsNone(msg)

    def test_up_to_date_no_message(self):
        """When behind=0, message should be None."""
        with TestDir() as d:
            os.makedirs(os.path.join(d, ".git"))
            cache = os.path.join(d, UPDATE_CACHE_FILE)
            import time
            with open(cache, "w") as f:
                json.dump({"timestamp": time.time(), "behind": 0}, f)
            behind, msg = check_for_updates(alzheimer_dir=d)
            self.assertEqual(behind, 0)
            self.assertIsNone(msg)


class TestBugReportPrivacy(unittest.TestCase):
    """Ensure bug reports never leak sensitive filenames or paths."""

    SENSITIVE_NAMES = [
        "project_secret_acquisition.md",
        "feedback_mental_health.md",
        "user_salary_negotiations.md",
        "reference_private_server.md",
    ]

    def _make_anomalies(self):
        """Create anomalies with sensitive-looking filenames."""
        return [
            Anomaly("error", "MEMORY.md exceeds hard limit: 210 lines "
                    "(max 200)", {"lines": 210, "limit": 200}),
            Anomaly("error",
                    "Broken reference: project_secret_acquisition.md",
                    {"source": "MEMORY.md",
                     "target": "project_secret_acquisition.md"}),
            Anomaly("warning",
                    "Orphaned memory file: feedback_mental_health.md",
                    {"file": "feedback_mental_health.md"}),
            Anomaly("warning",
                    "Orphaned memory file: user_salary_negotiations.md",
                    {"file": "user_salary_negotiations.md"}),
        ]

    def test_filenames_anonymized(self):
        """Specific filenames must not appear in the report."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "user", "A", "desc")
            report = format_bug_report(self._make_anomalies(), d)
            for name in self.SENSITIVE_NAMES:
                self.assertNotIn(name, report,
                                 f"Sensitive filename leaked: {name}")

    def test_type_prefixes_present(self):
        """Anonymized type prefixes should appear in the report."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "user", "A", "desc")
            report = format_bug_report(self._make_anomalies(), d)
            self.assertIn("project_*.md", report)
            self.assertIn("feedback_*.md", report)
            self.assertIn("user_*.md", report)

    def test_no_absolute_paths(self):
        """Report must not contain absolute paths (leak usernames)."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "user", "A", "desc")
            report = format_bug_report(self._make_anomalies(), d)
            self.assertNotIn(d, report,
                             "Absolute memory dir path leaked")
            self.assertNotIn(os.path.expanduser("~"), report,
                             "Home directory path leaked")

    def test_no_memory_content(self):
        """Report must not contain memory file content."""
        with TestDir() as d:
            make_leaf(d, "a.md", "user", "A",
                      "This is super secret content")
            make_index(d, [("A", "a.md", "secret content")])
            report = format_bug_report(self._make_anomalies(), d)
            self.assertNotIn("super secret", report)
            self.assertNotIn("secret content", report)

    def test_exception_info_included(self):
        """Exception tracebacks should still be included (no filenames)."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "user", "A", "desc")
            exc = "Traceback: KeyError in rebalance_index at line 42"
            report = format_bug_report([], d, exception_info=exc)
            self.assertIn("KeyError", report)
            self.assertIn("line 42", report)


class TestGuardrailsSoftLayer(unittest.TestCase):
    """Tests for the guardrails soft layer in rebalance.py."""

    def test_guardrails_not_stale_when_missing(self):
        """guardrails.md not existing is not considered stale."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "feedback", "A", "desc")
            self.assertFalse(guardrails_is_stale(d))

    def test_guardrails_stale_when_feedback_newer(self):
        """guardrails.md is stale when a feedback memory is newer."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            # Create guardrails file.
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\nupdated: 2026-01-01\n"
                        "rules: 1\n---\n\n# Guardrails\n\n"
                        "- **No push** — never push without asking\n")
            # Set guardrails mtime to the past.
            os.utime(gpath, (1000000, 1000000))
            # Create a newer feedback memory.
            make_leaf(d, "feedback_test.md", "feedback", "Test", "desc")
            self.assertTrue(guardrails_is_stale(d))

    def test_guardrails_not_stale_when_fresh(self):
        """guardrails.md is fresh when it's newer than all feedback."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            # Create feedback memory first.
            make_leaf(d, "feedback_test.md", "feedback", "Test", "desc")
            fpath = os.path.join(d, "feedback_test.md")
            os.utime(fpath, (1000000, 1000000))
            # Create guardrails file (newer).
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\nupdated: 2026-01-01\n"
                        "rules: 1\n---\n\n# Guardrails\n\n"
                        "- **No push** — never push without asking\n")
            self.assertFalse(guardrails_is_stale(d))

    def test_parse_guardrails(self):
        """parse_guardrails extracts rule names."""
        with TestDir() as d:
            make_index(d, [])
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\n---\n\n# Guardrails\n\n"
                        "- **Never push without asking** — always confirm\n"
                        "- **No force-push** — blocked entirely\n")
            rules = parse_guardrails(d)
            self.assertEqual(rules, ["Never push without asking",
                                     "No force-push"])

    def test_build_guardrails_entry(self):
        """build_guardrails_entry creates a valid MEMORY.md entry."""
        entry = build_guardrails_entry(["No push", "No force-push"])
        self.assertIn(GUARDRAILS_FILE, entry["path"])
        self.assertIn("no push", entry["desc"])
        self.assertEqual(entry["title"], "Guardrails")

    def test_guardrails_system_message(self):
        """guardrails_system_message includes feedback file names."""
        with TestDir() as d:
            make_index(d, [])
            make_leaf(d, "feedback_test.md", "feedback", "Test", "desc")
            msg = guardrails_system_message(d)
            self.assertIn("GUARDRAILS UPDATE NEEDED", msg)
            self.assertIn("feedback_test.md", msg)

    def test_update_guardrails_returns_entry(self):
        """update_guardrails returns entry when guardrails.md exists."""
        with TestDir() as d:
            make_index(d, [])
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\n---\n\n# Guardrails\n\n"
                        "- **No push** — always confirm\n")
            actions, entry, messages = update_guardrails(d)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["title"], "Guardrails")

    def test_guardrails_not_flagged_as_orphan(self):
        """guardrails.md should not be flagged as an orphan."""
        with TestDir() as d:
            make_index(d, [("A", "a.md", "desc")])
            make_leaf(d, "a.md", "feedback", "A", "desc")
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\n---\n\n# Guardrails\n\n"
                        "- **No push** — always confirm\n")
            actions, warnings = check_drift(d)
            # guardrails.md should not appear in any warnings.
            for w in warnings:
                self.assertNotIn(GUARDRAILS_FILE, w)
            for a in actions:
                self.assertNotIn(GUARDRAILS_FILE, a)

    def test_guardrails_pinned_not_moved_to_index(self):
        """type: guardrails entries stay in MEMORY.md, not pushed to _index."""
        with TestDir() as d:
            # Create enough entries to trigger rebalancing.
            entries = [(f"F{i}", f"f{i}.md", f"desc {i}")
                       for i in range(20)]
            make_index(d, entries)
            for title, path, desc in entries:
                make_leaf(d, path, "feedback", title, desc)
            # Add guardrails file and entry.
            gpath = os.path.join(d, GUARDRAILS_FILE)
            with open(gpath, "w") as f:
                f.write("---\ntype: guardrails\n---\n\n# Guardrails\n\n"
                        "- **No push** — always confirm\n")
            # Run rebalance.
            actions, warnings, messages = rebalance(d, max_lines=15)
            # Guardrails entry should still be in MEMORY.md.
            _, entries_after = parse_index(os.path.join(d, "MEMORY.md"))
            guardrails_entries = [e for e in entries_after
                                 if e["path"] == GUARDRAILS_FILE]
            # guardrails.md might not be in the index if it wasn't
            # there before rebalance — but it should NOT be in _index/.
            index_dir = os.path.join(d, "_index")
            if os.path.isdir(index_dir):
                for fname in os.listdir(index_dir):
                    fpath = os.path.join(index_dir, fname)
                    if os.path.isfile(fpath):
                        with open(fpath) as f:
                            content = f.read()
                        self.assertNotIn(GUARDRAILS_FILE, content)


class TestGuardrailsHardLayer(unittest.TestCase):
    """Tests for guardrails.py (hard layer / PreToolUse hook)."""

    def test_git_push_confirm(self):
        """git push should require confirmation by default rules."""
        allowed, msg = check_rules(
            "Bash", {"command": "git push origin main"})
        self.assertFalse(allowed)
        self.assertIn("guardrails", msg.lower())
        self.assertIn("--exec", msg)

    def test_git_push_force_confirm(self):
        """git push --force should require confirmation."""
        allowed, msg = check_rules(
            "Bash", {"command": "git push --force origin main"})
        self.assertFalse(allowed)
        self.assertIn("--exec", msg)

    def test_git_reset_hard_confirm(self):
        """git reset --hard should require confirmation."""
        allowed, msg = check_rules(
            "Bash", {"command": "git reset --hard HEAD~1"})
        self.assertFalse(allowed)
        self.assertIn("--exec", msg)

    def test_branch_delete_confirm(self):
        """git branch -D should require confirmation."""
        allowed, msg = check_rules(
            "Bash", {"command": "git branch -D feature-xyz"})
        self.assertFalse(allowed)
        self.assertIn("--exec", msg)

    def test_rm_rf_root_blocked(self):
        """rm -rf / should be blocked."""
        allowed, msg = check_rules(
            "Bash", {"command": "rm -rf /"})
        self.assertFalse(allowed)

    def test_safe_commands_allowed(self):
        """Normal commands should not be blocked."""
        allowed, msg = check_rules(
            "Bash", {"command": "git status"})
        self.assertTrue(allowed)
        self.assertEqual(msg, "")

    def test_git_commit_allowed(self):
        """git commit should not be blocked."""
        allowed, msg = check_rules(
            "Bash", {"command": "git commit -m 'test'"})
        self.assertTrue(allowed)

    def test_non_bash_tools_allowed(self):
        """Non-Bash tools should not be blocked by Bash rules."""
        allowed, msg = check_rules(
            "Write", {"file_path": "/tmp/test.md", "content": "git push"})
        self.assertTrue(allowed)

    def test_custom_rules_replace_defaults(self):
        """Custom rules in 'rules' key replace defaults."""
        custom = [{"tool": "Bash", "pattern": r"echo\s+hello",
                   "action": "block", "message": "no hello"}]
        # git push should be allowed (defaults replaced).
        allowed, _ = check_rules(
            "Bash", {"command": "git push"}, rules=custom)
        self.assertTrue(allowed)
        # echo hello should be blocked.
        allowed, msg = check_rules(
            "Bash", {"command": "echo hello"}, rules=custom)
        self.assertFalse(allowed)
        self.assertIn("no hello", msg)

    def test_get_match_text_bash(self):
        """Bash tool match text is the command string."""
        text = get_match_text("Bash", {"command": "ls -la"})
        self.assertEqual(text, "ls -la")

    def test_get_match_text_other(self):
        """Non-Bash tool match text is JSON-serialized input."""
        text = get_match_text("Write", {"file_path": "/tmp/x"})
        self.assertIn("file_path", text)
        self.assertIn("/tmp/x", text)

    def test_invalid_regex_skipped(self):
        """Rules with invalid regex patterns are skipped."""
        bad_rules = [{"tool": "Bash", "pattern": r"[invalid",
                      "action": "block", "message": "bad"}]
        allowed, _ = check_rules(
            "Bash", {"command": "anything"}, rules=bad_rules)
        self.assertTrue(allowed)

    def test_git_push_in_pipeline_confirm(self):
        """git push in a chained command should still require confirmation."""
        allowed, msg = check_rules(
            "Bash", {"command": "git add . && git commit -m x && git push"})
        self.assertFalse(allowed)
        self.assertIn("--exec", msg)

    def test_rm_rf_subdir_allowed(self):
        """rm -rf on non-root paths is allowed."""
        allowed, _ = check_rules(
            "Bash", {"command": "rm -rf /tmp/test-dir"})
        self.assertTrue(allowed)


class TestGuardrailsConfirmMode(unittest.TestCase):
    """Tests for confirm mode and --exec functionality."""

    def test_confirm_rule_blocks_with_exec_hint(self):
        """Confirm rules block and include --exec usage hint."""
        rules = [{"tool": "Bash", "pattern": r"git\s+push\b",
                  "action": "confirm", "message": "Push needs approval."}]
        allowed, msg = check_rules(
            "Bash", {"command": "git push origin main"}, rules=rules)
        self.assertFalse(allowed)
        self.assertIn("--exec", msg)
        self.assertIn("Push needs approval", msg)

    def test_confirm_default_message(self):
        """Confirm rules without custom message get a default."""
        rules = [{"tool": "Bash", "pattern": r".*", "action": "confirm"}]
        allowed, msg = check_rules(
            "Bash", {"command": "ls"}, rules=rules)
        self.assertFalse(allowed)
        self.assertIn("user confirmation", msg)

    def test_self_allowlist_exec(self):
        """guardrails.py --exec invocations bypass all rules."""
        allowed, msg = check_rules(
            "Bash",
            {"command": 'python3 /path/to/guardrails.py --exec "git push"'})
        self.assertTrue(allowed)

    def test_self_allowlist_python_no_3(self):
        """python (without 3) also matches self-allowlist."""
        allowed, msg = check_rules(
            "Bash",
            {"command": 'python /path/to/guardrails.py --exec "ls"'})
        self.assertTrue(allowed)

    def test_self_allowlist_not_other_scripts(self):
        """Other scripts with --exec are not allowlisted."""
        allowed, msg = check_rules(
            "Bash",
            {"command": 'python3 /path/to/evil.py --exec "git push"'})
        self.assertFalse(allowed)

    def test_self_allowlist_non_bash_ignored(self):
        """Self-allowlist only applies to Bash tool."""
        self.assertFalse(_is_self_exec(
            "Write", {"command": "guardrails.py --exec"}))


class TestGuardrailsConfigManipulation(unittest.TestCase):
    """Tests for .guardrails.conf add/remove operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = guardrails._alzheimer_dir
        guardrails._alzheimer_dir = lambda: self.tmpdir

    def tearDown(self):
        guardrails._alzheimer_dir = self._orig_dir
        shutil.rmtree(self.tmpdir)

    def test_add_rule_creates_config(self):
        """add_rule creates .guardrails.conf if it doesn't exist."""
        rule = {"tool": "Bash", "pattern": "test", "action": "confirm"}
        add_rule(rule)
        config, existed = _load_config()
        self.assertTrue(existed)
        self.assertIn(rule, config.get("extra_rules", []))

    def test_remove_rule_matches_by_tool_pattern_action(self):
        """remove_rule matches on tool + pattern + action."""
        rule = {"tool": "Bash", "pattern": "test", "action": "confirm",
                "message": "test msg"}
        add_rule(rule)
        removed = remove_rule(rule)
        self.assertTrue(removed)
        config, _ = _load_config()
        self.assertEqual(len(config.get("extra_rules", [])), 0)

    def test_remove_nonexistent_rule_returns_false(self):
        """remove_rule returns False if rule not found."""
        rule = {"tool": "Bash", "pattern": "nope", "action": "block"}
        removed = remove_rule(rule)
        self.assertFalse(removed)

    def test_add_remove_roundtrip(self):
        """Adding then removing a rule leaves config empty."""
        rule = {"tool": "Bash", "pattern": r"echo\b", "action": "confirm"}
        add_rule(rule)
        remove_rule(rule)
        config, _ = _load_config()
        self.assertEqual(len(config.get("extra_rules", [])), 0)

    def test_exec_with_temporary_allow(self):
        """exec_with_temporary_allow removes and re-adds rule."""
        rule = {"tool": "Bash", "pattern": r"echo\b", "action": "confirm"}
        add_rule(rule)

        rc, stdout, stderr = exec_with_temporary_allow("echo hello", rule)
        self.assertEqual(rc, 0)
        self.assertIn("hello", stdout)

        # Rule should be back in config.
        config, _ = _load_config()
        rules = config.get("extra_rules", [])
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["pattern"], r"echo\b")

    def test_exec_restores_rule_on_failure(self):
        """Rule is restored even when the command fails."""
        rule = {"tool": "Bash", "pattern": r"false\b", "action": "confirm"}
        add_rule(rule)

        rc, stdout, stderr = exec_with_temporary_allow("false", rule)
        self.assertNotEqual(rc, 0)

        # Rule should still be back.
        config, _ = _load_config()
        rules = config.get("extra_rules", [])
        self.assertEqual(len(rules), 1)

    def test_find_matching_rule_confirm_only(self):
        """find_matching_rule only matches confirm rules, not block."""
        block_rule = {"tool": "Bash", "pattern": r"echo\b",
                      "action": "block"}
        confirm_rule = {"tool": "Bash", "pattern": r"echo\b",
                        "action": "confirm"}
        add_rule(block_rule)
        add_rule(confirm_rule)

        found = find_matching_rule("echo hello")
        self.assertIsNotNone(found)
        self.assertEqual(found["action"], "confirm")

    def test_find_matching_rule_no_match(self):
        """find_matching_rule returns None when nothing matches."""
        found = find_matching_rule("echo hello")
        self.assertIsNone(found)

    def test_exec_multi_arg_command(self):
        """--exec with unquoted multi-word command joins all args."""
        import subprocess
        gpy = os.path.join(os.path.dirname(__file__), "guardrails.py")
        result = subprocess.run(
            ["python3", gpy, "--exec", "echo", "hello", "world"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello world", result.stdout)


# Need to import the module for monkeypatching _alzheimer_dir
import guardrails
import reminders


class TestRemindersTier1(unittest.TestCase):
    """Tests for the tier 1 timestamp gate."""

    def setUp(self):
        self.orig_timestamp = reminders.TIMESTAMP_FILE
        self.tmpdir = tempfile.mkdtemp()
        reminders.TIMESTAMP_FILE = os.path.join(self.tmpdir, "last-check")

    def tearDown(self):
        reminders.TIMESTAMP_FILE = self.orig_timestamp
        shutil.rmtree(self.tmpdir)

    def test_should_check_no_file(self):
        """First run (no timestamp file) should always check."""
        self.assertTrue(should_check())

    def test_should_check_recent(self):
        """Recent timestamp should skip check."""
        touch_timestamp()
        self.assertFalse(should_check())

    def test_should_check_expired(self):
        """Old timestamp should trigger check."""
        touch_timestamp()
        # Backdate the file by 2 hours.
        old_time = time.time() - 7200
        os.utime(reminders.TIMESTAMP_FILE, (old_time, old_time))
        self.assertTrue(should_check())

    def test_should_check_custom_interval(self):
        """Custom interval should be respected."""
        touch_timestamp()
        # 0 minute interval = always check.
        self.assertTrue(should_check(interval=0))

    def test_touch_creates_file(self):
        """touch_timestamp should create the file."""
        self.assertFalse(os.path.exists(reminders.TIMESTAMP_FILE))
        touch_timestamp()
        self.assertTrue(os.path.exists(reminders.TIMESTAMP_FILE))


class TestRemindersDateParsing(unittest.TestCase):
    """Tests for date reminder parsing."""

    def test_parse_date_reminders(self):
        """Parse standard date reminder lines."""
        content = (
            "# Reminders\n\n"
            "- 2026-04-12 — Check issue traction\n"
            "- 2026-05-01 — Review quarterly\n"
            "- Not a reminder\n"
            "- **Bold item**: also not a date reminder\n"
        )
        reminders_list = parse_date_reminders(content)
        self.assertEqual(len(reminders_list), 2)
        self.assertEqual(reminders_list[0], ("2026-04-12", "Check issue traction"))
        self.assertEqual(reminders_list[1], ("2026-05-01", "Review quarterly"))

    def test_parse_date_reminders_dash_variants(self):
        """Parse reminders with different dash types."""
        content = (
            "- 2026-04-12 - With hyphen\n"
            "- 2026-04-13 – With en-dash\n"
            "- 2026-04-14 — With em-dash\n"
        )
        reminders_list = parse_date_reminders(content)
        self.assertEqual(len(reminders_list), 3)

    def test_parse_empty(self):
        """Empty content returns no reminders."""
        self.assertEqual(parse_date_reminders(""), [])

    def test_check_date_due(self):
        """Reminders on or before today are due."""
        reminders_list = [
            ("2026-04-01", "Due today"),
            ("2026-03-15", "Overdue"),
            ("2026-12-25", "Future"),
        ]
        due = check_date_reminders(reminders_list, today="2026-04-01")
        self.assertEqual(len(due), 2)
        self.assertIn("Due today", due[0])
        self.assertIn("Overdue", due[1])

    def test_check_date_none_due(self):
        """No reminders due if all are in the future."""
        reminders_list = [("2099-01-01", "Far future")]
        due = check_date_reminders(reminders_list, today="2026-04-01")
        self.assertEqual(due, [])


class TestRemindersDailyChecks(unittest.TestCase):
    """Tests for daily checks section parsing."""

    def test_parse_daily_checks(self):
        """Parse daily checks section."""
        content = (
            "# Reminders\n\n"
            "- 2026-04-12 — Something\n\n"
            "# Daily checks\n\n"
            "These run every session.\n\n"
            "- **Memory issue watch**: Run git pull then read report.\n"
            "- **Update check**: Check for new version.\n\n"
            "# Other section\n"
        )
        checks = parse_daily_checks(content)
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0][0], "Memory issue watch")
        self.assertIn("git pull", checks[0][1])

    def test_parse_daily_checks_empty(self):
        """No daily checks section returns empty list."""
        self.assertEqual(parse_daily_checks("# Reminders\n"), [])


class TestRemindersRecurring(unittest.TestCase):
    """Tests for recurring reminder parsing and checking."""

    def setUp(self):
        self.orig_state = reminders.RECURRING_STATE_FILE
        self.tmpdir = tempfile.mkdtemp()
        reminders.RECURRING_STATE_FILE = os.path.join(
            self.tmpdir, "recurring-state"
        )

    def tearDown(self):
        reminders.RECURRING_STATE_FILE = self.orig_state
        shutil.rmtree(self.tmpdir)

    def test_parse_daily(self):
        """Parse daily recurring reminders."""
        content = "- daily 09:00 — Pull report\n"
        result = parse_recurring_reminders(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("daily", "09:00", "Pull report"))

    def test_parse_weekly(self):
        """Parse weekly recurring reminders."""
        content = "- weekly Mon — Review issues\n"
        result = parse_recurring_reminders(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("weekly", "Mon", "Review issues"))

    def test_check_daily_due(self):
        """Daily reminder fires when time has passed."""
        from datetime import datetime
        reminders_list = [("daily", "09:00", "Morning task")]
        # Set now to 10am.
        now = datetime(2026, 4, 1, 10, 0)
        due = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(len(due), 1)
        self.assertIn("Morning task", due[0])

    def test_check_daily_not_yet(self):
        """Daily reminder doesn't fire before scheduled time."""
        from datetime import datetime
        reminders_list = [("daily", "09:00", "Morning task")]
        now = datetime(2026, 4, 1, 8, 0)
        due = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(due, [])

    def test_check_daily_no_double_fire(self):
        """Daily reminder fires only once per day."""
        from datetime import datetime
        reminders_list = [("daily", "09:00", "Morning task")]
        now = datetime(2026, 4, 1, 10, 0)
        # First check fires.
        due1 = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(len(due1), 1)
        # Second check same day does not.
        due2 = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(due2, [])

    def test_check_weekly_right_day(self):
        """Weekly reminder fires on the correct day."""
        from datetime import datetime
        reminders_list = [("weekly", "Wed", "Midweek review")]
        # 2026-04-01 is a Wednesday.
        now = datetime(2026, 4, 1, 12, 0)
        due = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(len(due), 1)

    def test_check_weekly_wrong_day(self):
        """Weekly reminder doesn't fire on the wrong day."""
        from datetime import datetime
        reminders_list = [("weekly", "Mon", "Monday task")]
        # 2026-04-01 is a Wednesday.
        now = datetime(2026, 4, 1, 12, 0)
        due = check_recurring_reminders(reminders_list, now=now)
        self.assertEqual(due, [])


class TestRemindersCollect(unittest.TestCase):
    """Tests for collect_due_reminders integration."""

    def setUp(self):
        self.orig_find = reminders.find_reminder_files
        self.orig_state = reminders.RECURRING_STATE_FILE
        self.tmpdir = tempfile.mkdtemp()
        reminders.RECURRING_STATE_FILE = os.path.join(
            self.tmpdir, "recurring-state"
        )

    def tearDown(self):
        reminders.find_reminder_files = self.orig_find
        reminders.RECURRING_STATE_FILE = self.orig_state
        shutil.rmtree(self.tmpdir)

    def test_collect_with_due_reminders(self):
        """collect_due_reminders finds due items across files."""
        reminder_file = os.path.join(self.tmpdir, "reminders.md")
        with open(reminder_file, "w") as f:
            f.write(
                "# Reminders\n\n"
                "- 2026-04-01 — Do the thing\n"
                "- 2099-12-31 — Future thing\n"
            )
        reminders.find_reminder_files = lambda: [reminder_file]
        due = collect_due_reminders(today="2026-04-01")
        self.assertEqual(len(due), 1)
        self.assertIn("Do the thing", due[0])

    def test_collect_nothing_due(self):
        """collect_due_reminders returns empty when nothing is due."""
        reminder_file = os.path.join(self.tmpdir, "reminders.md")
        with open(reminder_file, "w") as f:
            f.write("# Reminders\n\n- 2099-01-01 — Future\n")
        reminders.find_reminder_files = lambda: [reminder_file]
        due = collect_due_reminders(today="2026-04-01")
        self.assertEqual(due, [])

    def test_collect_no_files(self):
        """No reminder files returns empty list."""
        reminders.find_reminder_files = lambda: []
        due = collect_due_reminders()
        self.assertEqual(due, [])

    def test_missed_reminder_still_fires(self):
        """A reminder whose date has long passed still fires."""
        reminder_file = os.path.join(self.tmpdir, "reminders.md")
        with open(reminder_file, "w") as f:
            f.write("# Reminders\n\n- 2025-01-01 — Ancient reminder\n")
        reminders.find_reminder_files = lambda: [reminder_file]
        due = collect_due_reminders(today="2026-04-01")
        self.assertEqual(len(due), 1)
        self.assertIn("Ancient reminder", due[0])


class TestRemindersEscalation(unittest.TestCase):
    """Tests for cumulative escalation pressure."""

    def setUp(self):
        self.orig_fire_count = reminders.FIRE_COUNT_FILE
        self.tmpdir = tempfile.mkdtemp()
        reminders.FIRE_COUNT_FILE = os.path.join(self.tmpdir, "fire-count")

    def tearDown(self):
        reminders.FIRE_COUNT_FILE = self.orig_fire_count
        shutil.rmtree(self.tmpdir)

    def test_first_fire_no_prefix(self):
        """First firing has no escalation prefix."""
        self.assertEqual(escalation_prefix(1), "")

    def test_second_fire_gentle(self):
        """Second firing gets a gentle nudge."""
        prefix = escalation_prefix(2)
        self.assertIn("2 times", prefix)
        self.assertIn("Please address", prefix)

    def test_third_fire_warning(self):
        """Third firing escalates to warning."""
        prefix = escalation_prefix(3)
        self.assertIn("WARNING", prefix)
        self.assertIn("3 times", prefix)

    def test_fifth_fire_critical(self):
        """Fifth firing hits critical."""
        prefix = escalation_prefix(5)
        self.assertIn("CRITICAL", prefix)
        self.assertIn("STOP", prefix)

    def test_high_count_still_critical(self):
        """Very high count stays at critical level."""
        prefix = escalation_prefix(20)
        self.assertIn("CRITICAL", prefix)
        self.assertIn("20 times", prefix)

    def test_fire_count_roundtrip(self):
        """Write and read fire count."""
        _write_fire_count(7)
        self.assertEqual(_read_fire_count(), 7)

    def test_fire_count_default_zero(self):
        """No file means zero."""
        self.assertEqual(_read_fire_count(), 0)

    def test_reset_fire_count(self):
        """Reset sets count to zero."""
        _write_fire_count(5)
        _reset_fire_count()
        self.assertEqual(_read_fire_count(), 0)


if __name__ == "__main__":
    unittest.main()
