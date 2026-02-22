"""
Explore Agent for codebase analysis using DeepSeek V3.

DeepSeek is used on-demand (not persistent) for:
- Architecture analysis
- Error investigation
- Dependency mapping
- Test coverage analysis
- Security review
"""

from typing import Iterator, Optional
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
import re

from ..backends import FireworksBackend
from ..sandbox import FileSystemSandbox
from ..orchestration.prompts import DEEPSEEK_EXPLORE_SYSTEM
from ..prompts import EXPLORE_ERROR_PROMPT, EXPLORE_FILE_PROMPT


def find_file_in_project(name: str, project_root: Optional[Path] = None) -> Optional[Path]:
    """
    Find a file by name anywhere in the project tree.

    Handles bare filenames (``msae.py``), relative paths
    (``src/models/msae.py``), and absolute paths.
    Prefers matches inside ``src/`` over vendored/external dirs.

    Returns the resolved absolute Path, or None if not found.
    """
    root = project_root or Path.cwd()
    p = Path(name)

    # Already absolute and exists
    if p.is_absolute():
        return p if p.is_file() else None

    # Relative from project root
    candidate = root / p
    if candidate.is_file():
        return candidate

    # Bare filename — search the tree, prefer src/ matches
    # Only skip top-level dirs that contain non-project content
    skip_toplevel = {"external_repos", "models", "streaming-llm",
                     ".venv", "venv", ".tox", "node_modules"}
    # Always skip these anywhere in the tree
    skip_anywhere = {".git", "__pycache__"}
    best: Optional[Path] = None
    for match in root.rglob(p.name):
        if not match.is_file():
            continue
        rel = match.relative_to(root)
        # Skip hidden/cache dirs anywhere
        if any(part in skip_anywhere for part in rel.parts):
            continue
        # Skip certain top-level directories (but not nested ones like src/models/)
        if rel.parts[0] in skip_toplevel:
            continue
        # Prefer files under src/
        if "src" in rel.parts[:2]:
            return match
        if best is None:
            best = match

    return best


# ---------------------------------------------------------------------------
# Definition-aware patterns (Python + JS/TS)
# ---------------------------------------------------------------------------
_DEF_RE = re.compile(
    r"^(\s*)(class |def |async def |function |export function |export default function |export class )",
)


