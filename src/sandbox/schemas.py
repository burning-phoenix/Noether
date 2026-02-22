from typing import Literal, List, Optional
from pydantic import BaseModel, Field, validator
from pathlib import Path

# Git subcommands allowed for read-only operations
GIT_READONLY_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "rev-parse", "stash",
})


class ReadOnlyShellCommand(BaseModel):
    """Schema for read-only bash commands (auto loop, no approval needed)."""

    command: Literal[
        # File exploration
        "ls", "cat", "grep", "find", "tree", "head", "tail", "wc",
        # Diffing
        "diff",
        # Info utilities
        "pwd", "which", "env", "printenv", "date", "whoami",
        # Version control (read-only subcommands enforced below)
        "git",
    ] = Field(description="Allowed read-only command (whitelist only)")

    args: List[str] = Field(
        default=[],
        description="Command arguments",
    )

    cwd: Optional[str] = Field(
        default=None,
        description="Working directory (within project only)"
    )

    @validator('args')
    def validate_args(cls, args, values):
        """Validate arguments — block path traversal and enforce git subcommands."""
        # Block path traversal patterns
        dangerous_patterns = ['../', '~/', '/etc/', '/root/', '/var/', '/usr/']
        for arg in args:
            for pattern in dangerous_patterns:
                if pattern in arg:
                    raise ValueError(f"Path traversal blocked: {pattern}")

        # Git subcommand validation
        command = values.get('command')
        if command == 'git' and args:
            subcommand = args[0]
            if subcommand not in GIT_READONLY_SUBCOMMANDS:
                raise ValueError(
                    f"Git subcommand '{subcommand}' not allowed in read-only mode. "
                    f"Allowed: {', '.join(sorted(GIT_READONLY_SUBCOMMANDS))}"
                )

        return args

    @validator('cwd')
    def validate_cwd(cls, cwd):
        """Ensure cwd is within project."""
        if cwd is None:
            return cwd

        project_root = Path.cwd().resolve()
        target = Path(cwd).resolve()

        try:
            target.relative_to(project_root)
        except ValueError:
            if target != project_root:
                raise ValueError(f"cwd must be within project root: {project_root}")

        return cwd


class FullShellCommand(BaseModel):
    """Schema for all bash commands (chat path, approval required)."""

    command: Literal[
        # File exploration
        "ls", "cat", "grep", "find", "tree", "head", "tail", "wc",
        # Text processing
        "sed", "awk", "cut", "sort", "uniq", "tr", "diff",
        # Python tools
        "python", "python3", "pip", "pip3", "pytest", "pylint", "mypy",
        "black", "flake8", "ruff", "isort",
        # Version control
        "git",
        # File operations (safe ones)
        "touch", "mkdir", "cp", "mv", "rm",
        # Other utilities
        "echo", "pwd", "which", "env", "printenv", "date", "whoami",
        # Node/JS (if needed)
        "node", "npm", "npx", "yarn",
    ] = Field(description="Allowed command (whitelist only)")

    args: List[str] = Field(
        default=[],
        description="Command arguments",
    )

    cwd: Optional[str] = Field(
        default=None,
        description="Working directory (within project only)"
    )

    @validator('args')
    def validate_args(cls, args, values):
        """
        Validate arguments — block path traversal and restrict python patterns.

        Shell operators (|, ||, &&, ;) are allowed because:
        1. The SRT sandbox provides the actual security isolation
        2. Piped commands are legitimate (find | head, grep | wc)
        3. Blocking these breaks normal shell usage patterns
        """
        # Block path traversal patterns
        dangerous_patterns = ['../', '~/', '/etc/', '/root/', '/var/', '/usr/']
        for arg in args:
            for pattern in dangerous_patterns:
                if pattern in arg:
                    raise ValueError(f"Path traversal blocked: {pattern}")

        # Python pattern restriction (12.8): block -c (inline code execution)
        command = values.get('command')
        if command in ("python", "python3") and args:
            if args[0] == "-c":
                raise ValueError(
                    "Inline Python execution (-c) is not allowed. "
                    "Use 'python script.py' or 'python -m module' instead."
                )

        return args

    @validator('cwd')
    def validate_cwd(cls, cwd):
        """Ensure cwd is within project."""
        if cwd is None:
            return cwd

        project_root = Path.cwd().resolve()
        target = Path(cwd).resolve()

        try:
            target.relative_to(project_root)
        except ValueError:
            if target != project_root:
                raise ValueError(f"cwd must be within project root: {project_root}")

        return cwd


# Backward-compatible alias — existing code that imports ShellCommand keeps working
ShellCommand = FullShellCommand
