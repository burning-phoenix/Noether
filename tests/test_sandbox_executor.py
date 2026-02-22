import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from src.sandbox.command_executor import SandboxCommandExecutor

@pytest.fixture
def project_root(tmp_path):
    # Setup mock project structure
    root = tmp_path / "test_project"
    root.mkdir()

    # Create fake CLI at a tmp location (simulates SRT install)
    cli_dir = tmp_path / "srt" / "dist"
    cli_dir.mkdir(parents=True)
    (cli_dir / "cli.js").touch()

    # Create project-local config
    (root / ".srt-config.json").touch()

    return root

@pytest.fixture
def executor(project_root, tmp_path, monkeypatch):
    # Point SRT_PATH to the fake CLI
    cli_js = tmp_path / "srt" / "dist" / "cli.js"
    monkeypatch.setenv("SRT_PATH", str(cli_js))
    # Mock _sanity_check since the fake cli.js can't actually execute
    with patch.object(SandboxCommandExecutor, '_sanity_check', return_value=True):
        return SandboxCommandExecutor(project_root=project_root)

@pytest.mark.asyncio
async def test_initialization(executor, project_root, tmp_path):
    assert executor.project_root == project_root
    # Config resolves to project-local .srt-config.json
    assert executor.srt_config == project_root / ".srt-config.json"
    # CLI path comes from SRT_PATH env var
    assert executor.cli_js_path == tmp_path / "srt" / "dist" / "cli.js"

@pytest.mark.asyncio
async def test_execute_safe_command(executor):
    # Mock subprocess execution
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"file.txt\n", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process
        
        result = await executor.execute("ls")
        
        assert result["success"] is True
        assert result["command"] == "ls"
        assert "file.txt" in result["stdout"]
        
        # Verify arguments passed to node
        args = mock_exec.call_args[0]
        assert args[0] == "node"
        assert str(executor.cli_js_path) in args[1]
        assert "--settings" in args
        assert "-c" in args
        assert "ls" in args

@pytest.mark.asyncio
async def test_execute_timeout(executor):
    # Mock timeout
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        # Simulate long running process
        mock_process.communicate.side_effect = asyncio.TimeoutError() 
        mock_exec.return_value = mock_process
        
        result = await executor.execute("sleep 60")
        
        assert result["success"] is False
        assert result["error"] == "Timeout"
        assert mock_process.kill.called

@pytest.mark.asyncio
async def test_missing_config_falls_back(tmp_path, monkeypatch):
    # Point SRT_PATH to nonexistent file so global ~/.noether/ doesn't interfere
    monkeypatch.setenv("SRT_PATH", str(tmp_path / "nonexistent" / "cli.js"))
    # Test with non-existent SRT setup -- should fall back to direct execution
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    executor = SandboxCommandExecutor(project_root=empty_root)

    # SRT not available, falls back to direct execution
    assert executor.is_available is False

    # Execute should still work via fallback
    result = await executor.execute("echo hello")
    assert result["sandboxed"] is False


@pytest.mark.asyncio
async def test_missing_config_sandboxed_fails(tmp_path, monkeypatch):
    # Point SRT_PATH to nonexistent file so global ~/.noether/ doesn't interfere
    monkeypatch.setenv("SRT_PATH", str(tmp_path / "nonexistent" / "cli.js"))
    # Test the sandboxed path directly when config is missing
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    executor = SandboxCommandExecutor(project_root=empty_root)

    # Direct call to sandboxed execution should fail
    result = await executor._execute_sandboxed("ls")
    assert result["success"] is False