def focused_read(
    file_path: Path,
    target: str,
    context_before: int = 5,
    context_after: int = 40,
) -> Optional[str]:
    """
    Read a focused section of a file around a target identifier.

    Pure Python — no LLM, no subprocess.  Works like:
      grep -n <target> <file>  →  find line number
      sed -n 'start,end p'    →  extract section

    For class/function targets it finds the definition and reads until
    the next same-indent-or-less definition (i.e. the whole body).

    Args:
        file_path: Absolute path to the file.
        target: Identifier to search for (class name, function name,
                or any grep-like string).
        context_before: Lines to include before the match.
        context_after: Max lines to include after the match (capped by
                       the next same-level definition or EOF).

    Returns:
        Numbered content string, or None if target not found.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return None

    lines = content.split("\n")

    # 1. Find the target line — try definition match first, then plain grep
    target_line: Optional[int] = None
    target_indent: Optional[int] = None

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Definition match: "class GameInfo" or "def process"
        if _DEF_RE.match(line) and target in stripped:
            target_line = i
            target_indent = len(line) - len(stripped)
            break

    # Fallback: plain substring search
    if target_line is None:
        for i, line in enumerate(lines):
            if target in line:
                target_line = i
                target_indent = None
                break

    if target_line is None:
        return None

    # 2. Determine the end of the section
    start = max(0, target_line - context_before)

    if target_indent is not None:
        # Read until next definition at same or lesser indent
        end = min(target_line + context_after, len(lines))
        for j in range(target_line + 1, end):
            m = _DEF_RE.match(lines[j])
            if m:
                indent = len(m.group(1))
                if indent <= target_indent:
                    end = j
                    break
    else:
        end = min(target_line + context_after, len(lines))

    # 3. Format with line numbers
    section = []
    for j in range(start, end):
        section.append(f"L{j+1}: {lines[j]}")

    rel = file_path.name
    return f"# {rel} (lines {start+1}-{end}, {len(lines)} total)\n```\n" + "\n".join(section) + "\n```"


def skim_file(content: str, file_path: str, context_lines: int = 3) -> str:
    """
    Token-economical file skim: definitions + context below each.

    Strategy:
    1. Small files (<=80 lines) → return full content.
    2. Larger files → grep every class/function definition, then
       include *context_lines* lines below it.  This typically
       captures the docstring opening line.  Short functions fit
       entirely inside the window.

    No head/tail filler — every token carries structural info.
    """
    lines = content.split("\n")
    line_count = len(lines)

    # Small files: full content is cheapest
    if line_count <= 80:
        return f"# {file_path} ({line_count} lines)\n```\n{content}\n```"

    parts = [f"# {file_path} ({line_count} lines)\n```"]

    # Collect definition line indices
    defs: list[int] = []
    for i, line in enumerate(lines):
        if _DEF_RE.match(line):
            defs.append(i)

    if not defs:
        # No definitions found — first 30 lines only
        parts.append("\n".join(f"L{i+1}: {l}" for i, l in enumerate(lines[:30])))
        if line_count > 30:
            parts.append(f"  ... ({line_count - 30} more lines)")
        parts.append("```")
        return "\n".join(parts)

    # Walk definitions, emit each with context, skip overlapping lines
    shown: set[int] = set()
    for def_idx in defs:
        end = min(def_idx + context_lines + 1, line_count)
        block_lines = []
        for j in range(def_idx, end):
            if j not in shown:
                block_lines.append(f"L{j+1}: {lines[j]}")
                shown.add(j)
        if block_lines:
            parts.append("\n".join(block_lines))

    if len(defs) > 50:
        parts.append(f"  ... {len(defs)} definitions total")

    parts.append("```")
    return "\n".join(parts)


@dataclass
class ExploreReport:
    """Report generated by exploration."""
    title: str
    explore_type: str
    summary: str
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    raw_content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        """Convert report to markdown format."""
        lines = [
            f"# {self.title}",
            f"*Generated: {self.timestamp.strftime('%Y-%m-%d %H:%M')}*",
            "",
            "## Summary",
            self.summary,
            "",
        ]

        if self.findings:
            lines.append("## Key Findings")
            for finding in self.findings:
                lines.append(f"- {finding}")
            lines.append("")

        if self.recommendations:
            lines.append("## Recommendations")
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"{i}. {rec}")
            lines.append("")

        if self.relevant_files:
            lines.append("## Relevant Files")
            for file in self.relevant_files:
                lines.append(f"- `{file}`")
            lines.append("")

        return "\n".join(lines)


class ExploreAgent:
    """
    Agent for codebase exploration using DeepSeek V3.

    Stateless agent - each exploration is independent.
    Used for on-demand analysis tasks.
    """

    # Bash commands to run per explore type: list of (command, description)
    EXPLORE_COMMANDS: dict[str, list[tuple[str, str]]] = {
        "architecture": [
            ("tree -I __pycache__ -L 4", "Project directory tree (4 levels)"),
            ('wc -l $(find . -name "*.py")', "Line counts per Python file"),
            ("head -3 README* 2>/dev/null", "First lines of README files"),
        ],
        "errors": [
            ('grep -rn "except\\|raise" --include="*.py" | head -60', "Exception/raise usage"),
            ('grep -rn "logging\\." --include="*.py" | head -30', "Logging calls"),
        ],
        "dependencies": [
            ('grep -rn "^import\\|^from" --include="*.py" | sort -u', "All Python imports"),
            ("cat requirements*.txt pyproject.toml 2>/dev/null", "Dependency files"),
        ],
        "tests": [
            ('find . -name "test_*.py" -o -name "*_test.py"', "Test file locations"),
            ('grep -c "def test_" $(find . -name "test_*.py") 2>/dev/null', "Test count per file"),
        ],
        "security": [
            ('grep -rn "password\\|secret\\|token\\|api_key" --include="*.py" | head -30', "Sensitive strings in code"),
            ('find . -name ".env*"', "Environment files"),
            ('grep -rn "subprocess\\|os.system\\|eval(" --include="*.py"', "Dangerous function calls"),
        ],
        "api": [
            ('grep -rn "@app\\.\\|@router\\.\\|def.*route" --include="*.py"', "Route/endpoint decorators"),
            ('grep -rn "def.*endpoint\\|def.*view" --include="*.py"', "Endpoint/view functions"),
        ],
    }

    # Exploration types and their focus areas
    EXPLORE_TYPES = {
        "architecture": {
            "description": "Analyze overall code structure and patterns",
            "file_patterns": ["**/*.py", "**/README*", "**/setup.py", "**/pyproject.toml"],
            "focus": "structure, modules, design patterns, dependencies",
        },
        "errors": {
            "description": "Analyze error handling and potential issues",
            "file_patterns": ["**/*.py"],
            "focus": "try/except blocks, error propagation, edge cases, logging",
        },
        "dependencies": {
            "description": "Map imports and module relationships",
            "file_patterns": ["**/*.py", "**/requirements*.txt", "**/pyproject.toml"],
            "focus": "imports, circular dependencies, version constraints",
        },
        "tests": {
            "description": "Analyze test coverage and patterns",
            "file_patterns": ["**/test_*.py", "**/*_test.py", "**/tests/**/*.py", "**/conftest.py"],
            "focus": "test coverage, assertions, fixtures, mocking patterns",
        },
        "security": {
            "description": "Security review for common vulnerabilities",
            "file_patterns": ["**/*.py", "**/.env*", "**/config*"],
            "focus": "input validation, secrets, authentication, SQL/command injection",
        },
        "api": {
            "description": "Analyze API endpoints and interfaces",
            "file_patterns": ["**/*.py", "**/routes/**", "**/api/**", "**/views/**"],
            "focus": "endpoints, request/response handling, validation, documentation",
        },
    }

    def __init__(
        self,
        backend: FireworksBackend,
        sandbox: Optional[FileSystemSandbox] = None,
        project_root: Optional[str] = None,
    ):
        """
        Initialize the explore agent.

        Args:
            backend: Fireworks backend configured for DeepSeek
            sandbox: File system sandbox for safe file access
            project_root: Project root directory
        """
        self.backend = backend
        self.sandbox = sandbox
        self.project_root = Path(project_root) if project_root else Path.cwd()

    def get_exploration_commands(self, explore_type: str) -> list[tuple[str, str]]:
        """
        Return the list of bash commands for the given explore type.

        Args:
            explore_type: One of the EXPLORE_TYPES keys.

        Returns:
            List of (command, description) tuples, empty if type unknown.
        """
        return list(self.EXPLORE_COMMANDS.get(explore_type, []))

    def explore_with_commands(
        self,
        explore_type: str = "architecture",
        query: Optional[str] = None,
        command_results: Optional[dict[str, str]] = None,
        max_files: int = 20,
        max_content_per_file: int = 2000,
    ) -> Iterator[str]:
        """
        Explore the codebase with command output injected into the prompt.

        Same as explore() but enriches the prompt with pre-collected
        bash command results.

        Args:
            explore_type: Type of exploration
            query: Optional specific query
            command_results: Mapping of command string -> stdout output
            max_files: Maximum files to include in context
            max_content_per_file: Max characters per file

        Yields:
            Analysis text chunks
        """
        if explore_type not in self.EXPLORE_TYPES:
            yield f"Unknown explore type: {explore_type}. "
            yield f"Available: {', '.join(self.EXPLORE_TYPES.keys())}"
            return

        config = self.EXPLORE_TYPES[explore_type]

        yield f"[Scanning project for {explore_type} analysis...]\n\n"

        context = self._gather_context(
            config["file_patterns"],
            max_files,
            max_content_per_file,
        )

        if not context["files"] and not command_results:
            yield "No relevant files found for analysis."
            return

        yield f"[Found {len(context['files'])} relevant files]\n\n"

        prompt = self._build_prompt(
            explore_type, config, context, query,
            command_results=command_results,
        )

        for chunk in self.backend.stream(prompt, system=DEEPSEEK_EXPLORE_SYSTEM):
            yield chunk

    def explore(
        self,
        explore_type: str = "architecture",
        query: Optional[str] = None,
        max_files: int = 20,
        max_content_per_file: int = 2000,
    ) -> Iterator[str]:
        """
        Explore the codebase with streaming output.

        Args:
            explore_type: Type of exploration (architecture, errors, etc.)
            query: Optional specific query to focus on
            max_files: Maximum files to include in context
            max_content_per_file: Max characters per file

        Yields:
            Analysis text chunks
        """
        if explore_type not in self.EXPLORE_TYPES:
            yield f"Unknown explore type: {explore_type}. "
            yield f"Available: {', '.join(self.EXPLORE_TYPES.keys())}"
            return

        config = self.EXPLORE_TYPES[explore_type]

        # Gather context
        yield f"[Scanning project for {explore_type} analysis...]\n\n"

        context = self._gather_context(
            config["file_patterns"],
            max_files,
            max_content_per_file,
        )

        if not context["files"]:
            yield "No relevant files found for analysis."
            return

        yield f"[Found {len(context['files'])} relevant files]\n\n"

        # Build prompt
        prompt = self._build_prompt(explore_type, config, context, query)

        # Stream analysis
        for chunk in self.backend.stream(prompt, system=DEEPSEEK_EXPLORE_SYSTEM):
            yield chunk

    def explore_error(
        self,
        error_message: str,
        traceback: str,
        max_files: int = 10,
    ) -> Iterator[str]:
        """
        Analyze a specific error.

        Args:
            error_message: The error message
            traceback: Full traceback
            max_files: Maximum files to include

        Yields:
            Analysis text chunks
        """
        yield "[Analyzing error...]\n\n"

        # Extract file paths from traceback
        files_in_trace = self._extract_files_from_traceback(traceback)

        # Read those files
        context = self._gather_specific_files(files_in_trace)

        prompt = EXPLORE_ERROR_PROMPT.format(
            error_message=error_message,
            traceback=traceback,
            context_content=context['content']
        )

        for chunk in self.backend.stream(prompt, system=DEEPSEEK_EXPLORE_SYSTEM):
            yield chunk

    def explore_file(
        self,
        file_path: str,
        query: Optional[str] = None,
    ) -> Iterator[str]:
        """
        Analyze a specific file.

        Uses definition-aware skimming: greps for class/function
        definitions and shows a few lines of context below each.

        Args:
            file_path: Path to the file
            query: Optional specific question about the file

        Yields:
            Analysis text chunks
        """
        content = self._read_file_safe(file_path)
        if content is None:
            yield f"Cannot read file: {file_path}"
            return
        skimmed = skim_file(content, file_path)

        query_section = f"Specific question: {query}\n\n" if query else ""
        prompt = EXPLORE_FILE_PROMPT.format(
            skimmed=skimmed,
            query_section=query_section
        )

        for chunk in self.backend.stream(prompt, system=DEEPSEEK_EXPLORE_SYSTEM):
            yield chunk

    def _gather_context(
        self,
        patterns: list[str],
        max_files: int,
        max_content: int,
    ) -> dict:
        """Gather context from matching files."""
        files = []
        content_parts = []
        total_chars = 0
        max_total = max_files * max_content

        for pattern in patterns:
            if self.sandbox:
                matching = list(self.sandbox.safe_glob(pattern))
            else:
                matching = list(self.project_root.glob(pattern))

            for path in matching:
                if len(files) >= max_files:
                    break

                if total_chars >= max_total:
                    break

                content = self._read_file_safe(str(path))
                if content:
                    # Truncate if needed
                    if len(content) > max_content:
                        content = content[:max_content] + "\n... (truncated)"

                    rel_path = path.relative_to(self.project_root) if path.is_absolute() else path
                    files.append(str(rel_path))
                    content_parts.append(f"### {rel_path}\n```\n{content}\n```")
                    total_chars += len(content)

        return {
            "files": files,
            "content": "\n\n".join(content_parts),
            "tree": self._get_tree() if self.sandbox else "",
        }

    def _gather_specific_files(self, file_paths: list[str]) -> dict:
        """Gather context from specific files."""
        files = []
        content_parts = []

        for path in file_paths:
            content = self._read_file_safe(path)
            if content:
                files.append(path)
                content_parts.append(f"### {path}\n```\n{content[:3000]}\n```")

        return {
            "files": files,
            "content": "\n\n".join(content_parts),
        }

    def _build_prompt(
        self,
        explore_type: str,
        config: dict,
        context: dict,
        query: Optional[str],
        command_results: Optional[dict[str, str]] = None,
    ) -> str:
        """Build the exploration prompt."""
        prompt = f"""Explore this codebase focusing on: {config['description']}

