# Investigation Guide

> Standard process for investigating, isolating, and resolving high-severity bugs
> in this repository. Every investigation follows this guide regardless of subsystem.

---

## 1. Purpose

This document defines a repeatable, evidence-driven methodology for debugging
complex issues. It ensures:

- Conclusions are based on evidence, not assumptions.
- Git history stays clean (production vs instrumentation separated).
- Each investigation leaves the project stronger than before.
- Any team member (human or AI agent) can pick up an investigation mid-flight.

---

## 2. Investigation Lifecycle

Every investigation flows through seven phases. Do not skip phases.

### 2.1 Planning

Before writing any code or adding any log:

1. **Define the problem** in one sentence.
2. **List symptoms** the user/developer observed.
3. **Write reproduction steps** (exact clicks, exact sequence).
4. **Assign an Investigation ID**: `INV-YYYY-NNN`
5. **Set Exit Criteria** — when is this investigation done?
6. **Open a dedicated branch**: `debug/inv-YYYY-NNN-slug`

### 2.2 Instrument Last

Prefer existing evidence first. Add instrumentation only when current evidence cannot distinguish between competing hypotheses.

```
Current evidence
      │
      ▼
Can it distinguish between
competing hypotheses?
      │
   ┌──┴──┐
  Yes    No
  │       │
  ▼       ▼
Continue  Add minimal instrumentation
analysis      │
              ▼
      Collect new evidence
              │
              ▼
        Evidence review
```

**Exception**: For race conditions, memory corruption, one-shot crashes, or startup crashes, instrument immediately — the evidence may not survive a second reproduction.

**Rules:**
- **Never add "to see what happens" markers.** Every marker must answer a specific question from the fault isolation tree.
- **Instrument from outside in**: user action → application logic → transport → receive → parser → processor → state update → renderer.
- **Every marker must include ownership**: thread, socket, connection generation, packet sequence, transaction ID.
- **Temporary markers** must include a removal note: `TODO(INV-YYYY-NNN): Remove after reproduction`.

### 2.3 Evidence Collection

Run the reproduction. Collect logs.

- **Timestamps are mandatory.** Prefer nanosecond monotonic over wall clock.
- **Thread order != log order.** Two threads writing to the same file may interleave.
- **Absence of evidence is not evidence of absence.** If a marker is missing, consider: was it enabled? Did the process crash before writing?
- **Number all evidence**: E1, E2, E3... Reference them in the report.

### 2.4 Evidence Review

Before moving to root cause analysis, review all collected evidence formally.

- **List every evidence point** collected so far.
- **Classify each**: SUPPORTS a hypothesis, DISPROVES a hypothesis, or INCONCLUSIVE.
- **Identify gaps**: what question remains unanswered? What additional evidence is needed?
- **Decide**: is there enough evidence to isolate the root cause, or do we need another instrumentation round?

If evidence is insufficient, return to phase 2.2 (Instrument Last). Do not guess.

### 2.5 Root Cause Isolation

Use the fault isolation tree. Expand every branch until no "unknown" remains.

- **Do not fix code before isolating the fault domain.** You must answer: Logic? Transport? Protocol? Parser? Processor? Renderer?
- **Do not chase ghosts.** If evidence does not support a hypothesis, discard it.
- **Do not investigate two bugs simultaneously.** Suspend the less critical one.

### 2.6 Fix Validation

After applying a fix:

1. Build succeeds.
2. Original reproduction no longer triggers the bug.
3. Regression matrix passes (see Section 9).
4. No temporary instrumentation remains.
5. No `TODO(remove)` or debug CVars remain.

### 2.7 Cleanup

1. Delete or tag the investigation branch.
2. Write the postmortem (see Section 10).
3. Update documentation if a new pattern was discovered.
4. Delete remote investigation branch if pushed.

---

## 3. Branch Strategy

```
Primary integration branch
      │
      ▼
debug/inv-YYYY-NNN-slug        ← all work happens here
      │
      ├── production commits (feat, fix, refactor)
      ├── diagnostic commits (diag)
      └── disposable commits (investigation probes)
      │
      ▼
  Regression pass?
   ┌────┴────┐
  No        Yes
  │          │
Stay on    Tag debug branch
debug      snapshot
  │          │
  ▼          ▼
Fix bug   Cherry-pick production commits only
  │          │
  ▼          ▼
Repeat    Tag production release
regression   │
             ▼
       Delete debug branch
```

