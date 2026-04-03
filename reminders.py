#!/usr/bin/env python3
"""
reminders.py — Fixes time-blindness in Claude Code.

Claude has no built-in mechanism for time-triggered actions. "Remind me
next week" only works if Claude happens to read the right file at the
right time — which it usually doesn't, especially across compaction and
session restarts. This hook makes reminders mechanical.

Two-tier architecture:
  Tier 1: Lightweight timestamp check (~1ms per prompt). If less than
          CHECK_INTERVAL minutes have elapsed since last check, exit
          immediately. Cost: one stat() call.
  Tier 2: Parse all reminders.md files, find due reminders, inject them
          into Claude's context via hookSpecificOutput.additionalContext.

Supports one-shot date reminders, daily checks, and recurring schedules
(daily/weekly). Escalation pressure increases urgency if reminders fire
repeatedly without being acted on.

The hook receives prompt info on stdin (ignored — we only care about time).
Exit 0 always (this is advisory, never blocks).
"""

import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────

CHECK_INTERVAL = 60  # minutes between tier 2 checks
TIMESTAMP_FILE = os.path.expanduser("~/.alzheimer-last-check")

# ── Tier 1: timestamp gate ───────────────────────────────────────────

def should_check(now=None, interval=None):
    """Return True if enough time has elapsed since last check.

    Reads a single timestamp file. Cost: one stat() + one read.
    """
    if now is None:
        now = time.time()
    if interval is None:
        interval = CHECK_INTERVAL

    if not os.path.exists(TIMESTAMP_FILE):
        return True

    try:
        mtime = os.path.getmtime(TIMESTAMP_FILE)
        return (now - mtime) >= (interval * 60)
    except OSError:
        return True


