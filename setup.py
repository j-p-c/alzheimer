#!/usr/bin/env python3
"""
setup.py — Generate Claude Code hook configuration for the alzheimer
rebalancer, with paths appropriate for this machine.

Usage:
    python3 setup.py              # Print hook JSON to paste into settings
    python3 setup.py --install    # Merge hooks into ~/.claude/settings.json
    python3 setup.py --check      # Verify hooks are installed correctly
"""

import glob
import json
import os
import re
import sys


def get_rebalancer_path():
    """Return the absolute path to rebalance.py."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "rebalance.py")


def generate_hooks(rebalancer_path):
    """Generate the hook configuration dict."""
    # Escape for JSON embedding in shell commands.
    rp = rebalancer_path.replace('"', '\\"')

    return {
        "PostToolUse": [{
            "matcher": "Write|Edit",
            "hooks": [{
                "type": "command",
                "command": (
                    f"jq -r '(.tool_input.file_path // "
                    f".tool_response.filePath) // empty' | "
                    f'{{ read -r f; echo "$f" | grep -q \'/memory/\' '
                    f"&& python3 \"{rp}\" --hook "
                    f'"$(dirname "$f")" 2>&1 | head -5; }} || true'
                ),
                "timeout": 15,
                "statusMessage": "Checking memory balance..."
            }]
        }],
        "SessionStart": [{
            "hooks": [{
                "type": "command",
                "command": (
                    f'for d in ~/.claude/projects/*/memory; do '
                    f'[ -f "$d/MEMORY.md" ] && '
                    f'python3 "{rp}" --hook "$d" 2>&1; done || true'
                ),
                "timeout": 15,
                "statusMessage": "Rebalancing memory tree..."
            }]
        }],
        "PreCompact": [{
            "hooks": [{
                "type": "command",
                "command": (
                    f'for d in ~/.claude/projects/*/memory; do '
                    f'[ -f "$d/MEMORY.md" ] && '
                    f'python3 "{rp}" --hook "$d" 2>&1; done || true'
                ),
                "timeout": 15,
                "statusMessage": "Rebalancing memory tree before compact..."
            }]
        }],
    }


def read_settings(settings_path):
    """Read existing settings, or return empty dict."""
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            return json.load(f)
    return {}


def merge_hooks(existing_hooks, new_hooks):
    """Merge new hooks into existing, avoiding duplicates.

    For each event, checks if an alzheimer hook already exists
    (by looking for 'rebalance.py' in the command). If so, replaces it.
    If not, appends it.
    """
    merged = dict(existing_hooks)

    for event, hook_groups in new_hooks.items():
        if event not in merged:
            merged[event] = hook_groups
            continue

        # Check if alzheimer hook already exists in this event.
        existing_groups = merged[event]
        alzheimer_idx = None
        for i, group in enumerate(existing_groups):
            for hook in group.get("hooks", []):
                if "rebalance.py" in hook.get("command", ""):
                    alzheimer_idx = i
                    break
            if alzheimer_idx is not None:
                break

        if alzheimer_idx is not None:
            # Replace existing alzheimer hook group.
            existing_groups[alzheimer_idx] = hook_groups[0]
        else:
            # Append new hook group.
            existing_groups.extend(hook_groups)

    return merged


def install_hooks(settings_path, hooks):
    """Merge alzheimer hooks into the settings file."""
    settings = read_settings(settings_path)
    existing_hooks = settings.get("hooks", {})
    settings["hooks"] = merge_hooks(existing_hooks, hooks)

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    return settings


def check_hooks(settings_path, rebalancer_path):
    """Verify that alzheimer hooks are installed and point to the
    correct rebalancer path."""
    if not os.path.exists(settings_path):
        print(f"FAIL: {settings_path} does not exist.")
        return False

    settings = read_settings(settings_path)
    hooks = settings.get("hooks", {})
    ok = True

    for event in ["PostToolUse", "SessionStart", "PreCompact"]:
        found = False
        for group in hooks.get(event, []):
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                if "rebalance.py" in cmd:
                    found = True
                    if rebalancer_path not in cmd:
                        print(f"WARN: {event} hook points to different "
                              f"rebalance.py path.")
                        print(f"  Expected: {rebalancer_path}")
        if found:
            print(f"  {event}: OK")
        else:
            print(f"  {event}: MISSING")
            ok = False

    return ok


def find_install_dir(settings_path):
    """Find where alzheimer is installed by reading settings.json.

    Parses hook commands for the path to rebalance.py and returns
    the containing directory, or None if not found.
    """
    if not os.path.exists(settings_path):
        return None
    try:
        settings = read_settings(settings_path)
        hooks = settings.get("hooks", {})
        for event, groups in hooks.items():
            for group in groups:
                for hook in group.get("hooks", []):
                    cmd = hook.get("command", "")
                    # Look for python3 "/.../rebalance.py" or
                    # python3 /.../rebalance.py in the command.
                    m = re.search(r'python3\s+"?([^"]+/rebalance\.py)"?',
                                  cmd)
                    if m:
                        rpath = m.group(1)
                        if os.path.exists(rpath):
                            return os.path.dirname(rpath)
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def seed_memory(memory_dir, alzheimer_dir):
    """Write a reference memory file so Claude knows what 'alzheimer' is.

    This survives compaction, ensuring the instance always recognises
    the name even long after the install conversation is gone.
    """
    memory_file = os.path.join(memory_dir, "reference_alzheimer.md")
    memory_md = os.path.join(memory_dir, "MEMORY.md")

    # Read version from rebalance.py.
    version = "unknown"
    rpy = os.path.join(alzheimer_dir, "rebalance.py")
    if os.path.exists(rpy):
        with open(rpy) as f:
            for line in f:
                m = re.match(r'^VERSION\s*=\s*"(.+)"', line)
                if m:
                    version = m.group(1)
                    break

    content = f"""---
