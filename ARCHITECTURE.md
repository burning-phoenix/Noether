# Noether Architecture

Noether is a terminal-based, multi-agent code editor that orchestrates LLMs to plan, explore, and edit codebases within a sandboxed environment.

This document is the comprehensive guide for developers seeking to understand, modify, or extend the codebase.

---

## 1. Design Philosophy

Noether runs inside your project directory — not a sandbox copy, not a container, your actual files. This position of trust shapes every architectural decision.

**One path, not five.** Every bash command and file operation flows through a single `OperationPipeline`. Not because it's elegant, but because when five different code paths do five different things, you can't reason about safety. One path means one place to validate, one place to approve, one place to record undo, one place to audit.

**The LLM is not to be trusted.** LLMs are powerful and unreliable. They hallucinate whitespace, guess indentation, and occasionally try to `rm -rf .` when asked to clean up. Four layers of content matching tolerate imprecise edits. Schema whitelists constrain what commands can even be requested. Snapshot-based undo ensures that when things go wrong — not if — you can get back to where you were.

**Read-only is a feature.** The autonomous loop (`/auto`, `/explore-add`) can read your codebase but not modify it. Write operations require either your explicit approval (chat path) or your explicit consent (task queue). The two-tier command schema exists because the most dangerous thing the tool can do is act without asking.

**Synchronous is a feature.** The pipeline uses `subprocess.run()`, not asyncio. A synchronous pipeline works identically whether called from a worker thread or an async context. No nested event loops, no race conditions, no subtle concurrency bugs.

**Undo means undo.** Every undo entry stores the full content of the file before modification. Not a diff. Not a reverse search-and-replace. The complete file. Undo restores exactly what was there, every time.

---

## 2. Agents

Noether orchestrates three agents, each with distinct boundaries:

| Agent | Role | Backend | State |
|-------|------|---------|-------|
| **PlannerAgent** | Planning, editing, chat, autonomous loop | API (K2-Thinking via Fireworks) | Session memory + modes |
| **CoderAgent** | Code generation from atomic tasks | Local GGUF or API (Qwen3-Coder) | Stateless per task |
| **ExploreAgent** | Codebase analysis, structured reports | API (DeepSeek V3) | Stateless per query |

**PlannerAgent** is the strategist. It talks to the user, understands intent, decomposes work into atomic tasks, and uses native tool calling to interact with the codebase. In chat, it has full tools but every action requires approval. In the autonomous loop, it's restricted to read-only tools.

**CoderAgent** is the generator. It writes code from atomic task descriptions. It doesn't run commands, doesn't make decisions, doesn't touch files directly. Everything it produces goes through batch approval. This is where the most room for improvement is. If we can somehow device mechanisms for effecient task allocation, the performance gains would be massive.

**ExploreAgent** is the scout. Stateless, focused, temporary. It gathers information and reports back. Specific files, specific semantic patterns, or anything that is in your codebase.

---

## 3. Project Layout

```
run.py                          # Entry point (convenience wrapper)
src/
  cli.py                        # CLI entry point (arg parsing, API key prompt)
  ui/
    app.py                      # Orchestration hub (~2200 lines)
    messages.py                 # Textual Message types
    screens/task_manager.py     # Planner chat + task queue pane
    screens/coder_view.py       # Code output pane
    modals/                     # Approval modals (command, file, undo)
  agents/
    planner_agent.py            # Planner with Go/Plan modes, memory
    coder_agent.py              # Code generation from atomic tasks
    explore_agent.py            # File skimming, focused reads, exploration
  orchestration/
    pipeline.py                 # SINGLE ENTRY POINT for all file/bash ops
    dispatcher.py               # AgentDispatcher — mounts agents, routes messages
    orchestrator.py             # Task queue with priority + dependency sorting
    agentic_loop.py             # Reason-Act-Observe loop (read-only tools)
    editor.py                   # 4-layer S/R content matcher
    undo.py                     # Snapshot-based undo stack
    task.py                     # Task decomposition data types
  prompts.py                    # All system prompts
  memory/planner_memory.py      # Session memory (10 events, 10 exchanges)
  sandbox/
    command_executor.py         # SRT-based command sandbox (sync via subprocess.run)
    schemas.py                  # ReadOnlyShellCommand / FullShellCommand (split)
    tools.py                    # get_readonly_tools() / get_chat_tools() (split)
    filesystem_sandbox.py       # Path resolution + safe file access
    embeddings.py               # CodebaseRAG — local Jina embeddings + Qdrant
  backends/
    openai_backend.py           # Fireworks API streaming client (OpenAI-compatible)
    qwen_backend.py             # Local GGUF model via llama-cpp-python
config/settings.py              # App configuration
```