**Rules:**
- Primary integration branch is frozen during active investigation. No direct development.
- Debug branch is the superset. Primary branch only receives validated commits.
- Never cherry-pick diagnostic commits to primary branch.
- Tag the debug branch snapshot before cherry-picking.
- Use `git cherry-pick -x` for traceability back to the investigation.
- If regression fails, stay on debug branch, fix, and repeat the cycle.

---

## 4. Commit Strategy

### 4.1 Commit Types

| Prefix | Scope | Lives on primary branch? |
|--------|-------|--------------------------|
| `feat(subsystem):` | New feature or behavior change | Yes |
| `fix(subsystem):` | Bug fix | Yes |
| `refactor(subsystem):` | Code structure change, no behavior change | Yes |
| `diag(subsystem):` | Diagnostic instrumentation | No |
| `diag(timer):` | High-frequency timing instrumentation | No |
| `diag(tick):` | Tick-level investigation probes | No |

### 4.2 One Commit = One Purpose

Never mix feature + diagnostics + fix in a single commit. Each commit must be:

- **Single responsibility**: does one thing.
- **Self-contained**: does not depend on other unstaged hunks.
- **Buildable**: compiles without errors.
- **Reviewable**: a human can understand the diff in isolation.

### 4.3 Staging with `git add -p`

When adjacent hunks belong to different commit types:

```
Stage this hunk [y,n,q,a,d,s,e,?]? s      ← try split first
```

If split fails:

```
Stage this hunk [y,n,q,a,d,s,e,?]? e      ← manual edit
```

**Always verify before committing:**

```
git diff --cached --stat
git diff --cached
```

Check that only the intended hunks are staged. If anything unexpected appears, unstage it.

---

## 5. Marker Rules

### 5.1 Classification

Every marker is classified by cost level:

| Level | Description | Example | Allowed in production? |
|-------|-------------|---------|----------------------|
| 0 | Error/Warning only | `UE_LOG(Error, ...)` | Yes |
| 1 | One log per significant event | `QUEUE_PUSH`, `PACKET_DISPATCH` | Yes (if useful) |
| 2 | One log per object per event | `FBX_IMPORT_BEGIN` | Yes (if useful) |
| 3 | One log per frame | `TICK_PROBE`, `INTERP_PROBE` | No |
| 4 | One log per object per frame | Per-transform per tick | No |

**Rule: Cost >= 3 never merges to primary branch.**

### 5.2 Ownership

Every marker must record, at minimum:

- **Thread**: which thread wrote this?
- **Connection generation**: which connection cycle?
- **Packet sequence**: which packet?
- **Transaction ID**: which logical transaction?
- **Timestamp**: nanosecond monotonic preferred.

### 5.3 Question Mapping

Every marker maps to exactly one question in the fault isolation tree:

| Marker | Question |
|--------|----------|
| `QUEUE_PUSH` | Did the packet enter the send queue? |
| `QUEUE_POP` | Did the packet leave the queue? |
| `SEND_RETURN` | Did the kernel accept the send? |
| `SOCKET_RECV` | Did the receiver get bytes? |
| `PACKET_DISPATCH` | Did the parser dispatch the packet? |
| `IMPORT_BEGIN` | Did the importer start? |
| `IMPORT_END` | Did the importer finish? |
| `ACTOR_UPDATE` | Did the state update apply? |

### 5.4 Lifecycle

Every marker must be classified at creation time:

- **Production**: stays permanently. No removal note needed.
- **Temporary**: must include `TODO(INV-YYYY-NNN): Remove after reproduction`.

---

## 6. Evidence Rules

### 6.1 Evidence > Opinion

Never conclude from feeling. Only conclude from observed data.

Bad: "The queue probably lost the packet."
Good: "QUEUE_PUSH appeared. QUEUE_POP did not appear. Therefore the packet was lost between push and pop."

