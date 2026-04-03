# Memory Issue Watch — anthropics/claude-code

Tracking open issues relevant to problems alzheimer solves: memory truncation, MEMORY.md overflow, silent memory loss, hierarchical memory, and memory management in Claude Code.

<!-- Last scan: 2026-04-03 -->

## Relevant Issues (Last 48h)

| # | Title | URL | Relevance | Date Found |
|---|-------|-----|-----------|------------|
| 42376 | `--continue` on 2.1.90 silently drops conversation context (regression from 2.1.89) | https://github.com/anthropics/claude-code/issues/42376 | Session context lost on resume — exactly the long-session continuity gap alzheimer addresses via persistent MEMORY.md hierarchy | 2026-04-02 |

## All-Time Relevant Issues (Historical Reference)

| # | Title | URL | Relevance | Date Found |
|---|-------|-----|-----------|------------|
| 41671 | [Bug] Auto-memory writes inline content to MEMORY.md instead of creating linked files | https://github.com/anthropics/claude-code/issues/41671 | MEMORY.md bloat from inline writes — alzheimer's linked-file hierarchy directly solves this | 2026-04-01 |
| 41473 | Case Study: Governing Stateless Sessions with a Structured Memory Framework | https://github.com/anthropics/claude-code/issues/41473 | Community-documented need for structured memory governance across stateless sessions | 2026-04-01 |
| 41356 | Agent ignores loaded memory rules when delegating to subagents | https://github.com/anthropics/claude-code/issues/41356 | Memory propagation failure to subagents; alzheimer's explicit loading model addresses scoping | 2026-04-01 |
| 41283 | Memory identity is derived from filesystem path, causing orphaned memories | https://github.com/anthropics/claude-code/issues/41283 | Path-based identity causes orphaned entries — alzheimer's named-file structure avoids this | 2026-04-01 |
| 40806 | feat: add memory access tracking to improve Auto Dream cleanup decisions | https://github.com/anthropics/claude-code/issues/40806 | 200-line MEMORY.md limit — no data-driven way to decide which memories to prune at capacity | 2026-03-30 |
| 40614 | [FEATURE] Hierarchical memory to prevent silent loss at 200-line MEMORY.md limit | https://github.com/anthropics/claude-code/issues/40614 | Directly requests hierarchical memory to replace flat MEMORY.md index that silently drops entries | 2026-03-30 |
| 40245 | Memory system should be hierarchical and live in the project folder | https://github.com/anthropics/claude-code/issues/40245 | Requests project-local hierarchical memory structure, mirroring alzheimer's approach | 2026-03-30 |
| 40210 | [BUG] Memory index appends new entries at bottom but truncates from bottom — newest memories lost first | https://github.com/anthropics/claude-code/issues/40210 | Core truncation bug: newest memories silently lost first due to append-then-truncate ordering | 2026-03-30 |
| 39920 | Git worktrees resolve to main worktree's memory directory instead of their own | https://github.com/anthropics/claude-code/issues/39920 | Memory scoping across worktrees — isolation gap alzheimer's project-local structure avoids | 2026-03-30 |
| 39663 | Context is lost when Claude suggests restarting — should auto-save a session summary | https://github.com/anthropics/claude-code/issues/39663 | Session memory loss on restart; alzheimer's persistent hierarchy addresses root cause | 2026-03-30 |
| 37888 | Claude runs explicitly forbidden destructive git commands, ignores own memory rules, destroys user work twice in same session | https://github.com/anthropics/claude-code/issues/37888 | Advisory memory rules fail after compaction; directly motivates alzheimer's two-layer guardrails (soft memory + hard PreToolUse hook) | 2026-04-02 |
| 34556 | Feature Request: Persistent Memory Across Context Compactions (59 compactions, built our own) | https://github.com/anthropics/claude-code/issues/34556 | Power user rebuilt memory persistence from scratch after 59 compactions — the exact problem alzheimer solves | 2026-04-02 |
| 34327 | Claude Code destroyed user's uncommitted work by running git reset --hard on session startup — TWICE | https://github.com/anthropics/claude-code/issues/34327 | Autonomous git reset --hard is one of the four commands alzheimer's guardrails hard layer requires confirmation for by default | 2026-04-02 |
| 34075 | Auto-memory system lacks clear boundary with CLAUDE.md, leading to scope creep | https://github.com/anthropics/claude-code/issues/34075 | Memory/instruction boundary confusion; alzheimer separates tiers explicitly | 2026-03-30 |
| 27298 | Feature Request: Layered memory system for persistent cross-session context | https://github.com/anthropics/claude-code/issues/27298 | Requests layered memory hierarchy for cross-session persistence — alzheimer implements this | 2026-04-02 |
| 16538 | Plugin SessionStart hooks don't surface hookSpecificOutput.additionalContext to Claude | https://github.com/anthropics/claude-code/issues/16538 | Alzheimer's SessionStart hook relies on additionalContext to inject drift warnings and glossary instructions — this bug silently drops them | 2026-04-02 |
| 40380 | [BUG] PreToolUse/PostToolUse warn hook systemMessage silently dropped without hookSpecificOutput | https://github.com/anthropics/claude-code/issues/40380 | Alzheimer's PostToolUse hooks emit systemMessage; when hookSpecificOutput is omitted it is silently dropped — sister bug to #16538, directly affects alzheimer's hook visibility in the UI | 2026-04-03 |
| 40537 | [BUG] Claude Code executed command on physical IoT device without user confirmation despite explicit rules requiring approval | https://github.com/anthropics/claude-code/issues/40537 | Advisory confirmation rules bypassed despite explicit user directives — directly motivates alzheimer's mechanical PreToolUse guardrail that fires regardless of what Claude remembers | 2026-04-03 |
| 27993 | Session compaction summaries can override .claude/rules/ directives | https://github.com/anthropics/claude-code/issues/27993 | Compaction summary overwrites rules directives, causing model to violate them post-compaction — alzheimer's hard PreToolUse hook is immune to this failure because it is deterministic code, not attention-based | 2026-04-03 |
| 27242 | [BUG] No working mechanism to review previous context after compaction, plan-mode clear, or branch navigation — data preserved but UI inaccessible | https://github.com/anthropics/claude-code/issues/27242 | Post-compaction context retrieval gap — exactly the problem alzheimer's in-development historical memory (log-structured summaries with drill-down) will address | 2026-04-03 |
| 17428 | [Feature Request] Enhanced /compact with file-backed summaries and selective restoration | https://github.com/anthropics/claude-code/issues/17428 | Community request for file-backed compact summaries with selective restoration — what alzheimer's in-development historical memory feature provides | 2026-04-03 |