---

## 4. Process & Data Flow

```mermaid
flowchart TD
    Views[TUI Panes] -- User Input --> Dispatcher[AgentDispatcher]
    Dispatcher -- "@work thread" --> Planner[PlannerAgent]
    Dispatcher -- "@work thread" --> Coder[CoderAgent]
    Dispatcher -- "@work thread" --> Explore[ExploreAgent]

    Planner -- JSON tool calls --> Pipeline[OperationPipeline]
    Pipeline -- bash --> SRT[SRT Sandbox]
    Pipeline -- file ops --> Sandbox[FileSystemSandbox]

    Planner -- API stream --> FW[Fireworks API]
    Coder -- stream --> FW
    Coder -- stream --> Local[Local llama-cpp]

    Planner -. chunks .-> Dispatcher
    Coder -. chunks .-> Dispatcher
    Dispatcher -- Messages --> Views

    Pipeline -. approval .-> Modals[Approval Modals]
    Modals -. Event.set --> Pipeline
```

### Request Lifecycle (example: `/auto "list the files"`)

1. User types `/auto "list the files"` in the Chat tab.
2. `TaskManagerPane` wraps it into a `StartAutonomousLoop` message and fires it to `AgentDispatcher`.
3. The dispatcher spawns a `@work(thread=True)` worker.
4. Inside the worker, `PlannerAgenticLoop` initializes with read-only tools and opens a streaming connection to the API.
5. The API returns text chunks and/or tool call chunks. Tool calls are assembled and yielded as structured dicts.
6. The dispatcher wraps all yields into `PlannerResponse` messages posted to the Textual event bus. The UI paints them via `@on(PlannerResponse)`.
7. If a `tool_call` is emitted (e.g., `execute_bash("ls -la")`), the loop routes it through `OperationPipeline.execute()`. In `/auto` mode, read-only commands execute without approval.
8. The result is appended to context memory and the loop continues (up to 15 iterations).

---

## 5. The Operation Pipeline

Every file write, edit, create, delete, and bash command flows through `OperationPipeline.execute()`. This is the single enforcement point.

### Operation Types

| Type | What | Example |
|------|------|---------|
| `BASH_READ` | Read-only shell command | `ls -la`, `grep -rn "class"`, `git log` |
| `BASH_WRITE` | Mutating shell command | `sed -i`, `rm`, `pip install` |
| `FILE_EDIT` | Precise S/R modification | ContentMatcher + `write_text()` |
| `FILE_CREATE` | Write a new file | Coder generates `app.py` |
| `FILE_WRITE` | Overwrite existing file | Coder rewrites `app.py` |
| `FILE_DELETE` | Remove a file | Undo of a create |

### Pipeline Steps

```
VALIDATE → PREPARE → APPROVE → EXECUTE → UNDO RECORD → CONTEXT UPDATE
```

1. **VALIDATE** — Pydantic schema check (command whitelist, arg validation, path traversal blocking). `FileSystemSandbox` checks for file ops. SRT availability check for bash.
2. **PREPARE** — For `FILE_EDIT`: run 4-layer ContentMatcher (exact → whitespace → indent → fuzzy). Compute `before_content` snapshot. For creates/writes: read previous content for undo.
3. **APPROVE** — Policy-dependent:
   - `NEVER` (auto loop) — read-only ops only, no approval needed
   - `ALWAYS` (chat path) — modal for every command and edit
   - `BATCH` (task queue) — one modal for all files in a batch