def touch_timestamp():
    """Update the timestamp file to now."""
    try:
        with open(TIMESTAMP_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


# ── Tier 2: reminder parsing ────────────────────────────────────────

def find_reminder_files():
    """Find all reminders.md files in Claude project memory directories."""
    pattern = os.path.expanduser("~/.claude/projects/*/memory/reminders.md")
    return glob.glob(pattern)


def parse_date_reminders(content):
    """Parse date-based reminders from reminders.md content.

    Matches lines like:
        - 2026-04-12 — Check if issue got traction
        - 2026-05-01 — Review quarterly

    Returns list of (date_str, action_text) tuples.
    """
    reminders = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Match: - YYYY-MM-DD — action text
        m = re.match(
            r'^-\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(.+)$', line
        )
        if m:
            reminders.append((m.group(1), m.group(2).strip()))
    return reminders


def parse_daily_checks(content):
    """Parse the 'Daily checks' section from reminders.md.

    Returns list of (label, instruction) tuples for items under
    '# Daily checks' or '## Daily checks'.
    """
    checks = []
    in_daily = False
    for line in content.splitlines():
        stripped = line.strip()
        # Detect daily checks header.
        if re.match(r'^#{1,3}\s+Daily checks', stripped, re.IGNORECASE):
            in_daily = True
            continue
        # A new header ends the daily section.
        if in_daily and re.match(r'^#{1,3}\s+', stripped):
            break
        if in_daily and stripped.startswith("- **"):
            # Parse: - **Label**: instruction
            m = re.match(r'^-\s+\*\*(.+?)\*\*:?\s*(.+)$', stripped)
            if m:
                checks.append((m.group(1), m.group(2).strip()))
    return checks


def parse_recurring_reminders(content):
    """Parse recurring reminders from reminders.md content.

    Matches lines like:
        - daily 09:00 — Pull memory-issue-watch report
        - weekly Mon — Review open GitHub issues

    Returns list of (frequency, schedule, action_text) tuples.
    """
    reminders = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Match: - daily HH:MM — action
        m = re.match(
            r'^-\s+(daily)\s+(\d{2}:\d{2})\s*[—–-]\s*(.+)$', line
        )
        if m:
            reminders.append((m.group(1), m.group(2), m.group(3).strip()))
            continue
        # Match: - weekly Day — action
        m = re.match(
            r'^-\s+(weekly)\s+(\w+)\s*[—–-]\s*(.+)$', line
        )
        if m:
            reminders.append((m.group(1), m.group(2), m.group(3).strip()))
    return reminders


def check_date_reminders(reminders, today=None):
    """Check which date reminders are due.

    A reminder is due if today >= reminder_date.
    Returns list of action strings that are due.
    """
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    due = []
    for date_str, action in reminders:
        if today >= date_str:
            due.append(f"[Due {date_str}] {action}")
    return due


def check_recurring_reminders(reminders, now=None):
    """Check which recurring reminders are due.

    For daily reminders: due if current time >= scheduled time and
    not already fired today (checked via last-fired state file).

    Returns list of action strings that are due.
    """
    if now is None:
        now = datetime.now()

    due = []
    state = _load_recurring_state()

    for freq, schedule, action in reminders:
        key = f"{freq}_{schedule}_{action[:30]}"

        if freq == "daily":
            try:
                hour, minute = map(int, schedule.split(":"))
            except ValueError:
                continue
            scheduled_today = now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if now >= scheduled_today:
                last_fired = state.get(key, "")
                today_str = now.strftime("%Y-%m-%d")
                if last_fired != today_str:
                    due.append(f"[Daily {schedule}] {action}")
                    state[key] = today_str

        elif freq == "weekly":
            day_names = {
                "mon": 0, "tue": 1, "wed": 2, "thu": 3,
                "fri": 4, "sat": 5, "sun": 6
            }
            target_day = day_names.get(schedule[:3].lower())
            if target_day is None:
                continue
            if now.weekday() == target_day:
                last_fired = state.get(key, "")
                today_str = now.strftime("%Y-%m-%d")
                if last_fired != today_str:
                    due.append(f"[Weekly {schedule}] {action}")
                    state[key] = today_str

    _save_recurring_state(state)
    return due


# ── Recurring state persistence ──────────────────────────────────────

RECURRING_STATE_FILE = os.path.expanduser("~/.alzheimer-recurring-state")
FIRE_COUNT_FILE = os.path.expanduser("~/.alzheimer-reminder-fire-count")


def _load_recurring_state():
    """Load recurring reminder state (last-fired dates)."""
    if not os.path.exists(RECURRING_STATE_FILE):
        return {}
    try:
        with open(RECURRING_STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_recurring_state(state):
    """Save recurring reminder state."""
    try:
        with open(RECURRING_STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


# ── Fire count (escalation pressure) ────────────────────────────────

def _read_fire_count():
    """Read how many consecutive times reminders have fired without being cleared."""
    if not os.path.exists(FIRE_COUNT_FILE):
        return 0
    try:
        with open(FIRE_COUNT_FILE) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return 0


def _write_fire_count(count):
    """Write the fire count."""
    try:
        with open(FIRE_COUNT_FILE, "w") as f:
            f.write(str(count))
    except OSError:
        pass


def _reset_fire_count():
    """Reset fire count to zero (reminders were acted on)."""
    _write_fire_count(0)


def escalation_prefix(fire_count):
    """Return escalation prefix based on how many times reminders were ignored.

    Pressure increases with each consecutive firing:
      1: normal
      2: gentle nudge
      3-4: warning
      5+: critical — impossible to justify ignoring
    """
    if fire_count <= 1:
        return ""
    elif fire_count == 2:
        return (
            f"NOTE: These reminders have fired {fire_count} times "
            "without being acted on. Please address them when you can.\n\n"
        )
    elif fire_count <= 4:
        return (
            f"WARNING: These reminders have been ignored {fire_count} "
            "times. Act on them before continuing with other work.\n\n"
        )
    else:
        return (
            f"CRITICAL: These reminders have been ignored {fire_count} "
            "times. STOP and address them NOW before responding to "
            "anything else.\n\n"
        )


# ── Main ─────────────────────────────────────────────────────────────

def collect_due_reminders(today=None, now=None):
    """Scan all reminders.md files and collect everything that's due.

    Returns list of strings describing due reminders.
    """
    all_due = []

    for path in find_reminder_files():
        try:
            with open(path) as f:
                content = f.read()
        except OSError:
            continue

        # Date reminders.
        date_reminders = parse_date_reminders(content)
        all_due.extend(check_date_reminders(date_reminders, today=today))

        # Daily checks section.
        daily_checks = parse_daily_checks(content)
        for label, instruction in daily_checks:
            all_due.append(f"[Daily check: {label}] {instruction}")

        # Recurring reminders.
        recurring = parse_recurring_reminders(content)
        all_due.extend(check_recurring_reminders(recurring, now=now))

    return all_due


def main():
    """Entry point for UserPromptSubmit hook."""
    # Tier 1: timestamp gate.
    if not should_check():
        sys.exit(0)

    # Tier 2: check reminders.
    touch_timestamp()

    due = collect_due_reminders()
    if not due:
        # Nothing due — reset fire count (reminders were acted on).
        _reset_fire_count()
        sys.exit(0)

    # Increment fire count (reminders are due but haven't been cleared).
    fire_count = _read_fire_count() + 1
    _write_fire_count(fire_count)

    # Output as systemMessage for additionalContext.
    prefix = escalation_prefix(fire_count)
    lines = [f"{prefix}Alzheimer reminders due:"]
    for item in due:
        lines.append(f"  • {item}")
    lines.append("")
    lines.append(
        "Act on these reminders: bring them up with the user, "
        "then remove completed one-shot reminders from reminders.md. "
        "Ignoring this will cause it to fire again with increasing urgency."
    )

    message = {"systemMessage": "\n".join(lines)}
    print(json.dumps(message))
    sys.exit(0)


if __name__ == "__main__":
    main()
