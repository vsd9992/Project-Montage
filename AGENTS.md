# AGENTS.md — Operating Policy

This file is mandatory operating policy for all Codex/Claude sessions in this repository.

## Memory / Context

- Orient from `.ai/00-project-index.md` first.
- Start with minimum context; escalate only when uncertainty requires it.
- Avoid rereading unchanged files already reliably loaded since the last compaction.
- After compaction/context loss: re-read this file + the index, then restore only the context actually needed.
- Workflow: Planning & Evaluation -> Execution -> Testing & Bug Fixing.
- Intended truth (what SHOULD happen): current user instruction -> approved baseline -> current decisions.
- Implementation truth (what DOES happen): source/config/schema -> tests -> observed behaviour.
- Surface deviations between intended and implementation truth; never silently rewrite docs or code to hide them.
- Live docs (change often): index, roadmap status, active tasks, risks.
- Baseline docs (change only after approval): project-plan, requirements, architecture, workflows, current decisions.
- Update baseline docs only after an approved change, and update their `modified:` field in the same edit.
- Create atomic decision records only for important, durable rationale.
- Create/promote active task memory only when complexity, multi-session work, compaction, repeated failed approaches, non-obvious findings, or blockers justify it.
- Archive completed/superseded records rather than deleting them.
- Maintain the index whenever tasks/milestones/archives change.
- Periodically reconcile the index against actual `.ai/` contents.
- Verify at task, milestone, and major phase/release levels as appropriate.
- Size limits: index <= 3 KB, decisions <= 3 KB each, task records <= 5 KB each.

## Working Rules

- Work one task at a time.
- Do not change scope, requirements, architecture, or product behaviour without approval.
- Do not refactor unrelated code.
- Verify file-backed claims before asserting them.
- Never claim a build/test/lint check passed unless it was actually run successfully.
- Keep searches/logs/diffs/test output bounded.
- Protect secrets, credentials, production data, deployment, DNS, SSL, payments, and account/session/token settings unless explicitly authorized.
- Record verified install/dev/build/test/lint commands once known.

## Local AI Worker

- Optional MCP worker: `local_qwen_worker`, invoked via `delegate_task`.
- The local engine is external and user-managed; never auto-start/stop/restart/reconfigure it.
- Delegate only bounded, context-complete work where delegation likely saves meaningful effort.
- Good candidates: small code drafts, boilerplate, repetitive transformations, bounded summaries, candidate tests, review of a small supplied diff/snippet.
- Never delegate: repository truth, scope/requirements/architecture, durable `.ai/` memory, security/destructive decisions, or final verification.
- Treat worker output as an untrusted draft; independently review and validate it.
- If unavailable, continue normally without it.

### Local worker setup status

- `local_qwen_worker` is already registered and **enabled** globally (via user-level Codex config; confirmed with `codex mcp list`), pointing at `C:\_AI_Engine\local-ai\worker\codex_mcp_server.py`.
- No project-scoped `.mcp.json` / `.codex/config.toml` exists in this repo, and none was added — the global registration already makes the tool available in this project. If a project-scoped override is later wanted, mirror the known-working entry from `C:\_AI_Engine\.mcp.json` / `C:\_AI_Engine\.codex\config.toml`, do not invent new values, and re-verify with `codex mcp list`.

## Project Memory Layout

```
.ai/
  00-project-index.md
  project/
    project-plan.md
    roadmap.md
  decisions/
    current/
    archive/
  tasks/
    active/
    archive/
  verification/
```

`requirements.md`, `architecture.md`, `workflows.md`, and `risks.md` under `.ai/project/` are created later, once Planning & Evaluation makes them necessary.
