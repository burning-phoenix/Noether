# Noether

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A terminal-based code editor that orchestrates multiple AI agents for intelligent code generation, planning, and execution.

[Watch the demo](https://github.com/burning-phoenix/noether/releases/download/v0.1.0/noether-trailer.mp4)

```bash
noether --fast
```

## The Idea

Most coding assistants hit a wall: they either have limited context (can't understand large projects) or they're expensive (pay per token for everything). This project solves that with a three-agent architecture:

1. **Planner Agent** (Fireworks API - K2-Thinking) - Plans and orchestrates. Has 128K+ context to understand entire projects, uses the Socratic method for scope refinement, and decomposes complex tasks into atomic units. Uses Native API Tool Calling.
2. **Coder Agent** (Local or API - Qwen3-Coder) - Generates code. Runs locally for free on an M1 Mac 8GB via `llama-cpp-python`, or via the Fireworks API for speed.
3. **Explore Agent** (Fireworks API - DeepSeek V3) - Analyzes codebases on-demand. Runs bash commands, gathers context, and produces structured reports.

The Planner breaks complex tasks into atomic units that fit the Coder's context window. You get the benefits of huge context for planning, while code generation stays cheap (or free).

## Features

- **Dual Mode**: Run code generation locally (free, ~2-4 tok/s) or via API (fast, ~50+ tok/s).
- **Planner Modes**: Switch between Go Mode (act first, ask later) and Plan Mode (Socratic questioning with Maintainable or Discovery sub-modes).
- **Universal Native API Tooling**: Replaces fragile text parsing with strict JSON Schema function calling (powered by Pydantic) for Bash Execution, Semantic Search, and File Editing.
- **Semantic Codebase Retrieval (RAG)**: Integrates `qdrant-client` and local `jina-embeddings-v2-base-code` (using <300MB RAM) to navigate complex ASTs semantically on 8GB M1 Macs. 
- **Unified Operation Pipeline**: Every bash command and file operation flows through a single `OperationPipeline` — validation, SRT isolation, approval, undo, and audit logging enforced in one place.
- **SRT Sandbox**: Commands execute through the Anthropic Sandbox Runtime with network and filesystem isolation. No `create_subprocess_shell` fallback.
- **Two-Tier Command Schemas**: Read-only commands for autonomous loops, full commands (with approval) for chat.
- **Robust UI Concurrency**: Complete elimination of multi-threaded race conditions by migrating all Agent-to-UI state mutations to native Textual Message passing (`AgentDispatcher`).
- **Observability**: Real-time LLM interaction trace logging rendered natively inside the TUI dashboard, plus `.noether/audit.jsonl` for all operations.
- **Search/Replace Editing**: Precise code modifications with 4-layer content matching (exact, whitespace, indent, fuzzy).
- **Snapshot Undo**: Full file content stored before every modification — sequential undos always work, no S/R reversal matching.

## Quick Start

### Install from Source

```bash
git clone https://github.com/burning-phoenix/noether.git
cd noether
pip install -e .
```

### Run

```bash
cd ~/your-project
noether --fast
```

You'll be prompted for your [Fireworks AI](https://fireworks.ai) API key on first launch. The key is never saved to disk.

### Local Mode (Optional)

For free, offline code generation using a local Qwen model (~18GB download):

```bash
# 1. Install local model dependencies
pip install -e ".[local]"

# 2. Install llama-cpp-python with Metal support (Apple Silicon)
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir

# 3. Download the model
python download_model.py

# 4. Run without --fast
noether
```

Local mode runs Qwen3-Coder-30B at ~2-4 tokens/sec on M1 8GB. Use `--fast` for API-based generation (~50+ tok/sec). You can also swap out for any other model that would be more suitable for your machine's specs.

### Local-Only Mode (No API Key)

```bash
noether --no-api
```

Runs Qwen locally without any API calls. Planner and Explore agents are unavailable.

## Usage

### The Interface

The app has multiple panes and a collapsible sidebar:

- **Tab 1 (Code)**: Shows generated code with line numbers
- **Tab 2 (Chat)**: Chat with the Planner, manage task queue
- **Tab 3 (Settings & Trace)**: Configuration and real-time backend logging
- **Sidebar**: Session tasks checklist, token usage, activity log

Press `Ctrl+1` / `Ctrl+2` to switch tabs, `Ctrl+B` to toggle the sidebar.

### Basic Workflow

1. `cd` into your project directory (or an empty dir for a new project)
2. Describe your project to the Planner in the Chat tab
3. Planner asks questions to understand requirements (Plan Mode) or acts immediately (Go Mode)
4. Type `/confirm` when the scope is clear
5. Planner decomposes into prioritized subtasks with dependencies
6. Coder generates code for each subtask — files are created in your project directory with approval

### Commands

| Command | Description |
|---------|-------------|
| `/confirm` | Approve scope, start task decomposition |
| `/run` | Execute pending tasks in queue |
| `/auto <task>` | Run autonomous loop (Planner reasons, acts, observes via tool calling) |
| `/explore [type]` | Analyze codebase (architecture, security, errors, tests, dependencies, api) |
| `/explore-add [type]` | Explore and add results to Planner's context |
| `/mode fast` | Switch to API-based code generation |
| `/mode local` | Switch to local Qwen model |
| `/planner go` | Switch Planner to Go Mode (action-first) |
| `/planner plan` | Switch Planner to Plan Mode (Socratic, asks a lot of questions, maintainable) |
| `/planner plan discovery` | Switch Planner to Plan Mode (experimental, MVP-focused) |
| `/clear` | Clear chat history |
| `/queue` | Show current task queue |
| `/undo` | Undo the last edit or file operation |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+1` | Code tab |
| `Ctrl+2` | Chat tab |
| `Ctrl+E` | Trigger codebase exploration |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+L` | Clear current view |
| `Ctrl+Q` | Quit |

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                 Textual TUI (Multi-Tab Interface)           │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────┐  │
│  │  Code View      │  │  Chat            │  │ Trace Logs │  │
│  │  (code output)  │  │  (Planner chat + │  │ (Backend)  │  │
│  │                 │  │   task queue)    │  │            │  │
│  └─────────────────┘  └──────────────────┘  └────────────┘  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                ┌──────────────┼──────────────┐
                │       AgentDispatcher       │
                │   (Textual Message Bus)     │
                └──────────────┬──────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
  │  CoderAgent   │    │ PlannerAgent  │    │ ExploreAgent  │
  │   (coding)    │    │  (planning)   │    │  (analysis)   │
  │               │    │               │    │               │
  │ Generates     │    │ Socratic chat │    │ Runs bash     │
  │ code from     │    │ Decomposes    │    │ commands,     │
  │ atomic tasks  │    │ Edits code    │    │ gathers       │
  │               │    │ /auto loop    │    │ context,      │
  │               │    │ Go/Plan modes │    │ semantic src  │
  └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
          │                    │                    │
          ▼                    ▼                    ▼
  ┌───────────────┐    ┌────────────────────────────────────┐
  │ LocalBackend  │    │          FireworksBackend          │
  │ (llama-cpp    │    │     (OpenAI-compatible API)        │
  │  local GGUF)  │    │  K2-Thinking + DeepSeek + Qwen3    │
  └───────────────┘    └────────────────────────────────────┘
                               │
               ┌───────────────┴────────────────┐
               │         Sandbox Layer          │
               │  ┌───────────┐  ┌───────────┐  │
               │  │ Command   │  │CodebaseRAG│  │
               │  │ Executor  │  │ (Qdrant)  │  │
               │  │ (Native   │  │ (jina-em) │  │
               │  │  Tools)   │  └───────────┘  │
               │  └───────────┘                 │
               └────────────────────────────────┘
                               │
                               ▼
                     Your Project Directory
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| **App Hub** | `src/ui/app.py` | Central shell, layouts, & dependency injection |
| **Pipeline** | `src/orchestration/pipeline.py` | **Single entry point** for all bash/file operations (see [ARCHITECTURE.md](ARCHITECTURE.md)) |
| **Dispatcher**| `src/orchestration/dispatcher.py`| Mounts background agents, routes Textual UI Messages, routes tool calls through pipeline |
| **Messages** | `src/ui/messages.py` | Textual Message payloads bridging threads |
| **PlannerAgent** | `src/agents/planner_agent.py` | Planning, decomposition, editing, autonomous loop, native tool calling |
| **CoderAgent** | `src/agents/coder_agent.py` | Code generation from atomic tasks |
| **ExploreAgent** | `src/agents/explore_agent.py` | Stateless codebase analysis via DeepSeek |
| **Orchestrator** | `src/orchestration/orchestrator.py` | Task queue with priority + dependency sorting |
| **Agentic Loop** | `src/orchestration/agentic_loop.py` | React-style Reason-Act-Observe loop (read-only tools only) |
| **Undo Stack** | `src/orchestration/undo.py` | Snapshot-based undo — stores full file content for reliable rollback |
| **Schemas** | `src/sandbox/schemas.py` | `ReadOnlyShellCommand` (auto loop) / `FullShellCommand` (chat) Pydantic validation |
| **Tools** | `src/sandbox/tools.py` | `get_readonly_tools()` / `get_chat_tools()` — Pydantic to OpenAI JSON Schema |
| **Executor** | `src/sandbox/command_executor.py` | SRT-based bash execution via `subprocess.run()` — no async, no fallback |
| **CodebaseRAG**| `src/sandbox/embeddings.py`| Local Jina AST embedding indexing & Qdrant retrieval |

### Threading Model

- Textual runs an async event loop on the **main thread**.
- All LLM calls run in **worker threads** via `@work(thread=True)` to avoid blocking the UI.
- The `AgentDispatcher` consumes worker outputs and posts standard Textual `Message` objects into the DOM.
- Race conditions previously caused by threaded UI element traversal (`lock()` contentions) are solved natively through the reactive event bus (`@on(CoderResponse)`).
- The `OperationPipeline` is fully synchronous — bash execution uses `subprocess.run()`, not asyncio. This ensures it works from any context (worker threads, async loops) without event loop collisions.

## Planner Modes

The Planner operates in two top-level modes that control its behavior:

### Go Mode (default for existing projects)
- **Act first, ask later.** Direct execution with minimal questions.
- Uses Native Tools (`execute_bash`, `semantic_search`) immediately to navigate ASTs.
- Short 1-3 sentence responses. No preamble.
- Best for: Quick edits, exploring codebases, running commands.

### Plan Mode (default for new/empty projects)
- **Socratic method.** Asks deep clarifying questions before acting.
- Two sub-modes:
  - **Maintainable**: Focus on architecture, testability, error handling, documentation. Includes test and documentation tasks in decomposition.
  - **Discovery**: Focus on rapid prototyping, MVPs, experimentation. Fewer larger tasks, skip tests for throwaway prototypes.
- Best for: New projects, complex features, architectural decisions.

Switch modes with `/planner go`, `/planner plan`, or `/planner plan discovery`.

## Execution Endpoints

### Fast Mode (`--fast`)
- Uses Qwen3-Coder via Fireworks API
- Much faster generation (~50+ tokens/sec)
- Costs practically nothing (~$0.27 per million tokens)
- Best for: Active development, quick iterations

### Local Mode (default)
- Uses Qwen3-Coder-30B (Q4_K_M quantization) via `llama-cpp-python`
- Slower (~2-4 tokens/sec on M1 8GB)
- Completely free after model download (~18GB)
- Memory-mapped weights stream from SSD for 8GB RAM compatibility
- Best for: Long-running background tasks, cost-conscious usage
- Alternatives for 8-16GB RAM: Llama-3.2-8B-Instruct, NVIDIA-Nemotron-Nano-9B

Switch at runtime with `/mode fast` or `/mode local`.

## Security

All agent actions flow through a single `OperationPipeline` — there are no alternate execution paths. See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete security analysis.

1. **Single Pipeline**: Every bash command and file operation goes through `OperationPipeline.execute()` which enforces validation, approval, undo recording, and audit logging in one place.
2. **SRT Mandatory**: Bash commands execute exclusively through the Anthropic Sandbox Runtime (SRT) — no `create_subprocess_shell` fallback. If SRT is not installed, bash commands fail with a clear error. Run `noether setup-sandbox` to install.
3. **Two-Tier Command Schemas**: The autonomous loop (`/auto`, `/explore-add`) can only use read-only commands (`ls`, `cat`, `grep`, `find`, `git status/log/diff`). The chat path has all commands but requires user approval for each.
4. **Filesystem Sandbox**: File operations restricted to the current working directory. No access to `.env`, secrets, credentials, `.ssh/`, `.aws/`, `.git/`.
5. **Approval Modals**: Commands and file writes in the chat path require user confirmation.
6. **Snapshot Undo**: Every file modification stores the full previous content, enabling reliable rollback.
7. **Audit Trail**: All operations logged to `.noether/audit.jsonl` with timestamps, targets, and outcomes.

## Development

### Running Tests

```bash
# Unit and Integration tests (200+ targets)
pytest tests/
```

### Debug Mode

```bash
# Run with verbose logging pumped directly to console (bypasses UI)
python -u run.py 2>&1 | tee debug.log
```

## Community

- [GitHub Issues](https://github.com/burning-phoenix/noether/issues) — Bug reports and release tracking
- [GitHub Discussions](https://github.com/burning-phoenix/noether/discussions) — Questions, ideas, show & tell

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

**The one rule:** All bash commands and file operations MUST go through `OperationPipeline.execute()`. See [ARCHITECTURE.md](ARCHITECTURE.md) for why.

### TODO

- Git patch diff integration, and just making the UI better, designing better inter-LLM orchestration mechanisms etc. This is intended to be a base for building a more intuitive LLM-based code editor which remains open-source.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
