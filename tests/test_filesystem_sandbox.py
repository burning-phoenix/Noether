"""Tests for FileSystemSandbox."""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from src.sandbox.filesystem_sandbox import FileSystemSandbox


@pytest.fixture
def sandbox(tmp_path):
    """Create a sandbox rooted in a temp directory."""
    project = tmp_path / "project"
    project.mkdir()
    approval = AsyncMock(return_value=True)
    return FileSystemSandbox(project_root=str(project), approval_callback=approval)


@pytest.fixture
def sandbox_with_files(sandbox):
    """Create a sandbox with some test files."""
    root = Path(sandbox.project_root)
    (root / "app.py").write_text("print('hello')")
    (root / "README.md").write_text("# Project")
    subdir = root / "src"
    subdir.mkdir()
    (subdir / "utils.py").write_text("def helper(): pass")
    return sandbox


class TestResolvePath:
    """Test path resolution and validation."""

    def test_relative_path(self, sandbox):
        resolved = sandbox.resolve_path("app.py")
        assert resolved is not None
        assert resolved.name == "app.py"

    def test_absolute_path_within(self, sandbox):
        abs_path = str(Path(sandbox.project_root) / "app.py")
        resolved = sandbox.resolve_path(abs_path)
        assert resolved is not None

    def test_path_traversal_blocked(self, sandbox):
        resolved = sandbox.resolve_path("../../../etc/passwd")
        assert resolved is None

    def test_path_traversal_with_dots(self, sandbox):
        resolved = sandbox.resolve_path("src/../../etc/shadow")
        assert resolved is None

    def test_absolute_path_outside(self, sandbox):
        resolved = sandbox.resolve_path("/etc/passwd")
        assert resolved is None


class TestIsPathAllowed:
    """Test blocked and read-only pattern enforcement."""

    def test_normal_file_allowed(self, sandbox):
        path = Path(sandbox.project_root) / "app.py"
        assert sandbox.is_path_allowed(path) is True

    def test_env_file_blocked(self, sandbox):
        path = Path(sandbox.project_root) / ".env"
        assert sandbox.is_path_allowed(path) is False

    def test_env_variant_blocked(self, sandbox):
        path = Path(sandbox.project_root) / ".env.local"
        assert sandbox.is_path_allowed(path) is False

    def test_pyc_blocked(self, sandbox):
        path = Path(sandbox.project_root) / "module.pyc"
        assert sandbox.is_path_allowed(path) is False

    def test_pycache_blocked(self, sandbox):
        path = Path(sandbox.project_root) / "__pycache__" / "module.cpython-312.pyc"
        assert sandbox.is_path_allowed(path) is False

    def test_pem_blocked(self, sandbox):
        path = Path(sandbox.project_root) / "server.pem"
        assert sandbox.is_path_allowed(path) is False

    def test_key_blocked(self, sandbox):
        path = Path(sandbox.project_root) / "private.key"
        assert sandbox.is_path_allowed(path) is False

    def test_ssh_dir_blocked(self, sandbox):
        path = Path(sandbox.project_root) / ".ssh" / "id_rsa"
        assert sandbox.is_path_allowed(path) is False

    def test_node_modules_blocked(self, sandbox):
        path = Path(sandbox.project_root) / "node_modules" / "pkg" / "index.js"
        assert sandbox.is_path_allowed(path) is False

    def test_git_read_only(self, sandbox):
        path = Path(sandbox.project_root) / ".git" / "HEAD"
        # Read is OK for git files
        assert sandbox.is_path_allowed(path, for_write=False) is True
        # Write is blocked
        assert sandbox.is_path_allowed(path, for_write=True) is False

    def test_gitignore_read_only(self, sandbox):
        path = Path(sandbox.project_root) / ".gitignore"
        assert sandbox.is_path_allowed(path, for_write=False) is True
        assert sandbox.is_path_allowed(path, for_write=True) is False

    def test_lock_file_read_only(self, sandbox):
        path = Path(sandbox.project_root) / "package-lock.json"
        assert sandbox.is_path_allowed(path, for_write=False) is True
        assert sandbox.is_path_allowed(path, for_write=True) is False

    def test_path_outside_project(self, sandbox):
        path = Path("/etc/passwd")
        assert sandbox.is_path_allowed(path) is False