### 6.2 One Evidence Proves One Thing

`SOCKET_RECV` proves the kernel delivered bytes. It does not prove the parser is correct or the importer succeeded. Do not chain inferences across layers using a single evidence point.

### 6.3 Absence vs Evidence

Missing a marker in the log does not prove the code path was not taken. Consider:

- Was the marker enabled?
- Was the log file flushed?
- Did the process crash before writing?

Phrasing: "ENQUEUE was not observed in the current log." Not: "The operator did not run."

### 6.4 Timestamps Override Log Order

Two threads writing to the same file may interleave. The line order in the log file does not guarantee temporal order. Always trust monotonic timestamps over line position.

### 6.5 Evidence Numbering

Number all evidence sequentially: E1, E2, E3...

Reference evidence in the report:

```
E1: QUEUE_PUSH seq=1082 observed
E2: SEND_RETURN ret=128 errno=0 observed
E3: SOCKET_RECV absent in receiver log
=> Packet lost between sender send() and receiver recv()
```

---

## 7. Hypothesis Lifecycle

Every hypothesis follows a defined lifecycle. Track status explicitly in the postmortem.

```
NEW
 │
 ▼
ACTIVE  ← under investigation, evidence being collected
 │
 ├──→ SUPPORTED  ← evidence points to this, but not yet proven
 │         │
 │         ├──→ CONFIRMED  ← proven with targeted instrumentation or fix validation
 │         │
 │         └──→ DISPROVED  ← evidence contradicts, hypothesis discarded
 │
 └──→ DROPPED  ← not worth pursuing (low priority, insufficient access, etc.)
```

**Rules:**
- **Never resurrect a disproved hypothesis without new evidence.** If new evidence appears, create a new hypothesis (H_new) that references the old one.
- **SUPPORTED is not CONFIRMED.** A supported hypothesis may still be wrong. Only CONFIRMED has been validated through reproduction or fix.
- **DROPPED is not DISPROVED.** A dropped hypothesis may be true but is not worth pursuing given current constraints.

---

## 8. Decision Log

Recommended for multi-session or high-severity investigations. For short investigations (<30 minutes), a Decision Log is optional but still useful if hypotheses were considered and rejected.

Record every significant decision during the investigation. This prevents re-investigation and helps future team members understand why choices were made.

### Format

```
Decision D(N): <short description>
  Date: YYYY-MM-DD
  Based on: E(x), E(y), ...
  Accepted: <what was chosen>
  Rejected: <alternatives considered>
  Reason: <why this decision was made>
```

### Example

```
Decision D1: Focus investigation on transport layer
  Date: 2026-07-05
  Based on: E1 (QUEUE_PUSH observed), E2 (SEND_RETURN success)
  Accepted: Trace packet through socket/queue
  Rejected: Parser investigation, importer investigation
  Reason: E1+E2 confirm packet entered transport; loss is downstream of send

Decision D2: Drop H2 (socket ownership race)
  Date: 2026-07-07
  Based on: E5 (new socket fd correct), E6 (old socket cleaned up)
  Accepted: Socket lifecycle is correct
  Rejected: H2 (socket ownership race)
  Reason: E5+E6 prove ownership follows connection generation
```

### Rules

- **Number decisions sequentially**: D1, D2, D3...
- **Reference evidence**: every decision must cite which evidence it is based on.
- **Record rejections**: knowing what was considered and rejected is as valuable as knowing what was accepted.
- **Never delete decisions.** If a decision is reversed, record a new decision that references the old one.

---

## 9. Fault Isolation Tree

Always build a complete tree. Every leaf must be either CONFIRMED, DISPROVED, or ACTIVE (under investigation). Never leave a leaf as UNKNOWN without stating what evidence would resolve it.

