import asyncio
import subprocess
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger("noether.sandbox")

# Output truncation limit (12.6)
MAX_OUTPUT_CHARS = 4000


class SandboxCommandExecutor:
    """
    Executes commands using the Anthropic Sandbox Runtime (srt).
    Wraps the 'srt' CLI tool to enforce network and filesystem isolation.
    No fallback — SRT is mandatory for bash execution.
    """

    def __init__(self,
                 project_root: Optional[Path] = None,
                 srt_config_path: Optional[Path] = None,
                 timeout: int = 30):
        self.project_root = project_root or Path.cwd()
        self.timeout = timeout

        # Resolve SRT CLI path: env override > global install
        self.cli_js_path = self._resolve_cli_path()

        # Resolve config: project-local > global default
        self.srt_config = srt_config_path or self._resolve_config_path()

        # Check availability at init time
        self._srt_available = self.validate_setup()

        if not self._srt_available:
            logger.warning("Sandbox not available. Bash commands will be rejected until SRT is installed.")

    @property
    def is_available(self) -> bool:
        """Check if SRT sandbox is available."""
        return self._srt_available

    async def execute(self,
                     command: str,
                     cwd: Optional[Path] = None,
                     env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Execute a command using SRT sandbox. No direct fallback.

        Returns:
            Dict containing success status, returncode, stdout, stderr.
        """
        if not self._srt_available:
            return {
                "success": False,
                "returncode": -1,
                "error": "SRT not installed",
                "stdout": "",
                "stderr": "SRT not installed. Run `noether setup-sandbox` to install it.",
                "command": command,
                "sandboxed": False,
            }

        return await self._execute_sandboxed(command, cwd, env)

    async def _execute_sandboxed(self,
                                  command: str,
                                  cwd: Optional[Path] = None,
                                  env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Execute a command within the SRT sandbox."""
        if cwd is None:
            cwd = self.project_root

        if not self.srt_config.exists():
            return {
                "success": False,
                "error": "Configuration missing. Run noether setup-sandbox.",
                "returncode": -1,
                "stdout": "",
                "stderr": f"Config file not found: {self.srt_config}"
            }

        sandboxed_cmd = [
            "node",
            str(self.cli_js_path),
            "--settings", str(self.srt_config),
            "-c", command
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *sandboxed_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=env or os.environ.copy()
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )

                stdout = stdout_data.decode().strip()
                stderr = stderr_data.decode().strip()

                # Truncate output (12.6)
                if len(stdout) > MAX_OUTPUT_CHARS:
                    stdout = stdout[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
                if len(stderr) > MAX_OUTPUT_CHARS:
                    stderr = stderr[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

                return {
                    "success": process.returncode == 0,
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "command": command,
                    "sandboxed": True,
                }

            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass

                return {
                    "success": False,
                    "returncode": -1,
                    "error": "Timeout",
                    "stdout": "",
                    "stderr": f"Command execution timed out after {self.timeout}s",
                    "command": command,
                    "sandboxed": True,
                }

        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "error": "Execution Failed",
                "stdout": "",
                "stderr": str(e),
                "command": command,
                "sandboxed": True,
            }

    def execute_sync(self,
                     command: str,
                     cwd: Optional[Path] = None,
                     env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Synchronous execution via subprocess.run. No event loop needed.
        This is the primary execution path used by OperationPipeline.

        Returns:
            Dict containing success status, returncode, stdout, stderr.
        """
        if not self._srt_available:
            return {
                "success": False,
                "returncode": -1,
                "error": "SRT not installed",
                "stdout": "",
                "stderr": "SRT not installed. Run `noether setup-sandbox` to install it.",
                "command": command,
                "sandboxed": False,
            }

        if cwd is None:
            cwd = self.project_root

        if not self.srt_config.exists():
            return {
                "success": False,
                "error": "Configuration missing. Run noether setup-sandbox.",
                "returncode": -1,
                "stdout": "",
                "stderr": f"Config file not found: {self.srt_config}",
                "command": command,
                "sandboxed": True,
            }

        sandboxed_cmd = [
            "node",
            str(self.cli_js_path),
            "--settings", str(self.srt_config),
            "-c", command
        ]

        try:
            result = subprocess.run(
                sandboxed_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(cwd),
                env=env or os.environ.copy(),
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            # Truncate output (12.6)
            if len(stdout) > MAX_OUTPUT_CHARS:
                stdout = stdout[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            if len(stderr) > MAX_OUTPUT_CHARS:
                stderr = stderr[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
                "sandboxed": True,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "returncode": -1,
                "error": "Timeout",
                "stdout": "",
                "stderr": f"Command execution timed out after {self.timeout}s",
                "command": command,
                "sandboxed": True,
            }

        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "error": "Execution Failed",
                "stdout": "",
                "stderr": str(e),
                "command": command,
                "sandboxed": True,
            }

    @staticmethod
    def _noether_home() -> Path:
        """Return the global Noether config directory (~/.noether/)."""
        return Path.home() / ".noether"

    def _resolve_cli_path(self) -> Path:
        """Resolve SRT CLI path: SRT_PATH env var > ~/.noether/ global install."""
        env_path = os.environ.get("SRT_PATH")
        if env_path:
            return Path(env_path)
        return self._noether_home() / "sandbox-runtime" / "dist" / "cli.js"

    def _resolve_config_path(self) -> Path:
        """Resolve SRT config: project-local .srt-config.json > ~/.noether/ global."""
        local_config = self.project_root / ".srt-config.json"
        if local_config.exists():
            return local_config
        return self._noether_home() / "srt-config.json"

    def validate_setup(self) -> bool:
        """Check if sandbox is ready to use (file existence + sanity check)."""
        if not self.cli_js_path.exists() or not self.srt_config.exists():
            return False
        return self._sanity_check()

    def _sanity_check(self) -> bool:
        """Run a quick echo command through SRT to verify it works."""
        try:
            result = subprocess.run(
                [
                    "node", str(self.cli_js_path),
                    "--settings", str(self.srt_config),
                    "-c", "echo noether_ok",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.project_root),
            )
            return "noether_ok" in result.stdout
        except Exception:
            return False