Focus areas: {config['focus']}

"""
        if query:
            prompt += f"Specific query: {query}\n\n"

        # Inject command output before file contents
        if command_results:
            prompt += "## Command Output\n\n"
            for cmd, output in command_results.items():
                prompt += f"### `{cmd}`\n```\n{output}\n```\n\n"

        if context.get("tree"):
            prompt += f"## Project Structure\n```\n{context['tree']}\n```\n\n"

        prompt += f"## Source Files\n\n{context['content']}\n\n"

        prompt += "Provide a comprehensive analysis with specific file:line references."

        return prompt

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read a file."""
        if self.sandbox:
            return self.sandbox.safe_read(path)

        try:
            full_path = self.project_root / path if not Path(path).is_absolute() else Path(path)
            return full_path.read_text(encoding="utf-8")
        except (IOError, UnicodeDecodeError):
            return None

    def _get_tree(self) -> str:
        """Get project tree."""
        if self.sandbox:
            return self.sandbox.get_project_tree(max_depth=3, max_files=50)
        return ""

    def _extract_files_from_traceback(self, traceback: str) -> list[str]:
        """Extract file paths from a Python traceback."""
        import re
        pattern = r'File "([^"]+)"'
        matches = re.findall(pattern, traceback)
        # Filter to project files
        project_files = []
        for match in matches:
            try:
                path = Path(match)
                if path.is_relative_to(self.project_root):
                    project_files.append(str(path.relative_to(self.project_root)))
                elif not path.is_absolute():
                    project_files.append(match)
            except (ValueError, TypeError):
                continue
        return project_files

    @classmethod
    def get_explore_types(cls) -> dict:
        """Get available exploration types."""
        return {
            name: config["description"]
            for name, config in cls.EXPLORE_TYPES.items()
        }