```
User action
      │
      ▼
Application enqueue?
      │
      ├── No  → Logic error in caller
      │
      └── Yes
            │
            ▼
      Queue push?
            │
            ├── No  → Queue bug
            │
            └── Yes
                  │
                  ▼
            Send/transport?
                  │
                  ├── errno != 0  → Kernel/OS error
                  │
                  └── ret > 0
                        │
                        ▼
                  Receiver got bytes?
                        │
                        ├── No   → Network/transport loss
                        │
                        └── Yes
                              │
                              ▼
                        Parser dispatched?
                              │
                              ├── No   → Parser error
                              │
                              └── Yes
                                    │
                                    ▼
                              Processor handled?
                                    │
                                    ├── No   → Processor skip/reject
                                    │
                                    └── Yes
                                          │
                                          ▼
                                    State updated?
                                          │
                                          ├── No   → State/cache bug
                                          │
                                          └── Yes → Bug is downstream (render, display)
```

### Tree Versioning

The tree is versioned. When new evidence changes the tree structure:

1. Save as `Decision Tree v(N+1)`
2. Record: `Reason for change: Evidence E(N)`
3. Never overwrite the previous version.

---

## 10. Regression Matrix

The regression matrix is defined per investigation. Categorize scenarios into three tiers:

### Mandatory (always run)

| Scenario | Expected |
|----------|----------|
| Primary reproduction steps | PASS |
| Clean connect | PASS |
| Disconnect | PASS |
| Reconnect | PASS |

### Project-specific (run if subsystem is affected)

Define based on the subsystem under investigation. Examples:

- Mesh import
- Texture import
- Material update
- Transform update
- Hierarchy sync
- Visibility toggle

### Bug-specific (run if the fix touches these paths)

Define based on what the fix changes. Examples:

- Multiple objects
- Large payload (>10k verts)
- Rapid reconnect (3x)
- Concurrent operations

A fix is not complete until all applicable rows pass.

---

## 11. Postmortem Template

Every investigation must produce a postmortem. Save as:
`Docs/Investigations/INV-YYYY-NNN-<slug>.md`

```markdown
# INV-YYYY-NNN: <Title>

## Metadata

- **Status**: Open | Closed
- **Owner**: <name>
- **Started**: YYYY-MM-DD
- **Closed**: YYYY-MM-DD
- **Classification**: Transport | Protocol | Threading | Lifetime | Ownership | Logic | Engine bug

## Problem

<One-sentence description of the bug.>

## Symptoms

- <What the user saw>
- <What the logs showed>
- <When it happens>

## Reproduction Steps

1. <step>
2. <step>
3. <step>

## Hypotheses

| ID | Hypothesis | Status |
|----|-----------|--------|
| H1 | <hypothesis> | Disproved by E(n) |
| H2 | <hypothesis> | Confirmed by E(m) |
| H3 | <hypothesis> | Active |

## Evidence Collected

| ID | Description | Source | Classification |
|----|-------------|--------|----------------|
| E1 | <observation> | <log/file/marker> | Supports H(x) |
| E2 | <observation> | <log/file/marker> | Disproves H(y) |

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | <decision> | E(x), E(y) | <choice> | <alt> | <why> |

## Decision Tree

<Version number>
<ASCII tree or reference to file>

## Root Cause

<What actually caused the bug. Cite evidence.>

**Confidence**: Low | Medium | High | Proven

- **Low**: plausible hypothesis, limited supporting evidence
- **Medium**: multiple evidence points support, no contradictions found
- **High**: strong evidence chain, only one explanation fits
- **Proven**: reproduced with targeted instrumentation, fix validated

## Why Existing Tests Missed It

<What gap in the test suite allowed this bug to exist?>

## Fix

<What was changed. Reference commits.>

| Commit | Description |
|--------|-------------|
| C1 | feat(subsystem): ... |
| C2 | fix(subsystem): ... |

## Prevent Recurrence

<What test, invariant, or process change ensures this class of bug cannot return?>

## Regression

<Results of regression matrix.>

## Remaining Unknowns

- <Anything not fully resolved>

## Investigation Retrospective

### What worked
- <practices that were effective>

### What wasted time
- <activities that did not contribute to the solution>

### What assumptions were wrong
- <initial beliefs that were disproven>

### What evidence was missing initially
- <evidence that should have been collected earlier>

### Which instrumentation became permanent
- <markers that proved useful long-term>

### Which instrumentation was deleted
- <markers that were removed after investigation>
```

---

## 12. Exit Criteria

