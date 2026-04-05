# Memory Issue Watch — anthropics/claude-code

Tracking open issues relevant to problems alzheimer solves: memory truncation, MEMORY.md overflow, silent memory loss, hierarchical memory, and memory management in Claude Code.

<!-- Last scan: 2026-04-05 -->

## Relevant Issues (Last 48h)

| # | Title | URL | Relevance | Date Found |
|---|-------|-----|-----------|------------|
| 42376 | `--continue` on 2.1.90 silently drops conversation context (regression from 2.1.89) | https://github.com/anthropics/claude-code/issues/42376 | Session context lost on resume — exactly the long-session continuity gap alzheimer addresses via persistent MEMORY.md hierarchy | 2026-04-02 |
| 43592 | [FEATURE] Session-scoped working memory tool (MemoryWrite/MemoryRead) — compact-proof constraint anchoring | https://github.com/anthropics/claude-code/issues/43592 | Proposes compact-proof MemoryWrite/MemoryRead API for constraint persistence — the same user-facing problem alzheimer solves via the MEMORY.md tree and glossary | 2026-04-04 |
| 43407 | PreToolUse hooks returning exit 2 + deny JSON do not block tool execution | https://github.com/anthropics/claude-code/issues/43407 | Alzheimer's guardrails hard layer relies on PreToolUse exit 2 to block destructive commands — this bug means the hard layer may silently fail to block | 2026-04-04 |
| 43450 | [BUG] PreCompact hook exit code 2 does not block compaction | https://github.com/anthropics/claude-code/issues/43450 | Alzheimer uses PreCompact to rebalance memory before context is lost; if exit 2 doesn't block compaction, alzheimer's pre-compact safety step can be bypassed | 2026-04-04 |
| 43603 | [BUG] Post-compact auto-read doubles hook injections — configurable autoReadFiles needed | https://github.com/anthropics/claude-code/issues/43603 | Alzheimer's UserPromptSubmit reminders hook would be doubled on post-compact auto-reads, causing duplicate reminder injections | 2026-04-04 |
| 43772 | Subagents with bypassPermissions ignore PreToolUse hooks — unauthorized commands, wasted tokens | https://github.com/anthropics/claude-code/issues/43772 | Alzheimer's hard-layer guardrails rely on PreToolUse hooks to block destructive commands; bypassPermissions mode in subagents silently circumvents the entire hard layer, allowing unauthorized commits, deletions, and permission changes | 2026-04-05 |
| 43733 | PreCompact hook: allow Claude to take actions before context compaction (write session state) | https://github.com/anthropics/claude-code/issues/43733 | Requests PreCompact hooks that trigger Claude actions (not just shell commands) to write session state before compaction — would strengthen Alzheimer's pre-compaction memory rebalancing and enable structured session-state writing | 2026-04-05 |
| 43716 | Opus 4.6 (1M): Ignores CLAUDE.md rules and user instructions in long sessions | https://github.com/anthropics/claude-code/issues/43716 | Permission drift over ~8h sessions: CLAUDE.md rules progressively ignored, destructive action taken (deleted 331 lines of chat history) in violation of explicit rules — exactly the behavioral failure Alzheimer's deterministic hard-layer guardrails prevent | 2026-04-05 |
| 43632 | [4.6 opus MODEL] Built-in system prompt rules override CLAUDE.md and hooks — model ignores user-configured verification rules | https://github.com/anthropics/claude-code/issues/43632 | Built-in defaults override user rules even when CLAUDE.md + hooks + session memory all configured; user lost data across 10+ sessions — demonstrates why Alzheimer's deterministic PreToolUse hard layer (code, not attention) is necessary | 2026-04-05 |
| 43557 | Claude Code reads CLAUDE.md behavioral rules but doesn't follow them during task execution | https://github.com/anthropics/claude-code/issues/43557 | Memory/rules read but not mechanically enforced — precisely the gap alzheimer's hard-layer PreToolUse guardrails fix via deterministic code | 2026-04-04 |
| 42542 | [BUG] Silent context degradation — tool results cleared without notification on 1M context sessions | https://github.com/anthropics/claude-code/issues/42542 | Silent degradation of tool results is the same class of silent data loss alzheimer's drift detection and in-dev historical memory address | 2026-04-02 |

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
| 41279 | [FEATURE] Compaction-protected Rulebook — standing behavioral rules as a counterpart to Skills | https://github.com/anthropics/claude-code/issues/41279 | Community request for the exact mechanism alzheimer's guardrails provide — durable behavioral rules that persist unconditionally through compaction, enforced by code not attention | 2026-04-04 |
| 40492 | Feature Request: PostCompact hook event for post-compaction verification | https://github.com/anthropics/claude-code/issues/40492 | Alzheimer currently uses PreCompact; a PostCompact hook would enable post-rebalance verification of the memory tree after compaction completes | 2026-04-04 |

---

## Engaged Issues

Issues where j-p-c has commented. Checked for new replies each scan.

| # | Title | URL | Last checked | New replies? | Notes |
|---|-------|-----|--------------|--------------|-------|
| 41671 | [Bug] Auto-memory writes inline content to MEMORY.md instead of creating linked files | https://github.com/anthropics/claude-code/issues/41671 | 2026-04-05 | No | j-p-c last commented 2026-04-01; no new replies since |
| 41356 | Agent ignores loaded memory rules when delegating to subagents | https://github.com/anthropics/claude-code/issues/41356 | 2026-04-05 | No | j-p-c last commented 2026-04-01; no new replies since |
| 41283 | Memory identity is derived from filesystem path, causing orphaned memories | https://github.com/anthropics/claude-code/issues/41283 | 2026-04-05 | No | j-p-c last commented 2026-04-02; confirmed no new replies since |
| 40614 | [FEATURE] Hierarchical memory to prevent silent loss at 200-line MEMORY.md limit | https://github.com/anthropics/claude-code/issues/40614 | 2026-04-05 | No | Last activity 2026-04-02 (@mikeadolan SQLite comparison); no new replies since |
| 40210 | [BUG] Memory index appends new entries at bottom but truncates from bottom — newest memories lost first | https://github.com/anthropics/claude-code/issues/40210 | 2026-04-05 | No | Last activity 2026-04-02 (@taipan303 reported using alzheimer); no new replies since |
| 27298 | Feature Request: Layered memory system for persistent cross-session context | https://github.com/anthropics/claude-code/issues/27298 | 2026-04-05 | No | Last activity 2026-02-24 (singularityjason shared OMEGA memory system); no new replies since |
| 43156 | VS Code extension: uninterruptible processing loop causes irrecoverable data loss | https://github.com/anthropics/claude-code/issues/43156 | 2026-04-05 | No | j-p-c posted 2 comments 2026-04-03 including screen recording; no new replies since |
| 42772 | VS Code extension: auto-collapsing user prompts in chat history (no setting to disable) | https://github.com/anthropics/claude-code/issues/42772 | 2026-04-05 | New entry | j-p-c commented 2026-04-05 with detailed observations about non-deterministic "Show more" button behavior; VS Code UI issue, not Alzheimer-specific |

---

*This file is updated automatically. Do not manually edit the issue list — run the watch script to refresh.*
