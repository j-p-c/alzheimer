#!/usr/bin/env python3
"""
setup.py — Generate Claude Code hook configuration for the alzheimer
rebalancer, with paths appropriate for this machine.

Usage:
    python3 setup.py              # Print hook JSON to paste into settings
    python3 setup.py --install    # Merge hooks into ~/.claude/settings.json
    python3 setup.py --check      # Verify hooks are installed correctly
"""

import json
import os
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
                    f"&& python3 \"{rp}\" "
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
                    f'python3 "{rp}" "$d" 2>&1; done || true'
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
                    f'python3 "{rp}" "$d" 2>&1; done || true'
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
                    import re
                    m = re.search(r'python3\s+"?([^"]+/rebalance\.py)"?',
                                  cmd)
                    if m:
                        rpath = m.group(1)
                        if os.path.exists(rpath):
                            return os.path.dirname(rpath)
    except (json.JSONDecodeError, KeyError):
        pass
    return None


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
        print("\nVerifying:")
        check_hooks(args.settings, rebalancer_path)
        return

    # Default: print the hook JSON for manual pasting.
    print("Add the following to your ~/.claude/settings.json under "
          "\"hooks\":\n")
    print(json.dumps(hooks, indent=2))
    print(f"\n(Rebalancer path: {rebalancer_path})")


if __name__ == "__main__":
    main()
