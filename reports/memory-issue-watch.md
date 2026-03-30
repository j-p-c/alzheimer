# Memory Issue Watch — anthropics/claude-code

Tracking open issues relevant to problems alzheimer solves: memory truncation, MEMORY.md overflow, silent memory loss, hierarchical memory, and memory management in Claude Code.

<!-- Last scan: 2026-03-30 -->

## Relevant Issues (Last 48h)

| # | Title | URL | Relevance | Date Found |
|---|-------|-----|-----------|------------|
| 40806 | feat: add memory access tracking to improve Auto Dream cleanup decisions | https://github.com/anthropics/claude-code/issues/40806 | 200-line MEMORY.md limit — no data-driven way to decide which memories to prune at capacity | 2026-03-30 |
| 40614 | [FEATURE] Hierarchical memory to prevent silent loss at 200-line MEMORY.md limit | https://github.com/anthropics/claude-code/issues/40614 | Directly requests hierarchical memory to replace flat MEMORY.md index that silently drops entries | 2026-03-30 |
| 40245 | Memory system should be hierarchical and live in the project folder | https://github.com/anthropics/claude-code/issues/40245 | Requests project-local hierarchical memory structure, mirroring alzheimer's approach | 2026-03-30 |
| 40210 | [BUG] Memory index appends new entries at bottom but truncates from bottom — newest memories lost first | https://github.com/anthropics/claude-code/issues/40210 | Core truncation bug: newest memories silently lost first due to append-then-truncate ordering | 2026-03-30 |

## All-Time Relevant Issues (Historical Reference)

| # | Title | URL | Relevance | Date Found |
|---|-------|-----|-----------|------------|
| 39920 | Git worktrees resolve to main worktree's memory directory instead of their own | https://github.com/anthropics/claude-code/issues/39920 | Memory scoping across worktrees — isolation gap alzheimer's project-local structure avoids | 2026-03-30 |
| 39663 | Context is lost when Claude suggests restarting — should auto-save a session summary | https://github.com/anthropics/claude-code/issues/39663 | Session memory loss on restart; alzheimer's persistent hierarchy addresses root cause | 2026-03-30 |
| 34075 | Auto-memory system lacks clear boundary with CLAUDE.md, leading to scope creep | https://github.com/anthropics/claude-code/issues/34075 | Memory/instruction boundary confusion; alzheimer separates tiers explicitly | 2026-03-30 |

---

*This file is updated automatically. Do not manually edit the issue list — run the watch script to refresh.*
