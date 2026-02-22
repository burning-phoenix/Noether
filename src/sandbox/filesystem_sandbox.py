"""
File system sandbox for restricting file operations.

Ensures that all file operations are confined to the project directory
and blocks access to sensitive files.
"""

import fnmatch
from pathlib import Path
from typing import Callable, Awaitable, Optional, Generator


class FileSystemSandbox:
    """
    Sandbox for restricting file system access.

    All paths are validated to ensure they:
    1. Resolve within the project root
    2. Don't match blocked patterns
    3. Get approval for write operations
    """

    # Patterns for files that should never be accessed
    BLOCKED_PATTERNS = [
        # Git internals
        ".git/objects/*",
        ".git/hooks/*",
        ".git/config",

        # Compiled files
        "*.pyc",
        "__pycache__/*",
        "*.pyo",
        "*.so",
        "*.dylib",

        # Secrets and credentials
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*credentials*",
        "*secret*",
        "*password*",
        "*token*",
        "*.secrets",

        # SSH
        ".ssh/*",
        "*_rsa",
        "*_ed25519",
        "*_ecdsa",
        "known_hosts",
        "authorized_keys",

        # AWS/Cloud
        ".aws/*",
        ".gcloud/*",
        ".azure/*",

        # IDE/Editor configs (often contain tokens)
        ".vscode/settings.json",
        ".idea/*",

        # Node modules (too large, rarely needed)
        "node_modules/*",

        # Virtual environments
        "venv/*",
        ".venv/*",
        "env/*",
    ]

    # Patterns that are read-only (can read, cannot write)
    READ_ONLY_PATTERNS = [
        ".git/*",
        ".gitignore",
        ".gitattributes",
        "LICENSE*",
        "*.lock",
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
    ]

    def __init__(
        self,
        project_root: str,
        approval_callback: Callable[[str, str, str], Awaitable[bool]],
    ):
        """
        Initialize the file system sandbox.

        Args:
            project_root: Root directory for file operations
            approval_callback: Async function to request user approval
                               (operation, path, description) -> bool
        """
        self.project_root = Path(project_root).resolve()
        self.approval_callback = approval_callback

    def resolve_path(self, path: str) -> Optional[Path]:
        """
        Resolve a path and verify it's within the project.

        Args:
            path: Relative or absolute path

        Returns:
            Resolved Path if valid, None if outside project
        """
        try:
            # Handle absolute and relative paths
            if Path(path).is_absolute():
                resolved = Path(path).resolve()
            else:
                resolved = (self.project_root / path).resolve()

            # Verify path is within project root
            resolved.relative_to(self.project_root)
            return resolved

        except (ValueError, RuntimeError):
            return None

    def is_path_allowed(self, path: Path, for_write: bool = False) -> bool:
        """
        Check if a path is allowed by sandbox rules.

        Args:
            path: The path to check
            for_write: Whether this is a write operation

        Returns:
            True if path is allowed
        """
        try:
            rel_path = str(path.relative_to(self.project_root))
        except ValueError:
            return False

        # Check blocked patterns
        for pattern in self.BLOCKED_PATTERNS:
            if fnmatch.fnmatch(rel_path, pattern):
                return False
            # Also check filename only
            if fnmatch.fnmatch(path.name, pattern):
                return False

        # Check read-only patterns for write operations
        if for_write:
            for pattern in self.READ_ONLY_PATTERNS:
                if fnmatch.fnmatch(rel_path, pattern):
                    return False

        return True

    def safe_read(self, path: str) -> Optional[str]:
        """
        Safely read a file within sandbox.

        Args:
            path: Path to the file

        Returns:
            File contents if allowed and readable, None otherwise
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return None

        if not self.is_path_allowed(resolved, for_write=False):
            return None

        if not resolved.exists():
            return None

        if not resolved.is_file():
            return None

        try:
            return resolved.read_text(encoding="utf-8")
        except (IOError, UnicodeDecodeError):
            return None

    def safe_read_binary(self, path: str) -> Optional[bytes]:
        """
        Safely read a binary file within sandbox.

        Args:
            path: Path to the file

        Returns:
            File contents if allowed and readable, None otherwise
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return None

        if not self.is_path_allowed(resolved, for_write=False):
            return None

        if not resolved.exists():
            return None

        try:
            return resolved.read_bytes()
        except IOError:
            return None

    async def safe_write(
        self,
        path: str,
        content: str,
        description: str = "",
        skip_approval: bool = False,
    ) -> bool:
        """
        Safely write a file within sandbox.

        Args:
            path: Path to the file
            content: Content to write
            description: Description for approval dialog
            skip_approval: If True, skip the approval callback
                          (use when batch approval was already obtained)

        Returns:
            True if write was successful
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return False

        if not self.is_path_allowed(resolved, for_write=True):
            return False

        # Request approval for writes (unless already approved)
        if not skip_approval:
            rel_path = resolved.relative_to(self.project_root)
            approved = await self.approval_callback(
                "write",
                str(rel_path),
                description or f"Write {len(content)} characters to {rel_path}",
            )

            if not approved:
                return False

        try:
            # Create parent directories if needed
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return True
        except IOError:
            return False

    def safe_write_sync(
        self,
        path: str,
        content: str,
    ) -> bool:
        """
        Synchronous write — for use when approval was already obtained.

        Performs path resolution and sandbox checks but skips the
        async approval callback. Safe to call from worker threads.

        Args:
            path: Path to the file
            content: Content to write

        Returns:
            True if write was successful
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return False

        if not self.is_path_allowed(resolved, for_write=True):
            return False

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return True
        except IOError:
            return False


    async def safe_delete(
        self,
        path: str,
        description: str = "",
        skip_approval: bool = False,
    ) -> bool:
        """
        Safely delete a file within sandbox.

        Args:
            path: Path to the file
            description: Description for approval dialog
            skip_approval: If True, skip the approval callback
                          (use when batch approval was already obtained)

        Returns:
            True if delete was successful
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return False

        if not self.is_path_allowed(resolved, for_write=True):
            return False

        if not resolved.exists():
            return True  # Already deleted

        # Request approval for deletes (unless already approved)
        if not skip_approval:
            rel_path = resolved.relative_to(self.project_root)
            approved = await self.approval_callback(
                "delete",
                str(rel_path),
                description or f"Delete {rel_path}",
            )

            if not approved:
                return False

        try:
            if resolved.is_file():
                resolved.unlink()
            elif resolved.is_dir():
                import shutil
                shutil.rmtree(resolved)
            return True
        except IOError:
            return False

    def safe_glob(
        self,
        pattern: str,
        base: Optional[str] = None,
    ) -> Generator[Path, None, None]:
        """
        Safely glob within sandbox.

        Args:
            pattern: Glob pattern (e.g., "**/*.py")
            base: Base directory for glob (default: project root)

        Yields:
            Paths matching the pattern within sandbox
        """
        if base:
            base_path = self.resolve_path(base)
        else:
            base_path = self.project_root

        if not base_path:
            return

        for path in base_path.glob(pattern):
            if self.is_path_allowed(path, for_write=False):
                yield path

    def safe_listdir(self, path: str = ".") -> Optional[list[dict]]:
        """
        Safely list directory contents.

        Args:
            path: Directory path

        Returns:
            List of file info dicts, or None if not allowed
        """
        resolved = self.resolve_path(path)

        if not resolved:
            return None

        if not resolved.is_dir():
            return None

        files = []
        try:
            for item in resolved.iterdir():
                if self.is_path_allowed(item, for_write=False):
                    rel_path = item.relative_to(self.project_root)
                    files.append({
                        "name": item.name,
                        "path": str(rel_path),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else 0,
                    })
        except IOError:
            return None

        return sorted(files, key=lambda f: (not f["is_dir"], f["name"]))

    def get_project_tree(
        self,
        max_depth: int = 3,
        max_files: int = 100,
    ) -> str:
        """
        Get a tree view of the project structure.

        Args:
            max_depth: Maximum directory depth
            max_files: Maximum files to include

        Returns:
            Tree string representation
        """
        lines = [str(self.project_root.name) + "/"]
        file_count = 0

        def walk(dir_path: Path, prefix: str, depth: int):
            nonlocal file_count

            if depth > max_depth or file_count >= max_files:
                return

            try:
                items = sorted(
                    dir_path.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name)
                )
            except IOError:
                return

            items = [i for i in items if self.is_path_allowed(i, for_write=False)]

            for i, item in enumerate(items):
                if file_count >= max_files:
                    lines.append(f"{prefix}... (truncated)")
                    break

                is_last = i == len(items) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{item.name}")
                file_count += 1

                if item.is_dir():
                    next_prefix = prefix + ("    " if is_last else "│   ")
                    walk(item, next_prefix, depth + 1)

        walk(self.project_root, "", 0)

        return "\n".join(lines)
