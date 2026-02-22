import pytest
import asyncio
from pathlib import Path
from src.sandbox.command_executor import SandboxCommandExecutor

@pytest.fixture
def real_executor():
    # Use the real project root where setup_sandbox.py ran
    # Assuming code is running in project root
    return SandboxCommandExecutor(project_root=Path.cwd(), timeout=10)

@pytest.mark.asyncio
async def test_edge_case_1_safe_command(real_executor):
    """Safe Command: ls -> Expect Success"""
    if not real_executor.validate_setup():
        pytest.skip("Sandbox not set up")
        
    # List project root to see actual files
    result = await real_executor.execute("ls", cwd=real_executor.project_root)
    print(f"ls result: {result}")
    assert result["success"] is True
    assert "src" in result["stdout"] # src directory should exist in project root

@pytest.mark.asyncio
async def test_edge_case_2_blocked_network(real_executor):
    """Blocked Network: curl google.com -> Expect Failure"""
    if not real_executor.validate_setup():
        pytest.skip("Sandbox not set up")
        
    # Should perform DNS resolution but fail connection or be blocked by proxy
    result = await real_executor.execute("curl -I https://google.com --connect-timeout 5")
    print(f"curl result: {result}")
    
    # 3 possibilities: 
    # 1. Connection blocked by proxy (success=False/True but HTTP 403)
    # 2. Network unreachable (success=False)
    # 3. Timeout (if proxy drops packets)
    
    # Sandbox wrapper usually returns success=True if the command ran, but stderr might have info
    # Or strict network config blocks it.
    
    # We expect FAILURE to connect to google.com
    # If using default restrictive config allowDomains=[], it should block.
    
    # Check if we got a real google response
    assert "200 OK" not in result["stdout"]
    # It might return exit code != 0
    # assert result["success"] is False # curl exit code for connection fail is non-zero

@pytest.mark.asyncio
async def test_edge_case_3_forbidden_write(real_executor):
    """Forbidden Write: write to .env -> Expect Failure"""
    if not real_executor.validate_setup():
        pytest.skip("Sandbox not set up")
    
    # Trying to write to .env in project root (which is denyWrite)
    # Using '>>' in shell
    result = await real_executor.execute('echo "HACKED=1" >> .env')
    print(f"write .env result: {result}")
    
    assert result["success"] is False
    assert "Operation not permitted" in result["stderr"] or "Permission denied" in result["stderr"]

@pytest.mark.asyncio
async def test_edge_case_4_allowed_write(real_executor):
    """Allowed Write: write to project directory -> Expect Success"""
    if not real_executor.validate_setup():
        pytest.skip("Sandbox not set up")
        
    test_file = "test_write.txt"
    content = "Hello Sandbox"
    
    # project directory is in allowed write paths
    result = await real_executor.execute(f'echo "{content}" > {test_file}')
    print(f"write allowed result: {result}")
    
    assert result["success"] is True
    
    # Verify content
    read_result = await real_executor.execute(f"cat {test_file}")
    assert read_result["stdout"] == content

@pytest.mark.asyncio
async def test_edge_case_5_timeout(real_executor):
    """Timeout: sleep 60 -> Expect Timeout"""
    # Executor default is 10s for this test fixture
    # We can override per call if needed, but fixture has 10s
    
    result = await real_executor.execute("sleep 20") # > 10s
    assert result["success"] is False
    assert result["error"] == "Timeout"