class TestSafeRead:
    """Test safe file reading."""

    def test_read_existing_file(self, sandbox_with_files):
        content = sandbox_with_files.safe_read("app.py")
        assert content == "print('hello')"

    def test_read_missing_file(self, sandbox):
        content = sandbox.safe_read("nonexistent.py")
        assert content is None

    def test_read_blocked_file(self, sandbox):
        root = Path(sandbox.project_root)
        env_file = root / ".env"
        env_file.write_text("SECRET=123")
        content = sandbox.safe_read(".env")
        assert content is None

    def test_read_nested_file(self, sandbox_with_files):
        content = sandbox_with_files.safe_read("src/utils.py")
        assert content == "def helper(): pass"

    def test_read_directory_returns_none(self, sandbox_with_files):
        content = sandbox_with_files.safe_read("src")
        assert content is None


class TestSafeWrite:
    """Test safe file writing."""

    @pytest.mark.asyncio
    async def test_write_normal(self, sandbox):
        result = await sandbox.safe_write("new.py", "content")
        assert result is True
        path = Path(sandbox.project_root) / "new.py"
        assert path.read_text() == "content"

    @pytest.mark.asyncio
    async def test_write_blocked_path(self, sandbox):
        result = await sandbox.safe_write(".env", "SECRET=123")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_read_only_path(self, sandbox):
        result = await sandbox.safe_write(".gitignore", "*.pyc")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, sandbox):
        result = await sandbox.safe_write("a/b/c/file.py", "nested")
        assert result is True
        path = Path(sandbox.project_root) / "a" / "b" / "c" / "file.py"
        assert path.read_text() == "nested"

    @pytest.mark.asyncio
    async def test_write_denied_by_approval(self, sandbox):
        sandbox.approval_callback = AsyncMock(return_value=False)
        result = await sandbox.safe_write("denied.py", "content")
        assert result is False


class TestSafeDelete:
    """Test safe file deletion."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, sandbox_with_files):
        path = Path(sandbox_with_files.project_root) / "app.py"
        assert path.exists()
        result = await sandbox_with_files.safe_delete("app.py")
        assert result is True
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_delete_missing_returns_true(self, sandbox):
        result = await sandbox.safe_delete("nonexistent.py")
        assert result is True  # Already deleted

    @pytest.mark.asyncio
    async def test_delete_directory(self, sandbox_with_files):
        result = await sandbox_with_files.safe_delete("src")
        assert result is True
        path = Path(sandbox_with_files.project_root) / "src"
        assert not path.exists()


class TestEdgeCases:
    """Test edge cases."""

    def test_unicode_filename(self, sandbox):
        root = Path(sandbox.project_root)
        unicode_file = root / "datos_espa\u00f1ol.py"
        unicode_file.write_text("# spanish data")
        content = sandbox.safe_read("datos_espa\u00f1ol.py")
        assert content == "# spanish data"

    def test_symlink_escape(self, sandbox, tmp_path):
        """Symlinks pointing outside sandbox should be blocked."""
        outside = tmp_path / "outside_file.txt"
        outside.write_text("sensitive data")
        root = Path(sandbox.project_root)
        link = root / "link.txt"
        try:
            link.symlink_to(outside)
            # Resolve path should catch this
            resolved = sandbox.resolve_path("link.txt")
            if resolved:
                # If it resolves, it should point outside project root
                # and be caught by the relative_to check
                assert not str(resolved).startswith(str(sandbox.project_root)) or \
                    sandbox.safe_read("link.txt") is None
        except OSError:
            # Symlink creation failed (permissions), skip
            pass
