"""
File operation parser and executor.

Parses LLM output to extract file operations (create, write, delete)
and executes them through the sandbox.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Awaitable
from pathlib import Path


class FileOpType(Enum):
    """Types of file operations."""
    CREATE = "create"
    WRITE = "write"
    DELETE = "delete"
    MKDIR = "mkdir"


@dataclass
class FileOperation:
    """Represents a file operation to execute."""
    op_type: FileOpType
    path: str
    content: Optional[str] = None
    reason: str = ""

    def __str__(self) -> str:
        if self.op_type == FileOpType.CREATE:
            return f"CREATE {self.path} ({len(self.content or '')} chars)"
        elif self.op_type == FileOpType.WRITE:
            return f"WRITE {self.path} ({len(self.content or '')} chars)"
        elif self.op_type == FileOpType.DELETE:
            return f"DELETE {self.path}"
        elif self.op_type == FileOpType.MKDIR:
            return f"MKDIR {self.path}"
        return f"{self.op_type.value} {self.path}"


@dataclass
class FileOpResult:
    """Result of a file operation."""
    success: bool
    operation: FileOperation
    message: str


class FileOperationParser:
    """
    Parses LLM output to extract file operations.
    
    Supports multiple formats:
    1. Markdown code blocks with filename in header:
       ```python filename="app.py"
       content
       ```
    
    2. File blocks with path comments:
       ```python
       # file: app.py
       content
       ```
    
    3. JSON operation blocks:
       {"action": "create_file", "path": "app.py", "content": "..."}
    """
    
    # Pattern for markdown code blocks with filename
    # Matches: ```language filename="path" or ```language path="path" or ```language:path
    CODE_BLOCK_PATTERN = re.compile(
        r'```(\w+)?(?:\s+(?:filename|path|file)=["\'"]?([^"\'`\n]+)["\'"]?|\s*:\s*([^\n`]+))?'
        r'\n(.*?)```',
        re.DOTALL
    )
    
    # Pattern for file path in first comment line
    # Matches: # file: path or // file: path or # filepath: path
    FILE_COMMENT_PATTERN = re.compile(
        r'^(?:#|//)\s*(?:file(?:path|name)?|path)\s*:\s*(.+?)\s*$',
        re.MULTILINE | re.IGNORECASE
    )

    # Pattern for bare filename comment (e.g. "# calculator.py" without "file:" prefix)
    BARE_FILENAME_PATTERN = re.compile(
        r'^(?:#|//)\s*([\w./-]+\.(?:py|js|ts|jsx|tsx|html|css|json|yaml|yml|toml|md|txt|sh|rs|go|java|c|cpp|h|hpp))\s*$',
        re.MULTILINE | re.IGNORECASE
    )
    
    # Pattern for JSON file operation blocks
    JSON_OP_PATTERN = re.compile(
        r'\{[^{}]*"action"\s*:\s*"(create_file|write_file|delete_file|mkdir)"[^{}]*\}',
        re.DOTALL
    )
    
    def __init__(
        self,
        sandbox_root: Optional[Path] = None,
    ):
        """
        Initialize the parser.
        
        Args:
            sandbox_root: Root directory for file operations
        """
        self.sandbox_root = sandbox_root or Path.cwd()
    
    def parse(self, llm_output: str) -> list[FileOperation]:
        """
        Parse LLM output to extract file operations.
        
        Args:
            llm_output: The LLM's response text
            
        Returns:
            List of FileOperation objects
        """
        operations = []
        
        # 1. Find code blocks with filename in header
        for match in self.CODE_BLOCK_PATTERN.finditer(llm_output):
            language = match.group(1) or ""
            filename = match.group(2) or match.group(3)
            content = match.group(4)
            
            if filename:
                filename = filename.strip().strip('"\'')
                operations.append(FileOperation(
                    op_type=FileOpType.CREATE,
                    path=filename,
                    content=content.strip(),
                    reason=f"Code block with {language or 'unknown'} language",
                ))
            elif content:
                # Try to find filename in first line comment (# file: path)
                file_match = self.FILE_COMMENT_PATTERN.search(content[:200])
                if file_match:
                    filename = file_match.group(1).strip()
                    operations.append(FileOperation(
                        op_type=FileOpType.CREATE,
                        path=filename,
                        content=content.strip(),
                        reason=f"File path from comment",
                    ))
                else:
                    # Fallback: bare filename comment (# calculator.py)
                    bare_match = self.BARE_FILENAME_PATTERN.search(content[:200])
                    if bare_match:
                        filename = bare_match.group(1).strip()
                        operations.append(FileOperation(
                            op_type=FileOpType.CREATE,
                            path=filename,
                            content=content.strip(),
                            reason="Bare filename from comment",
                        ))
        
        # 2. Find JSON operation blocks
        import json
        for match in self.JSON_OP_PATTERN.finditer(llm_output):
            try:
                data = json.loads(match.group())
                action = data.get("action", "")
                path = data.get("path", "")
                content = data.get("content", "")
                
                if action == "create_file" and path:
                    operations.append(FileOperation(
                        op_type=FileOpType.CREATE,
                        path=path,
                        content=content,
                        reason="JSON operation block",
                    ))
                elif action == "delete_file" and path:
                    operations.append(FileOperation(
                        op_type=FileOpType.DELETE,
                        path=path,
                        reason="JSON operation block",
                    ))
                elif action == "mkdir" and path:
                    operations.append(FileOperation(
                        op_type=FileOpType.MKDIR,
                        path=path,
                        reason="JSON operation block",
                    ))
            except json.JSONDecodeError:
                continue
        
        return operations

    def parse_with_hint(self, llm_output: str, expected_filename: Optional[str] = None) -> list[FileOperation]:
        """
        Parse LLM output with an optional filename hint from task metadata.

        If standard parsing finds no files but there are code blocks,
        and expected_filename is provided, assign that name to the first
        unnamed code block.

        Args:
            llm_output: The LLM's response text
            expected_filename: Hint filename from task's expected_output field

        Returns:
            List of FileOperation objects
        """
        operations = self.parse(llm_output)

        if operations or not expected_filename:
            return operations

        # No files found — try to salvage unnamed code blocks using the hint
        for match in self.CODE_BLOCK_PATTERN.finditer(llm_output):
            language = match.group(1) or ""
            content = match.group(4)
            if content and content.strip():
                # Extract just the filename from expected_output
                # (it may be a description like "calculator.py with add/subtract")
                hint_name = expected_filename.strip().split()[0]
                if '.' in hint_name:
                    operations.append(FileOperation(
                        op_type=FileOpType.CREATE,
                        path=hint_name,
                        content=content.strip(),
                        reason=f"Filename from task hint ({language or 'unknown'} block)",
                    ))
                    break  # Only use hint for the first unnamed block

        return operations


class FileOperationExecutor:
    """
    Executes file operations through the sandbox.
    """
    
    def __init__(
        self,
        sandbox,  # FileSystemSandbox instance
        approval_callback: Optional[Callable[[FileOperation], Awaitable[bool]]] = None,
        auto_approve: bool = False,
    ):
        """
        Initialize the executor.
        
        Args:
            sandbox: FileSystemSandbox instance for safe file operations
            approval_callback: Optional callback to request user approval
            auto_approve: If True, skip approval for all operations
        """
        self.sandbox = sandbox
        self.approval_callback = approval_callback
        self.auto_approve = auto_approve
        self._results: list[FileOpResult] = []
    
    async def execute(
        self,
        operation: FileOperation,
    ) -> FileOpResult:
        """
        Execute a single file operation.
        
        Args:
            operation: The file operation to execute
            
        Returns:
            FileOpResult with success status
        """
        # Request approval if needed
        if not self.auto_approve and self.approval_callback:
            approved = await self.approval_callback(operation)
            if not approved:
                return FileOpResult(
                    success=False,
                    operation=operation,
                    message="Operation rejected by user",
                )
        
        try:
            if operation.op_type == FileOpType.CREATE:
                success = await self.sandbox.safe_write(
                    operation.path,
                    operation.content or "",
                    description=f"Create file: {operation.reason}",
                    skip_approval=self.auto_approve,
                )
                message = "File created" if success else "Failed to create file"
                
            elif operation.op_type == FileOpType.WRITE:
                success = await self.sandbox.safe_write(
                    operation.path,
                    operation.content or "",
                    description=f"Write file: {operation.reason}",
                    skip_approval=self.auto_approve,
                )
                message = "File written" if success else "Failed to write file"
                
            elif operation.op_type == FileOpType.DELETE:
                success = await self.sandbox.safe_delete(
                    operation.path,
                    description=f"Delete: {operation.reason}",
                    skip_approval=self.auto_approve,
                )
                message = "File deleted" if success else "Failed to delete file"
                
            elif operation.op_type == FileOpType.MKDIR:
                # Create directory via sandbox
                resolved = self.sandbox.resolve_path(operation.path)
                if resolved:
                    resolved.mkdir(parents=True, exist_ok=True)
                    success = True
                    message = "Directory created"
                else:
                    success = False
                    message = "Invalid path"
            else:
                success = False
                message = f"Unknown operation type: {operation.op_type}"
                
        except Exception as e:
            success = False
            message = f"Error: {str(e)}"
        
        result = FileOpResult(
            success=success,
            operation=operation,
            message=message,
        )
        self._results.append(result)
        return result
    
    async def execute_all(
        self,
        operations: list[FileOperation],
    ) -> list[FileOpResult]:
        """
        Execute multiple file operations.
        
        Args:
            operations: List of operations to execute
            
        Returns:
            List of FileOpResult objects
        """
        results = []
        for op in operations:
            result = await self.execute(op)
            results.append(result)
        return results
    
    def get_results(self) -> list[FileOpResult]:
        """Get all execution results."""
        return list(self._results)
    
    def clear_results(self) -> None:
        """Clear execution results."""
        self._results.clear()