An investigation is complete ONLY when ONE of these is true:

1. Root cause identified AND fix validated through regression.
2. Entire fault tree exhausted (all leaves CONFIRMED or DISPROVED).
3. Bug proven to be outside this codebase (engine bug, OS bug, third-party).

An investigation is NOT complete because:

- "The bug stopped happening."
- "We can't reproduce it anymore."
- "We ran out of time."

---

## Appendix A: Investigation ID Registry

Maintain a running list of all investigations:

| ID | Title | Status | Owner | Started |
|----|-------|--------|-------|---------|

Add a row when starting each investigation. Update status as it progresses.

---

## Appendix B: Investigation Checklist

### Mandatory (every investigation)

- [ ] Problem defined in one sentence
- [ ] Reproduction steps written
- [ ] Investigation ID assigned
- [ ] Existing logs reviewed before adding any markers
- [ ] Hypotheses listed
- [ ] Evidence numbered
- [ ] Fault isolation tree built
- [ ] Root cause stated with confidence level
- [ ] Fix implemented and builds
- [ ] Regression passed
- [ ] Debug commits removed from primary branch
- [ ] Postmortem written

### Optional (multi-session or high-severity)

- [ ] Exit criteria defined before starting
- [ ] Decision log maintained (D1, D2...)
- [ ] Evidence review formalized (section 2.4)
- [ ] Investigation retrospective completed
- [ ] Why existing tests missed it documented
- [ ] Prevent recurrence plan defined
- [ ] Checklist completed for P0/P1

### Advanced (long-running or cross-team investigations)

- [ ] Hypothesis lifecycle tracked (all hypotheses have explicit status)
- [ ] Decision tree versioned (v1, v2, v3...)
- [ ] Instrumentation catalog maintained (which markers exist, where, cost level)
- [ ] Regression matrix defined with all three tiers
- [ ] Debug branch tagged before cherry-pick
- [ ] Registry updated with final status

---

## Appendix C: Investigation Anti-patterns

These are common mistakes. Recognizing them early saves days of wasted effort.

### Process Anti-patterns

| Anti-pattern | Why it's harmful |
|---|---|
| Fix before root cause is identified | May fix symptom, not cause; may introduce new bugs |
| Add random logs "to see what happens" | Creates noise, obscures real evidence, wastes time reading |
| Investigate two bugs in one branch | Fault trees get mixed, commits become uncherryable |
| Mix feature + diagnostics in one commit | Cannot cherry-pick cleanly, cannot revert safely |
| Cherry-pick diagnostic commits to primary branch | Pollutes production history with temporary noise |
| Conclude from missing logs | Absence of evidence is not evidence of absence |
| Resurrect a disproved hypothesis | Leads to circular investigation, wastes time re-examining rejected ideas |
| Skip regression after fix | Bug may reappear, or fix may have introduced regression |
| Delete evidence (logs, markers) before investigation closes | Permanently loses data that may be needed later |
| Rewrite git history during investigation | Destroys timeline, makes it impossible to trace decisions |

### Analysis Anti-patterns

| Anti-pattern | Why it's harmful |
|---|---|
| Chain inferences across layers from one evidence point | One marker proves one thing, not five things |
| Use wall clock time for ordering | Thread interleaving makes wall clock unreliable |
| Trust log line order as temporal order | File writes are not atomic across threads |
| Skip evidence review before forming hypothesis | Leads to confirmation bias |
| Declare "bug fixed" without regression | Fix may be incomplete or may have side effects |
| Leave UNKNOWN leaves in fault tree | Creates blind spots that may contain the root cause |

---

*Last updated: 2026-07-12*
*Version: 1.0*
*This guide applies to all investigations in this repository.*

---

## Future: Document Split

If this guide grows beyond ~400 lines or is maintained by multiple authors, consider splitting into:

```
Docs/Investigation/
    Investigation_Guide.md      ← process overview (~250 lines)
    Decision_Log.md             ← template + examples
    Postmortem_Template.md      ← template with all sections
    Checklist.md                ← mandatory/optional/advanced
    AntiPatterns.md             ← process + analysis anti-patterns
```

Keep this file as the entry point that links to the others.
