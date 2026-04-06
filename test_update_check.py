"""Tests for check_for_updates() cache behaviour."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from rebalance import check_for_updates, _read_update_cache, _write_update_cache


class TestCheckForUpdates(unittest.TestCase):
    """Verify cache poisoning prevention and HEAD invalidation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        self.cache_path = os.path.join(self.tmpdir, ".alzheimer.lastcheck")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_cache(self, behind, timestamp=None, emitted_at=0, head=""):
        ts = timestamp if timestamp is not None else time.time()
        with open(self.cache_path, "w") as f:
            json.dump({"timestamp": ts, "behind": behind,
                        "emitted_at": emitted_at, "head": head}, f)

    def _read_cache(self):
        if not os.path.exists(self.cache_path):
            return None
        with open(self.cache_path) as f:
            return json.load(f)

    def _head_result(self, sha="abc123"):
        return MagicMock(returncode=0, stdout=sha + "\n", stderr="")

    @patch("rebalance.subprocess.run")
    def test_fetch_failure_does_not_poison_cache(self, mock_run):
        """When git fetch fails, the cache must not be overwritten."""
        self._write_cache(behind=2, timestamp=0)

        mock_run.side_effect = [
            self._head_result(),
            MagicMock(returncode=1, stdout="", stderr=""),  # fetch fails
        ]

        behind, msg = check_for_updates(self.tmpdir, force=True)

        self.assertEqual(behind, 0)
        self.assertIsNone(msg)
        cache = self._read_cache()
        self.assertEqual(cache["behind"], 2)

    @patch("rebalance.subprocess.run")
    def test_fetch_timeout_does_not_poison_cache(self, mock_run):
        """When git fetch times out, the cache must not be overwritten."""
        self._write_cache(behind=5, timestamp=0)

        mock_run.side_effect = [
            self._head_result(),
            subprocess.TimeoutExpired(cmd="git", timeout=10),
        ]

        behind, msg = check_for_updates(self.tmpdir, force=True)

        self.assertEqual(behind, 0)
        self.assertIsNone(msg)
        cache = self._read_cache()
        self.assertEqual(cache["behind"], 5)

    @patch("rebalance.subprocess.run")
    def test_fetch_success_behind_updates_cache(self, mock_run):
        """Successful fetch with commits behind updates the cache."""
        mock_run.side_effect = [
            self._head_result("def456"),
            MagicMock(returncode=0, stdout="", stderr=""),   # fetch
            MagicMock(returncode=0, stdout="3\n", stderr=""),  # rev-list
        ]

        behind, msg = check_for_updates(self.tmpdir, force=True)

        self.assertEqual(behind, 3)
        self.assertIn("3 new commit(s)", msg)
        cache = self._read_cache()
        self.assertEqual(cache["behind"], 3)
        self.assertEqual(cache["head"], "def456")

    @patch("rebalance.subprocess.run")
    def test_fetch_success_up_to_date(self, mock_run):
        """Successful fetch with 0 behind writes 0 to cache."""
        mock_run.side_effect = [
            self._head_result("def456"),
            MagicMock(returncode=0, stdout="", stderr=""),   # fetch
            MagicMock(returncode=0, stdout="0\n", stderr=""),  # rev-list
        ]

        behind, msg = check_for_updates(self.tmpdir, force=True)

        self.assertEqual(behind, 0)
        self.assertIsNone(msg)
        cache = self._read_cache()
        self.assertEqual(cache["behind"], 0)
        self.assertEqual(cache["head"], "def456")

    @patch("rebalance.subprocess.run")
    def test_no_cache_exists_fetch_fails_no_cache_created(self, mock_run):
        """If no cache exists and fetch fails, no cache file is created."""
        self.assertFalse(os.path.exists(self.cache_path))

        mock_run.side_effect = [
            self._head_result(),
            MagicMock(returncode=1, stdout="", stderr=""),  # fetch fails
        ]

        behind, msg = check_for_updates(self.tmpdir, force=True)

        self.assertEqual(behind, 0)
        self.assertIsNone(msg)
        self.assertFalse(os.path.exists(self.cache_path))

    @patch("rebalance.subprocess.run")
    def test_head_changed_invalidates_cache(self, mock_run):
        """If HEAD moved since cache was written, re-check even if fresh."""
        # Cache: behind=1, fresh timestamp, but HEAD was "old_hash".
        self._write_cache(behind=1, timestamp=time.time(), head="old_hash")

        # Current HEAD is different (user pulled manually).
        mock_run.side_effect = [
            self._head_result("new_hash"),
            MagicMock(returncode=0, stdout="", stderr=""),   # fetch
            MagicMock(returncode=0, stdout="0\n", stderr=""),  # rev-list: now up to date
        ]

        # NOT force=True — tests the cache invalidation path.
        behind, msg = check_for_updates(self.tmpdir)

        self.assertEqual(behind, 0)
        self.assertIsNone(msg)
        cache = self._read_cache()
        self.assertEqual(cache["behind"], 0)
        self.assertEqual(cache["head"], "new_hash")


if __name__ == "__main__":
    unittest.main()
