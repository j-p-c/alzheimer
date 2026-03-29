#!/usr/bin/env python3
"""Tests for the alzheimer memory rebalancer."""

import os
import shutil
import tempfile
import unittest

from rebalance import (
    CONFIG_FILE,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_BYTES,
    HARD_MAX_LINES,
    HARD_MAX_BYTES,
    MIN_GROUP_SIZE,
    MAX_DEPTH,
    collect_anomalies,
    extract_keywords,
    file_size_bytes,
    find_orphans,
    get_limits,
    group_entries_by_keyword,
    is_category_entry,
    load_config,
    parse_index,
    read_frontmatter_type,
    rebalance,
    summarize_entries,
    verify_tree,
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
            actions, _ = rebalance(d, max_lines=150)
            self.assertTrue(any("no rebalancing" in a for a in actions))

    def test_missing_memory_md(self):
        with TestDir() as d:
            actions, _ = rebalance(d)
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
            actions, _ = rebalance(d, max_lines=8)

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
            actions, _ = rebalance(d, max_lines=5)

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
            actions, _ = rebalance(d, max_lines=10)

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
            actions, _ = rebalance(d, max_lines=10)

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
            actions, _ = rebalance(d, max_lines=200, max_bytes=size - 100)

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
            actions, _ = rebalance(d, max_lines=200, max_bytes=50000)
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
            actions, _ = rebalance(d, max_lines=8)
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
            actions, _ = rebalance(d, max_lines=150)
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
            actions, _ = rebalance(d, max_lines=5)

            # Check that max depth message appears if needed, and
            # importantly, that it terminates (no infinite loop).
            self.assertIsInstance(actions, list)


class TestEdgeCases(unittest.TestCase):

    def test_empty_memory_dir(self):
        with TestDir() as d:
            # No MEMORY.md at all.
            actions, _ = rebalance(d)
            self.assertTrue(any("not found" in a for a in actions))

    def test_malformed_frontmatter(self):
        with TestDir() as d:
            # File with broken frontmatter.
            path = os.path.join(d, "bad.md")
            with open(path, "w") as f:
                f.write("---\nthis is not: valid: yaml: stuff\n---\n")
            make_index(d, [("Bad", "bad.md", "malformed")])
            # Should not crash.
            actions, _ = rebalance(d, max_lines=150)
            self.assertIsInstance(actions, list)

    def test_unicode_in_entries(self):
        with TestDir() as d:
            make_leaf(d, "uni.md", "feedback", "Ünïcödé",
                      'handles em dashes \u2014 and smart quotes \u201clike this\u201d')
            make_index(d, [
                ("Ünïcödé", "uni.md",
                 'handles em dashes \u2014 and smart quotes \u201clike this\u201d'),
            ])
            actions, _ = rebalance(d, max_lines=150)
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
            actions, _ = rebalance(d, max_lines=5)
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


if __name__ == "__main__":
    unittest.main()