4. **EXECUTE** — `SandboxCommandExecutor.execute_sync()` for bash (via SRT). `filesystem_sandbox.safe_write_sync()` for files.
5. **UNDO RECORD** — Push `SnapshotEntry` with full file content before modification. Undo = restore snapshot.
6. **CONTEXT UPDATE** — Swap full file content to skim in Planner context. Append to `.noether/audit.jsonl`.

### Approval from Worker Threads

Modals are shown via `push_screen(modal, callback)` + `threading.Event.wait(60)`. Never `push_screen_wait()` from workers — it's broken in Textual's threading model.

---

## 6. Two-Tier Command Schemas

The autonomous loop and chat path receive different tool schemas:

### `ReadOnlyShellCommand` (auto loop: `/auto`, `/explore-add`)

```
ls, cat, grep, find, tree, head, tail, wc, diff,
git (restricted: status, log, diff, show, branch, rev-parse),
pwd, which, env, printenv, date, whoami
```

The auto loop physically cannot emit a write command. This is enforced by Pydantic's `Literal` type.

### `FullShellCommand` (chat path — always requires approval)

Everything from ReadOnly, plus:
```
sed, awk, cut, sort, uniq, tr,
python, python3 (no -c flag — blocks inline execution),
pip, pip3, pytest, pylint, mypy, black, flake8, ruff, isort,
touch, mkdir, cp, mv, rm,
echo, node, npm, npx, yarn
```

### Tool Sets

| Context | Tools | Schema |
|---------|-------|--------|
| `/auto`, `/explore-add` | `get_readonly_tools()` — `execute_bash`, `semantic_search`, `ask_user` | `ReadOnlyShellCommand` |
| Planner chat | `get_chat_tools()` — `execute_bash`, `edit_file`, `semantic_search`, `ask_user` | `FullShellCommand` |

---

## 7. Security Architecture

### Layer 1: Schema Constraints
- Command whitelist via Pydantic `Literal` type
- Arg validator blocks path traversal (`../`, `~/`, `/etc/`, `/root/`)
- `cwd` validator ensures commands stay within project root
- `python -c` blocked to prevent inline execution escape

### Layer 2: SRT Process Isolation (Mandatory)
- Commands execute through the Anthropic Sandbox Runtime (SRT) via `subprocess.run()`
- No `create_subprocess_shell` fallback — if SRT is unavailable, bash commands fail
- Network: only PyPI + GitHub domains reachable
- Filesystem: cannot read `.ssh/`, `.aws/`, `.env`; cannot write `.env`, `.key`, `.git/`

### Layer 3: Approval Gating
- Auto loop: read-only only, no approval needed (user consented with `/auto`)
- Chat path: modal approval for every command and file edit
- Task queue: batch approval for all files before writes begin

### Layer 4: Filesystem Sandbox
- `FileSystemSandbox` enforces BLOCKED_PATTERNS for Python-level file ops
- No access to credentials, secrets, SSH keys, cloud configs
- `.git/` is read-only (can read `.gitignore`, cannot write `.git/objects`)

### Layer 5: Timeout
- 30-second hard timeout on all commands
- Process killed on timeout

### Audit Trail
- Every operation logged to `.noether/audit.jsonl` with timestamp, operation type, target, success, and duration.

---

## 8. Module Reference

### `src/ui/` — The View Layer
Built on Textual. Handles layout, rendering, and input capture.
- **`app.py`** — Application shell with multi-tab layout, dependency injection, global key bindings.
- **`messages.py`** — `Message` dataclasses for cross-thread transport (`CoderResponse`, `ModeSwitch`, `PendingFileOperations`, etc.).
- **`screens/`** — `coder_view.py` (code output), `task_manager.py` (chat + task queue), `settings_trace.py` (backend logging).
- **`modals/`** — `ApprovalModal`, `CommandApprovalModal`, `BatchFileApprovalModal`, `UndoConfirmationModal`. Use `Event()` locks for cross-thread blocking.

