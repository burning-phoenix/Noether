# Contributing to Noether

Thanks for your interest in contributing! Noether is a terminal-based multi-agent code editor, and we welcome contributions of all kinds.

## Ways to Contribute

- **Bug Reports** — Found something broken? Open a [GitHub Issue](https://github.com/burning-phoenix/noether/issues).
- **Feature Requests** — Have an idea? Start a thread in [GitHub Discussions](https://github.com/burning-phoenix/noether/discussions) (Ideas category).
- **Code** — Fix bugs, add features, improve performance.
- **Documentation** — Improve README, architecture docs, or inline comments.

## Reporting Bugs

When filing a bug report, please include:

1. Steps to reproduce the issue
2. Expected behavior vs. actual behavior
3. Your environment: OS, Python version, `noether --version` output
4. Any error messages or tracebacks (from the Trace tab or terminal)

## Development Setup

```bash
# Clone the repo
git clone https://github.com/burning-phoenix/noether.git
cd noether

# Install in development mode with test dependencies
pip install -e ".[dev]"

# Run the test suite
pytest tests/
```

### Optional: Local Coder Model

If you want to work on local model integration:

```bash
pip install -e ".[local]"
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

## Pull Request Process

1. **Fork** the repo and create a feature branch from `main`.
2. **Follow existing patterns** — read through the files you're modifying to match style and conventions.
3. **Read [ARCHITECTURE.md](ARCHITECTURE.md)** before making structural changes — it covers design philosophy, the operation pipeline, security architecture, threading rules, and the full module reference.
4. **Run the tests** before submitting: `pytest tests/`
5. **Submit a PR** with a clear description of what changed and why.

## The One Rule

> **All bash commands and file operations MUST go through `OperationPipeline.execute()`.**

This is the single enforcement point for schema validation, SRT sandbox isolation, user approval, undo recording, and audit logging. Never create alternate execution paths (e.g., calling `subprocess.run()` directly, using `asyncio.create_subprocess_shell()`, or adding new methods to `SandboxCommandExecutor`). Bypassing the pipeline bypasses the entire security architecture.

The only exceptions are documented in `ARCHITECTURE.md` (Section 10).

## Project Structure

```
src/
  ui/           # Textual TUI (app shell, screens, modals)
  agents/       # Planner, Coder, Explore agents
  orchestration/# Pipeline, undo, task queue, agentic loop
  sandbox/      # SRT executor, schemas, filesystem sandbox
  backends/     # Fireworks API client
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component map, module reference, and contributor patterns.

## Community

- [GitHub Issues](https://github.com/burning-phoenix/noether/issues) — Bug reports and release tracking
- [GitHub Discussions](https://github.com/burning-phoenix/noether/discussions) — Questions, ideas, show & tell

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

Noether is licensed under the [GNU General Public License v3.0](LICENSE). By contributing, you agree that your contributions will be licensed under the same terms.
