#!/usr/bin/env python3
"""
guardrails.py — PreToolUse hook that blocks dangerous operations.

Hard layer of the Alzheimer guardrails system. Pattern-matches tool
invocations against a configurable set of rules and blocks those that
match by exiting non-zero.

The hook receives tool information as JSON on stdin:
    {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}

Exit 0 to allow, exit 1 with a JSON message to block.

Also supports --exec mode for "confirm" rules: temporarily removes
the matching rule, runs the command, and re-adds the rule in a
try/finally block — guaranteeing rule restoration.
"""

import json
import os
import re
import subprocess
import sys


# ── Default rules ─────────────────────────────────────────────────────

# Each rule matches a tool name and a regex pattern against the tool
# input.  If both match, the action is taken.
#
# For Bash tools, the pattern is matched against the "command" field.
# For other tools, it is matched against the full JSON-serialized input.
DEFAULT_RULES = [
    {
        "tool": "Bash",
        "pattern": r"git\s+push\b",
        "action": "confirm",
        "message": (
            "git push requires user confirmation (Alzheimer guardrails). "
            "Ask the user for explicit permission before pushing."
        ),
    },
    {
        "tool": "Bash",
        "pattern": r"git\s+push\s+.*--force\b",
        "action": "confirm",
        "message": (
            "git push --force requires user confirmation (Alzheimer guardrails). "
            "This is a destructive operation. Ask the user first."
        ),
    },
    {
        "tool": "Bash",
        "pattern": r"git\s+reset\s+--hard\b",
        "action": "confirm",
        "message": (
            "git reset --hard requires user confirmation (Alzheimer guardrails). "
            "This discards uncommitted changes. Ask the user first."
        ),
    },
    {
        "tool": "Bash",
        "pattern": r"git\s+branch\s+-[dD]\b",
        "action": "confirm",
        "message": (
            "Branch deletion requires user confirmation (Alzheimer guardrails). "
            "Ask the user for explicit permission."
        ),
    },
    {
        "tool": "Bash",
        "pattern": r"rm\s+-r[f ]\s*/\s*$|rm\s+-r[f ]\s*/\s+",
        "action": "block",
        "message": (
            "Recursive delete of root (/) is blocked by Alzheimer guardrails."
        ),
    },
]

# Config file for custom rules (loaded from alzheimer install dir).
CONFIG_FILE = ".guardrails.conf"


# ── Rule loading ──────────────────────────────────────────────────────

def _alzheimer_dir():
    """Return the directory containing this script."""
    return os.path.dirname(os.path.abspath(__file__))


def load_rules():
    """Load rules from config file, falling back to defaults.

    Config format (.guardrails.conf):
    {
        "rules": [
            {"tool": "Bash", "pattern": "git\\s+push\\b", "action": "block",
             "message": "..."}
        ]
    }

    If the config file contains a "rules" key, those rules REPLACE the
    defaults entirely.  If it contains "extra_rules", those are APPENDED
    to the defaults.
    """
    config_path = os.path.join(_alzheimer_dir(), CONFIG_FILE)
    if not os.path.exists(config_path):
        return list(DEFAULT_RULES)

    try:
        with open(config_path) as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Bad config — fall back to defaults.
        return list(DEFAULT_RULES)

    if "rules" in config:
        return config["rules"]
    elif "extra_rules" in config:
        return list(DEFAULT_RULES) + config["extra_rules"]
    else:
        return list(DEFAULT_RULES)


# ── Matching ──────────────────────────────────────────────────────────

def get_match_text(tool_name, tool_input):
    """Extract the text to match against from the tool input.

    For Bash tools, match against the command string.
    For other tools, match against the JSON-serialized input.
    """
    if tool_name == "Bash":
        return tool_input.get("command", "")
    return json.dumps(tool_input)


def _is_self_exec(tool_name, tool_input):
    """Check if this is a guardrails.py --exec invocation (self-allowlist)."""
    if tool_name != "Bash":
        return False
    command = tool_input.get("command", "")
    # Match: python3 /path/to/guardrails.py --exec "..."
    return bool(re.search(
        r'python3?\s+.*guardrails\.py\s+--exec\b', command
    ))


