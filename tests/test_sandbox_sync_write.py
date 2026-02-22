"""Tests for FileSystemSandbox sync write and skip_approval functionality.

These test the Phase 1 crash fix — ensuring that:
1. safe_write_sync writes files correctly without async
2. safe_write with skip_approval=True bypasses the approval callback
3. safe_delete with skip_approval=True bypasses the approval callback
4. Sandbox path validation still enforced in all cases
"""

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
def sandbox_deny(tmp_path):
    """Create a sandbox that always denies approval."""
    project = tmp_path / "project"
    project.mkdir()
    approval = AsyncMock(return_value=False)
    return FileSystemSandbox(project_root=str(project), approval_callback=approval)


class TestSafeWriteSync:
    """Test synchronous write (for worker threads after batch approval)."""

    def test_write_new_file(self, sandbox):
        success = sandbox.safe_write_sync("hello.py", "print('hello')")
        assert success is True
        path = Path(sandbox.project_root) / "hello.py"
        assert path.read_text() == "print('hello')"

    def test_write_creates_parent_dirs(self, sandbox):
        success = sandbox.safe_write_sync("src/models/user.py", "class User: pass")
        assert success is True
        path = Path(sandbox.project_root) / "src" / "models" / "user.py"
        assert path.read_text() == "class User: pass"

    def test_write_overwrites_existing(self, sandbox):
        path = Path(sandbox.project_root) / "app.py"
        path.write_text("old content")
        success = sandbox.safe_write_sync("app.py", "new content")
        assert success is True
        assert path.read_text() == "new content"

    def test_write_blocked_path_env(self, sandbox):
        success = sandbox.safe_write_sync(".env", "SECRET=123")
        assert success is False

    def test_write_blocked_path_pem(self, sandbox):
        success = sandbox.safe_write_sync("server.pem", "key data")
        assert success is False

    def test_write_read_only_path(self, sandbox):
        success = sandbox.safe_write_sync(".gitignore", "*.pyc")
        assert success is False

    def test_write_outside_project(self, sandbox):
        success = sandbox.safe_write_sync("../../../etc/passwd", "hacked")
        assert success is False

    def test_write_does_not_call_approval(self, sandbox):
        """Sync write should never touch the approval callback."""
        sandbox.safe_write_sync("test.py", "content")
        sandbox.approval_callback.assert_not_called()


class TestSkipApproval:
    """Test skip_approval parameter on safe_write and safe_delete."""

    @pytest.mark.asyncio
    async def test_write_skip_approval_bypasses_callback(self, sandbox_deny):
        """With skip_approval=True, write should succeed even if callback denies."""
        result = await sandbox_deny.safe_write(
            "test.py", "content", skip_approval=True
        )
        assert result is True
        path = Path(sandbox_deny.project_root) / "test.py"
        assert path.read_text() == "content"
        # Callback should NOT have been called
        sandbox_deny.approval_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_without_skip_still_calls_callback(self, sandbox):
        """Without skip_approval, callback should be called."""
        await sandbox.safe_write("test.py", "content")
        sandbox.approval_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_skip_approval_still_checks_path(self, sandbox):
        """skip_approval should NOT bypass path validation."""
        result = await sandbox.safe_write(
            ".env", "SECRET=123", skip_approval=True
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_skip_approval_bypasses_callback(self, sandbox_deny):
        """With skip_approval=True, delete should succeed even if callback denies."""
        path = Path(sandbox_deny.project_root) / "deleteme.py"
        path.write_text("to be deleted")

        result = await sandbox_deny.safe_delete(
            "deleteme.py", skip_approval=True
        )
        assert result is True
        assert not path.exists()
        sandbox_deny.approval_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_without_skip_still_calls_callback(self, sandbox):
        """Without skip_approval, callback should be called."""
        path = Path(sandbox.project_root) / "deleteme.py"
        path.write_text("to be deleted")
        await sandbox.safe_delete("deleteme.py")
        sandbox.approval_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_skip_approval_still_checks_path(self, sandbox):
        """skip_approval should NOT bypass path validation."""
        result = await sandbox.safe_delete(
            ".env", skip_approval=True
        )
        assert result is False