---

## Engaged Issues

Issues where j-p-c has commented. Checked for new replies each scan.

| # | Title | URL | Last checked | New replies? | Notes |
|---|-------|-----|--------------|--------------|-------|
| 41671 | [Bug] Auto-memory writes inline content to MEMORY.md instead of creating linked files | https://github.com/anthropics/claude-code/issues/41671 | 2026-04-03 | No | j-p-c commented 2026-04-01; no new replies since |
| 41356 | Agent ignores loaded memory rules when delegating to subagents | https://github.com/anthropics/claude-code/issues/41356 | 2026-04-03 | No | j-p-c commented 2026-04-01; no new replies since |
| 41283 | Memory identity is derived from filesystem path, causing orphaned memories | https://github.com/anthropics/claude-code/issues/41283 | 2026-04-03 | No | j-p-c commented 2026-04-02; no new replies since |
| 40614 | [FEATURE] Hierarchical memory to prevent silent loss at 200-line MEMORY.md limit | https://github.com/anthropics/claude-code/issues/40614 | 2026-04-03 | Yes (3 new) | @prodan-s 2026-04-02 flagged a hook output routing bug in alzheimer; j-p-c fixed it in 0e52c34 same day; @mikeadolan also engaged |
| 40210 | [BUG] Memory index appends new entries at bottom but truncates from bottom — newest memories lost first | https://github.com/anthropics/claude-code/issues/40210 | 2026-04-03 | Yes (1 new) | j-p-c commented 2026-04-02 thanking @taipan303 for trying alzheimer and noting active development |
| 27298 | Feature Request: Layered memory system for persistent cross-session context | https://github.com/anthropics/claude-code/issues/27298 | 2026-04-03 | No | 21 total comments; no new replies since last scan |

---

*This file is updated automatically. Do not manually edit the issue list — run the watch script to refresh.*
