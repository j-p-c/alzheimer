#!/usr/bin/env python3
"""
setup.py — Installer and updater for Alzheimer.

Manages the hook registrations in Claude Code's settings that make
Alzheimer's fixes automatic: memory rebalancing, guardrails, reminders,
glossary updates, and drift detection. Also seeds reference memories so
every Claude instance knows what Alzheimer is — even after compaction.

Usage:
    python3 setup.py              # Preview hook configuration
    python3 setup.py --install    # Merge hooks into ~/.claude/settings.json
    python3 setup.py --check      # Verify hooks are installed correctly
    python3 setup.py --update     # Pull latest changes and re-install
    python3 setup.py --find       # Print install directory from settings
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


def get_guardrails_path():
    """Return the absolute path to guardrails.py."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "guardrails.py")


def get_reminders_path():
    """Return the absolute path to reminders.py."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "reminders.py")


def generate_hooks(rebalancer_path, guardrails_path=None,
                    reminders_path=None):
    """Generate the hook configuration dict."""
    # Escape for JSON embedding in shell commands.
    rp = rebalancer_path.replace('"', '\\"')

    hooks = {
        "PostToolUse": [{
            "matcher": "Write|Edit",
            "hooks": [{
                "type": "command",
                "command": (
                    f"jq -r '(.tool_input.file_path // "
                    f".tool_response.filePath) // empty' | "
                    f'{{ read -r f; echo "$f" | grep -q \'/memory/\' '
                    f"&& python3 \"{rp}\" --hook --hook-event PostToolUse "
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
                    f'python3 "{rp}" --hook --hook-event SessionStart "$d" 2>&1; done || true'
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
                    f'python3 "{rp}" --hook --hook-event PreCompact "$d" 2>&1; done || true'
                ),
                "timeout": 15,
                "statusMessage": "Rebalancing memory tree before compact..."
            }]
        }],
    }

    # Add guardrails PreToolUse hook (hard layer).
    if guardrails_path:
        gp = guardrails_path.replace('"', '\\"')
        hooks["PreToolUse"] = [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": f'python3 "{gp}"',
                "timeout": 5,
                "statusMessage": "Checking guardrails..."
            }]
        }]

    # Add reminders UserPromptSubmit hook (time-triggered checks).
    if reminders_path:
        mp = reminders_path.replace('"', '\\"')
        hooks["UserPromptSubmit"] = [{
            "hooks": [{
                "type": "command",
                "command": f'python3 "{mp}"',
                "timeout": 5,
                "statusMessage": "Checking reminders..."
            }]
        }]

    return hooks


def read_settings(settings_path):
    """Read existing settings, or return empty dict."""
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            return json.load(f)
    return {}


def _is_alzheimer_hook(command):
    """Check if a hook command belongs to alzheimer."""
    return ("rebalance.py" in command or "guardrails.py" in command
            or "reminders.py" in command)


def merge_hooks(existing_hooks, new_hooks):
    """Merge new hooks into existing, avoiding duplicates.

    For each event, checks if an alzheimer hook already exists
    (by looking for 'rebalance.py' or 'guardrails.py' in the command).
    If so, replaces it. If not, appends it.
    """
    merged = dict(existing_hooks)

    for event, hook_groups in new_hooks.items():
        if event not in merged:
            merged[event] = hook_groups
            continue

        # For each new hook group, find and replace existing alzheimer
        # hook or append.
        existing_groups = merged[event]
        for new_group in hook_groups:
            # Determine if this new group is an alzheimer hook.
            new_cmd = ""
            for hook in new_group.get("hooks", []):
                new_cmd = hook.get("command", "")
                if _is_alzheimer_hook(new_cmd):
                    break

            if not _is_alzheimer_hook(new_cmd):
                existing_groups.append(new_group)
                continue

            # Find the matching existing alzheimer hook to replace.
            # Match by the specific script name to avoid cross-replacement.
            if "guardrails.py" in new_cmd:
                script = "guardrails.py"
            elif "reminders.py" in new_cmd:
                script = "reminders.py"
            else:
                script = "rebalance.py"
            replaced = False
            for i, group in enumerate(existing_groups):
                for hook in group.get("hooks", []):
                    if script in hook.get("command", ""):
                        existing_groups[i] = new_group
                        replaced = True
                        break
                if replaced:
                    break

            if not replaced:
                existing_groups.append(new_group)

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
    correct paths."""
    if not os.path.exists(settings_path):
        print(f"FAIL: {settings_path} does not exist.")
        return False

    settings = read_settings(settings_path)
    hooks = settings.get("hooks", {})
    ok = True

    # Check rebalancer hooks.
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
            print(f"  {event} (rebalancer): OK")
        else:
            print(f"  {event} (rebalancer): MISSING")
            ok = False

    # Check guardrails hook.
    guardrails_path = get_guardrails_path()
    found = False
    for group in hooks.get("PreToolUse", []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "guardrails.py" in cmd:
                found = True
                if guardrails_path not in cmd:
                    print(f"WARN: PreToolUse hook points to different "
                          f"guardrails.py path.")
                    print(f"  Expected: {guardrails_path}")
    if found:
        print(f"  PreToolUse (guardrails): OK")
    else:
        print(f"  PreToolUse (guardrails): MISSING")
        ok = False

    # Check reminders hook.
    reminders_path = get_reminders_path()
    found = False
    for group in hooks.get("UserPromptSubmit", []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "reminders.py" in cmd:
                found = True
                if reminders_path not in cmd:
                    print(f"WARN: UserPromptSubmit hook points to different "
                          f"reminders.py path.")
                    print(f"  Expected: {reminders_path}")
    if found:
        print(f"  UserPromptSubmit (reminders): OK")
    else:
        print(f"  UserPromptSubmit (reminders): MISSING")
        ok = False

    # Check reference memory seed in all project memory directories.
    alzheimer_dir = os.path.dirname(os.path.abspath(__file__))
    memory_dirs = glob.glob(os.path.expanduser(
        "~/.claude/projects/*/memory"
    ))
    for d in memory_dirs:
        ref = os.path.join(d, "reference_alzheimer.md")
        if not os.path.exists(ref):
            print(f"  Reference seed: MISSING in {d}")
            ok = False
    if memory_dirs and all(
        os.path.exists(os.path.join(d, "reference_alzheimer.md"))
        for d in memory_dirs
    ):
        print(f"  Reference seed: OK ({len(memory_dirs)} director{'y' if len(memory_dirs) == 1 else 'ies'})")

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
name: Alzheimer — fixing Claude Code's memory bugs
description: "alzheimer" is a suite of fixes for Claude Code's memory and behavioral bugs, installed from github.com/j-p-c/alzheimer
type: reference
---

**Alzheimer** (v{version}) — fixing Claude Code's memory bugs.
Installed at: `{alzheimer_dir}`
GitHub repo: https://github.com/j-p-c/alzheimer

Alzheimer is **Claudeware**: Claude-native, Claude-first software built,
documented, and supported by human-directed Claudes.

## What Alzheimer does

Alzheimer runs automatically via hooks. You do not need to invoke it
manually. It fixes these Claude Code problems:

1. **Silent memory loss** — self-balancing memory tree keeps MEMORY.md
   within limits by pushing detail into category indices.
2. **Silent drift** — detects orphaned files, oversized leaves, broken
   references on every run.
3. **Term amnesia** — maintains a pinned glossary of key terms that
   survives compaction.
4. **Permission drift** — guardrails hook (`guardrails.py`) mechanically
   blocks dangerous operations regardless of what you remember.
5. **Time-blindness** — reminders hook (`reminders.py`) fires
   time-triggered actions on every prompt.
6. **Silent failures** — checks for updates, seeds reference memory,
   recovers from crashes.

## Reminders

When the user asks to be reminded of something, create or update
`reminders.md` in this memory directory. Do NOT use CronCreate — it
has a 7-day auto-expiry that makes it useless for recurring tasks.

Format for `reminders.md`:

```markdown
---
type: reminders
---

# Reminders

## Date reminders
- 2026-04-12 — Check if issue got traction
- 2026-05-01 — Review quarterly

## Recurring
- daily 09:00 — Pull report
- weekly Mon — Review open issues
```

The `reminders.py` hook checks this file automatically every 60 minutes
and injects due reminders into your context. One-shot reminders should
be removed after acting on them. Recurring reminders fire once per
interval and track their own state.

## Guardrails

The `guardrails.py` hook fires on every tool use. If it blocks a
command (e.g., `git push`), ask the user for confirmation, then use
`python3 "{alzheimer_dir}/guardrails.py" --exec "<command>"` to run
it through the deterministic wrapper.

## Common commands

- Update to latest: `python3 "{alzheimer_dir}/setup.py" --update`
- Check hooks are correct: `python3 "{alzheimer_dir}/setup.py" --check`
- Diagnose issues: `python3 "{alzheimer_dir}/rebalance.py" <memory_dir> --diagnose`
- Find install location: `python3 "{alzheimer_dir}/setup.py" --find`

When the user says "update alzheimer" they mean: pull latest from GitHub
and re-install hooks (i.e. run setup.py --update).

## Post-update protocol

After running --update, check the output for any WARN lines or
problems. You MUST fix them before reporting the update as complete:
- **Inline content in MEMORY.md**: Move each non-standard block into a
  separate .md file with frontmatter, replace with a one-line entry.
- **Over-limit MEMORY.md**: Restructure until under 150 lines.
- **Broken references**: Check if target exists; remove or recreate.
- **Orphaned files**: Add missing index entries in MEMORY.md.
Do NOT just report problems to the user — fix them, then tell the user.

## Being Claudewaric

Alzheimer is Claudeware. When working with it, think Claudewarically:
- **Claude-to-Claude communication uses .md files.** If you need to
  package knowledge for another Claude instance (via their human),
  write .md files — not scripts, archives, or other formats. .md files
  can be dropped into prompts, placed in memory directories, and read
  natively by any Claude.
- **Memory files are how you teach another Claude.** A well-structured
  .md file with frontmatter is the universal Claude-to-Claude transfer
  format.
- **Prefer Alzheimer's systems over built-in alternatives.** Use
  `reminders.md` instead of CronCreate. Use `guardrails.md` for
  behavioral rules. These persist across compaction; built-in tools
  may not.
- **Watch for recursive self-reference.** When a mechanism needs to
  bypass itself (e.g., guardrails.py --exec temporarily removing a
  rule to execute a confirmed command), the guard must recognize its
  own bypass invocation. The PreToolUse hook fires on *every* Bash
  call — including calls to guardrails.py itself. If the self-allowlist
  regex doesn't match the exact command Claude generates (including
  quoted paths), the bypass is blocked by the very thing it's trying
  to bypass. Always test self-referential mechanisms with the actual
  command format Claude produces, not idealized unquoted versions.
"""

    with open(memory_file, "w") as f:
        f.write(content)

    # Add or update the MEMORY.md index entry.
    index_line = ("- [Alzheimer](reference_alzheimer.md) "
                  "— fixing Claude Code's memory bugs; "
                  "update, diagnose, reminders, guardrails")

    if os.path.exists(memory_md):
        with open(memory_md) as f:
            lines = f.read()

        if "reference_alzheimer.md" in lines:
            # Remove ALL existing entries (there may be duplicates).
            lines = re.sub(
                r'^- \[.*\]\(reference_alzheimer\.md\).*\n?',
                '', lines, flags=re.MULTILINE
            )
            # Strip any trailing blank lines from removal, then append.
            lines = lines.rstrip("\n") + "\n" + index_line + "\n"
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
        seed_memory(d, alzheimer_dir)
        seeded += 1
        print(f"  Seeded memory: {d}")
    if seeded == 0:
        print("  No memory directories found to seed.")
    return seeded


def do_update(settings_path):
    """Pull latest changes, then delegate to --install via fresh process.

    After git pull, setup.py on disk may be newer than the version loaded
    in memory. We subprocess to --install so the updated code runs.
    """
    import subprocess

    alzheimer_dir = os.path.dirname(os.path.abspath(__file__))
    setup_py = os.path.join(alzheimer_dir, "setup.py")

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

    # Clear the update cache so the hook stops reporting "update available".
    cache_path = os.path.join(alzheimer_dir, ".alzheimer.lastcheck")
    try:
        import time, json as _json
        with open(cache_path, "w") as f:
            _json.dump({"timestamp": time.time(), "behind": 0}, f)
    except OSError:
        pass
    print()

    # Delegate to --install using the (potentially updated) code.
    print("Installing with updated code...")
    print()
    sys.stdout.flush()
    result = subprocess.run(
        [sys.executable, setup_py, "--install", "--settings", settings_path],
        cwd=alzheimer_dir
    )
    return result.returncode == 0


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
        "--seed-one", metavar="MEMORY_DIR",
        help="Seed reference_alzheimer.md into a single memory directory.",
    )
    parser.add_argument(
        "--settings", default=os.path.expanduser("~/.claude/settings.json"),
        help="Path to settings file (default: ~/.claude/settings.json).",
    )
    args = parser.parse_args()

    if args.seed_one:
        alzheimer_dir = os.path.dirname(os.path.abspath(__file__))
        seed_memory(args.seed_one, alzheimer_dir)
        return

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
    guardrails_path = get_guardrails_path()
    reminders_path = get_reminders_path()
    hooks = generate_hooks(rebalancer_path, guardrails_path, reminders_path)

    if args.check:
        ok = check_hooks(args.settings, rebalancer_path)
        sys.exit(0 if ok else 1)

    if args.install:
        settings = install_hooks(args.settings, hooks)
        print(f"Hooks installed in {args.settings}")
        print(f"Rebalancer: {rebalancer_path}")
        print(f"Guardrails: {guardrails_path}")
        print(f"Reminders:  {reminders_path}")
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
        problem_dirs = []
        for d in memory_dirs:
            memory_md = os.path.join(d, "MEMORY.md")
            if os.path.exists(memory_md):
                print(f"\nHealth check: {d}")
                actions, warnings, messages = do_rebalance(d)
                for a in actions:
                    print(f"  {a}")
                if warnings:
                    for w in warnings:
                        print(f"  WARN: {w}")
                    problem_dirs.append((d, warnings))
                if messages:
                    print(f"  Glossary update needed ({len(messages)} message(s))")
                verify_tree(d)

        if problem_dirs:
            print("\n" + "=" * 60)
            print("ACTION REQUIRED — YOU MUST FIX THESE NOW")
            print("=" * 60)
            print()
            print("Do NOT just report these problems to the user.")
            print("Fix them yourself, then tell the user what you fixed.")
            print()
            for d, warns in problem_dirs:
                print(f"  {d}:")
                for w in warns:
                    # Truncate the long instruction text for the summary.
                    short = w.split("IMPORTANT:")[0].strip()
                    print(f"    - {short}")
                print()
            print("For MEMORY.md files with inline content: read the file,")
            print("move each non-standard block into a separate .md file")
            print("with frontmatter (name, description, type), and replace")
            print("it with a one-line index entry:")
            print("  - [Title](filename.md) — short description")
            print()
            print("For over-limit MEMORY.md files: same approach —")
            print("restructure until under 150 lines.")
            print("=" * 60)

        return

    # Default: print the hook JSON for manual pasting.
    print("Add the following to your ~/.claude/settings.json under "
          "\"hooks\":\n")
    print(json.dumps(hooks, indent=2))
    print(f"\n(Rebalancer path: {rebalancer_path})")


if __name__ == "__main__":
    main()