def check_rules(tool_name, tool_input, rules=None):
    """Check tool invocation against rules.

    Returns (allowed, message) where allowed is True if the action
    should proceed, and message is the block reason if not.
    """
    # Self-allowlist: guardrails.py --exec invocations bypass all rules.
    if _is_self_exec(tool_name, tool_input):
        return True, ""

    if rules is None:
        rules = load_rules()

    match_text = get_match_text(tool_name, tool_input)

    for rule in rules:
        rule_tool = rule.get("tool", "")
        if rule_tool and rule_tool != tool_name:
            continue

        pattern = rule.get("pattern", "")
        if not pattern:
            continue

        try:
            if re.search(pattern, match_text):
                action = rule.get("action", "block")
                if action == "block":
                    message = rule.get(
                        "message",
                        f"Operation blocked by Alzheimer guardrails "
                        f"(matched: {pattern})"
                    )
                    return False, message
                elif action == "confirm":
                    message = rule.get(
                        "message",
                        f"Operation requires user confirmation "
                        f"(matched: {pattern})"
                    )
                    message += (
                        " Use guardrails.py --exec to run after "
                        "obtaining user approval."
                    )
                    return False, message
        except re.error:
            # Invalid regex in rule — skip it.
            continue

    return True, ""


# ── Config file manipulation ─────────────────────────────────────────

def _config_path():
    """Return path to .guardrails.conf."""
    return os.path.join(_alzheimer_dir(), CONFIG_FILE)


def _load_config():
    """Load config file, returning (config_dict, existed)."""
    path = _config_path()
    if not os.path.exists(path):
        return {}, False
    try:
        with open(path) as f:
            return json.load(f), True
    except (json.JSONDecodeError, OSError):
        return {}, False


def _save_config(config):
    """Write config dict to .guardrails.conf."""
    path = _config_path()
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def remove_rule(rule):
    """Remove a rule from .guardrails.conf. Returns True if removed."""
    config, existed = _load_config()
    key = "rules" if "rules" in config else "extra_rules"
    rules = config.get(key, [])

    # Match by tool + pattern + action.
    original_len = len(rules)
    rules = [
        r for r in rules
        if not (r.get("tool") == rule.get("tool")
                and r.get("pattern") == rule.get("pattern")
                and r.get("action") == rule.get("action"))
    ]
    if len(rules) == original_len:
        return False

    config[key] = rules
    _save_config(config)
    return True


def add_rule(rule):
    """Add a rule to .guardrails.conf."""
    config, _ = _load_config()
    key = "rules" if "rules" in config else "extra_rules"
    rules = config.get(key, [])
    rules.append(rule)
    config[key] = rules
    _save_config(config)


def exec_with_temporary_allow(command, rule):
    """Remove rule, run command, re-add rule. Guaranteed by try/finally.

    Returns (returncode, stdout, stderr).
    """
    removed = remove_rule(rule)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        if removed:
            add_rule(rule)


def find_matching_rule(command):
    """Find the first confirm rule that matches a command.

    Returns the rule dict, or None if no confirm rule matches.
    """
    rules = load_rules()
    for rule in rules:
        rule_tool = rule.get("tool", "")
        if rule_tool and rule_tool != "Bash":
            continue
        pattern = rule.get("pattern", "")
        action = rule.get("action", "block")
        if action != "confirm" or not pattern:
            continue
        try:
            if re.search(pattern, command):
                return rule
        except re.error:
            continue
    return None


# ── Main ──────────────────────────────────────────────────────────────

def main():
    """Entry point. Dispatches to hook mode or --exec mode."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--exec":
        main_exec(" ".join(sys.argv[2:]))
    else:
        main_hook()


def main_hook():
    """Read tool info from stdin, check against rules, exit accordingly."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Can't parse input — allow by default (fail open).
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    allowed, message = check_rules(tool_name, tool_input)

    if allowed:
        sys.exit(0)
    else:
        # Exit code 2 blocks the tool call and shows stderr to the model.
        # Exit code 1 would only show stderr to the user without blocking.
        print(json.dumps({"error": message}), file=sys.stderr)
        sys.exit(2)


def main_exec(command):
    """Execute a command with temporary rule removal (confirm mode).

    Finds the matching confirm rule, removes it, runs the command,
    and re-adds the rule in a try/finally block.
    """
    rule = find_matching_rule(command)
    if rule is None:
        # No confirm rule matches — just run it directly.
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        sys.exit(result.returncode)

    returncode, stdout, stderr = exec_with_temporary_allow(command, rule)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    sys.exit(returncode)


if __name__ == "__main__":
    main()