### `src/orchestration/` — The Control Layer
- **`pipeline.py`** — `OperationPipeline`: the single entry point for all operations. See [Section 5](#5-the-operation-pipeline).
- **`dispatcher.py`** — `AgentDispatcher`: wraps agent logic in workers, posts Messages to decouple threads, routes tool calls through the pipeline.
- **`orchestrator.py`** — `TaskOrchestrator`: dependency-graph task queue using topological sort.
- **`agentic_loop.py`** — `PlannerAgenticLoop`: Reason-Act-Observe loop restricted to `get_readonly_tools()`.
- **`editor.py`** — `CodeEditor` / `ContentMatcher`: 4-layer fallback matching (Exact → Whitespace → Indent → Fuzzy) with indent preservation.
- **`undo.py`** — `SnapshotUndoStack`: LIFO stack storing full file snapshots. Undo = restore previous content.

### `src/agents/` — The Brains
- **`planner_agent.py`** — Socratic chat (Go/Plan modes), session memory, native tool calling.
- **`coder_agent.py`** — Receives atomic task descriptions, generates executable code.
- **`explore_agent.py`** — Stateless codebase analysis and structured reports.

### `src/backends/` — The Transports
- **`openai_backend.py`** — Fireworks API streaming client with `tenacity` retry. Handles tool call assembly from SSE chunks.
- **`qwen_backend.py`** — Local GGUF model via `llama-cpp-python`. Memory-mapped weights for 8GB RAM compatibility.

### `src/sandbox/` — The Security Layer
- **`schemas.py`** — `ReadOnlyShellCommand` / `FullShellCommand` (Pydantic). Validators for args, cwd, subcommands.
- **`tools.py`** — Converts Pydantic schemas to OpenAI function-calling JSON. `get_readonly_tools()` and `get_chat_tools()`.
- **`command_executor.py`** — `SandboxCommandExecutor`: `execute_sync()` via `subprocess.run()` through SRT. No async, no fallback. Output truncated to 4000 chars.
- **`filesystem_sandbox.py`** — Path resolution, BLOCKED_PATTERNS, READ_ONLY_PATTERNS.
- **`embeddings.py`** — `CodebaseRAG`: local `jina-embeddings-v2-base-code` + Qdrant for semantic search.

---

## 9. Threading Model

Textual runs an async event loop on the main thread. All LLM calls happen in `@work(thread=True)` workers.

### Rules

1. **Never call `query_one()` from workers.** Cache widget data in app-level variables and read the cache directly. Use `call_from_thread()` to sync UI updates.

2. **Never use `push_screen_wait()` from workers.** It's broken in Textual's threading model. Use `push_screen(modal, callback)` + `threading.Event.wait()` instead.

3. **Never post messages to spawn independent edit workers.** This causes modal race conditions. Process edits sequentially within the same worker.

4. **The pipeline is fully synchronous.** `OperationPipeline.execute()` uses `subprocess.run()` for bash. Never add asyncio to the pipeline — it must work identically from both worker threads (no event loop) and async contexts (agentic loop inside an event loop).

5. **Route UI mutations through Messages.** Never mutate DOM nodes (`widget.update()`) from LLM processing threads. Post a `Message` to the `AgentDispatcher`, which handles it on the main thread.

---

## 10. Key Patterns for Contributors

1. **ALL operations go through the pipeline.** Never call `subprocess.run()`, `asyncio.create_subprocess_shell()`, or `Path.write_text()` directly for LLM-initiated operations. Always build an `OperationRequest` and call `OperationPipeline.execute()`. The only exceptions are `SandboxCommandExecutor._sanity_check()` (init-time verification) and the legacy `SandboxCommandExecutor.execute()` async path.

2. **Prompts live in one place.** Never hardcode prompt strings inside agent logic. Store all system prompts in `src/prompts.py`.

3. **Use Pydantic for new tools.** When adding native tools, write a `BaseModel` in `src/sandbox/schemas.py` with `Field()` descriptions. The infrastructure converts it to OpenAI function-calling JSON automatically. Use `ReadOnlyShellCommand` for the auto loop and `FullShellCommand` for chat.

4. **Respect the agent boundaries.** Planner plans and orchestrates. Coder generates code. Explorer gathers context. Don't give agents capabilities outside their role.

5. **Context lifecycle matters.** After edits, full file content is swapped to a skim (16-18% of source lines) in Planner's context to conserve tokens. Don't assume Planner carries full file content between turns.