name: Alzheimer memory rebalancer
description: "alzheimer" is the auto-memory rebalancer installed from github.com/j-p-c/alzheimer — how to update, diagnose, and report bugs
type: reference
---

**alzheimer** (v{version}) — self-balancing hierarchical memory tree for Claude Code.
Installed at: `{alzheimer_dir}`
GitHub repo: https://github.com/j-p-c/alzheimer

**Common commands** (run from any directory):
- Update to latest: `python3 "{alzheimer_dir}/setup.py" --update`
- Check hooks are correct: `python3 "{alzheimer_dir}/setup.py" --check`
- Diagnose issues: `python3 "{alzheimer_dir}/rebalance.py" <memory_dir> --diagnose`
- Find install location: `python3 "{alzheimer_dir}/setup.py" --find`

When the user says "update alzheimer" they mean: pull latest from GitHub and re-install hooks (i.e. run setup.py --update).
"""

    with open(memory_file, "w") as f:
        f.write(content)

    # Add or update the MEMORY.md index entry.
    index_line = ("- [Alzheimer memory rebalancer](reference_alzheimer.md) "
                  "— installed from github.com/j-p-c/alzheimer; "
                  "update, diagnose, report bugs")

    if os.path.exists(memory_md):
        with open(memory_md) as f:
            lines = f.read()

        if "reference_alzheimer.md" in lines:
            # Replace existing entry.
            lines = re.sub(
                r'^- \[.*\]\(reference_alzheimer\.md\).*$',
                index_line, lines, flags=re.MULTILINE
            )
        else:
            # Append entry.
            lines = lines.rstrip("\n") + "\n" + index_line + "\n"

        with open(memory_md, "w") as f:
            f.write(lines)
    # If no MEMORY.md exists, don't create one — rebalance.py handles that.


def seed_all_memory_dirs(alzheimer_dir):
    """Seed reference memory into every project memory directory."""
    memory_dirs = glob.glob(os.path.expanduser(
        "~/.claude/projects/*/memory"
    ))
    seeded = 0
    for d in memory_dirs:
        if os.path.exists(os.path.join(d, "MEMORY.md")):
            seed_memory(d, alzheimer_dir)
            seeded += 1
            print(f"  Seeded memory: {d}")
    if seeded == 0:
        print("  No memory directories found to seed.")
    return seeded


def do_update(settings_path):
    """Pull latest changes and re-install hooks.

    Can be run from the alzheimer directory itself (setup.py --update)
    or used by Claude after finding the install dir via settings.json.
    """
    import subprocess

    alzheimer_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"Alzheimer directory: {alzheimer_dir}")
    print()

    # Pull latest.
    print("Pulling latest changes...")
    result = subprocess.run(
        ["git", "pull"], cwd=alzheimer_dir,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"git pull failed: {result.stderr.strip()}")
        return False
    print(f"  {result.stdout.strip()}")
    print()

    # Re-install hooks (in case hook format changed).
    print("Re-installing hooks...")
    rebalancer_path = get_rebalancer_path()
    hooks = generate_hooks(rebalancer_path)
    install_hooks(settings_path, hooks)
    print(f"  Hooks updated in {settings_path}")
    print()

    # Seed reference memory.
    print("Seeding alzheimer reference memory:")
    seed_all_memory_dirs(alzheimer_dir)
    print()

    # Verify.
    print("Verifying:")
    ok = check_hooks(settings_path, rebalancer_path)
    return ok


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Set up alzheimer hooks in Claude Code settings."
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Merge hooks into ~/.claude/settings.json.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Verify hooks are installed correctly.",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Pull latest changes and re-install hooks.",
    )
    parser.add_argument(
        "--find", action="store_true",
        help="Print the alzheimer install directory (from settings.json).",
    )
    parser.add_argument(
        "--settings", default=os.path.expanduser("~/.claude/settings.json"),
        help="Path to settings file (default: ~/.claude/settings.json).",
    )
    args = parser.parse_args()

    if args.find:
        install_dir = find_install_dir(args.settings)
        if install_dir:
            print(install_dir)
        else:
            print("Alzheimer is not installed (not found in settings).",
                  file=sys.stderr)
            sys.exit(1)
        return

    if args.update:
        ok = do_update(args.settings)
        sys.exit(0 if ok else 1)

    rebalancer_path = get_rebalancer_path()
    hooks = generate_hooks(rebalancer_path)

    if args.check:
        ok = check_hooks(args.settings, rebalancer_path)
        sys.exit(0 if ok else 1)

    if args.install:
        settings = install_hooks(args.settings, hooks)
        print(f"Hooks installed in {args.settings}")
        print(f"Rebalancer: {rebalancer_path}")
        print("\nVerifying hooks:")
        check_hooks(args.settings, rebalancer_path)

        # Seed reference memory so Claude knows what "alzheimer" is.
        print("\nSeeding alzheimer reference memory:")
        seed_all_memory_dirs(os.path.dirname(os.path.abspath(__file__)))

        # Run initial rebalance + verify on all memory directories.
        from rebalance import rebalance as do_rebalance, verify_tree
        memory_dirs = glob.glob(os.path.expanduser(
            "~/.claude/projects/*/memory"
        ))
        for d in memory_dirs:
            memory_md = os.path.join(d, "MEMORY.md")
            if os.path.exists(memory_md):
                print(f"\nInitial health check: {d}")
                actions, warnings, messages = do_rebalance(d)
                for a in actions:
                    print(f"  {a}")
                if warnings:
                    for w in warnings:
                        print(f"  WARN: {w}")
                if messages:
                    print(f"  Glossary update needed ({len(messages)} message(s))")
                verify_tree(d)
        return

    # Default: print the hook JSON for manual pasting.
    print("Add the following to your ~/.claude/settings.json under "
          "\"hooks\":\n")
    print(json.dumps(hooks, indent=2))
    print(f"\n(Rebalancer path: {rebalancer_path})")


if __name__ == "__main__":
    main()
