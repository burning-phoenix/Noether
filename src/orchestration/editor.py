"""
Search/Replace Code Editor for precise content-based modifications.

Replaces line-based editing with content matching for reliability:
- 4-layer matching algorithm (exact, whitespace, indent, fuzzy)
- Indentation preservation
- Detailed error feedback for LLM self-correction
- Rollback on partial failures
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Tuple, List
from pathlib import Path
from difflib import SequenceMatcher
import re
import textwrap


@dataclass
class MatchResult:
    """Result of a content match attempt."""
    found: bool
    match_type: Optional[str] = None  # "exact", "whitespace", "indent", "fuzzy"
    confidence: float = 0.0
    start_index: int = -1
    end_index: int = -1
    matched_content: str = ""
    original_indent: str = ""
    line_start: int = -1
    line_end: int = -1
    closest_match: Optional[str] = None
    closest_similarity: float = 0.0
    closest_line_start: int = -1
    closest_line_end: int = -1


@dataclass
class SearchReplaceOperation:
    """Represents a search/replace edit operation."""
    target: str  # "coder_output" or file path
    search_content: str  # Content to find
    replace_content: str  # Content to replace with
    reason: str  # Human-readable reason
    match_type: Optional[str] = None  # Filled after matching
    match_confidence: Optional[float] = None
    original_indent: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "target": self.target,
            "search_content": self.search_content,
            "replace_content": self.replace_content,
            "reason": self.reason,
            "match_type": self.match_type,
            "match_confidence": self.match_confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SearchReplaceOperation":
        """Create from dictionary."""
        return cls(
            target=data.get("target", "coder_output"),
            search_content=data.get("search_content", ""),
            replace_content=data.get("replace_content", ""),
            reason=data.get("reason", ""),
        )


@dataclass
class EditResult:
    """Result of an edit operation."""
    success: bool
    operation: SearchReplaceOperation
    message: str
    before_content: str = ""
    after_content: str = ""
    match_result: Optional[MatchResult] = None
    # For sync mode file edits: contains computed new content, needs async write
    pending_file_write: Optional[str] = None


@dataclass
class EditError:
    """Detailed error for LLM feedback."""
    error_type: str  # "no_match", "ambiguous_match", "apply_failed"
    target: str
    search_content: str
    closest_match: Optional[str] = None
    closest_similarity: float = 0.0
    closest_context: str = ""  # 3 lines before/after
    suggestion: str = ""
    line_range: Optional[Tuple[int, int]] = None

    def format_feedback(self) -> str:
        """Format error as actionable feedback for LLM."""
        lines = [
            f"EDIT FAILED: {self.error_type}",
            "",
            f"Target: {self.target}",
            "",
            "SEARCH CONTENT (what you provided):",
            "```",
            self.search_content,
            "```",
        ]

        if self.closest_match:
            lines.extend([
                "",
                f"CLOSEST MATCH FOUND ({self.closest_similarity:.0%} similar)"
                + (f" at lines {self.line_range[0]}-{self.line_range[1]}:" if self.line_range else ":"),
                "```",
                self.closest_match,
                "```",
            ])

        if self.closest_context:
            lines.extend([
                "",
                "SURROUNDING CONTEXT:",
                "```",
                self.closest_context,
                "```",
            ])

        if self.suggestion:
            lines.extend([
                "",
                f"SUGGESTION: {self.suggestion}",
            ])

        return "\n".join(lines)


class ContentMatcher:
    """
    4-layer content matching algorithm.

    Layer 1: Exact match (fastest)
    Layer 2: Whitespace-insensitive match (collapse multiple spaces/tabs)
    Layer 3: Indentation-flexible match (strip common indent)
    Layer 4: Fuzzy match via difflib (85% threshold)
    """

    FUZZY_THRESHOLD = 0.75  # Lowered from 0.85 to allow more fuzzy matches

    def __init__(self, content: str):
        """Initialize with content to search in.

        Args:
            content: The content to search within. Must be a string (not None).

        Raises:
            ValueError: If content is None.
        """
        if content is None:
            raise ValueError(
                "ContentMatcher requires non-None content. "
                "Ensure the target exists and has content before attempting edits."
            )
        self.content = content
        self.lines = content.split("\n")

    def find_match(self, search: str) -> MatchResult:
        """
        Find the best match for search content.

        Args:
            search: Content to find

        Returns:
            MatchResult with match details
        """
        if not search or not search.strip():
            return MatchResult(found=False)

        # Layer 1: Exact match
        result = self._exact_match(search)
        if result.found:
            return result

        # Layer 2: Whitespace-insensitive
        result = self._whitespace_match(search)
        if result.found:
            return result

        # Layer 3: Indentation-flexible
        result = self._indent_match(search)
        if result.found:
            return result

        # Layer 4: Fuzzy match
        result = self._fuzzy_match(search)
        if result.found:
            return result

        # No match - return closest for feedback
        return self._find_closest_for_feedback(search)

    def _exact_match(self, search: str) -> MatchResult:
        """Layer 1: Exact substring match."""
        idx = self.content.find(search)
        if idx != -1:
            end_idx = idx + len(search)
            line_start, line_end = self._index_to_lines(idx, end_idx)
            indent = self._detect_indent(search)
            return MatchResult(
                found=True,
                match_type="exact",
                confidence=1.0,
                start_index=idx,
                end_index=end_idx,
                matched_content=search,
                original_indent=indent,
                line_start=line_start,
                line_end=line_end,
            )
        return MatchResult(found=False)

    def _whitespace_match(self, search: str) -> MatchResult:
        """Layer 2: Whitespace-normalized match."""
        # Normalize both search and content (collapse whitespace)
        normalized_search = self._normalize_whitespace(search)
        normalized_content = self._normalize_whitespace(self.content)

        idx = normalized_content.find(normalized_search)
        if idx != -1:
            # Map back to original positions (approximate)
            original_idx = self._map_normalized_to_original(idx, normalized_search)
            if original_idx is not None:
                # Find the actual content at this location
                search_lines = search.strip().split("\n")
                content_lines = self.lines

                # Find where these lines appear
                for i in range(len(content_lines) - len(search_lines) + 1):
                    window = content_lines[i:i + len(search_lines)]
                    if self._lines_match_whitespace(search_lines, window):
                        matched = "\n".join(window)
                        line_start = i + 1
                        line_end = i + len(search_lines)
                        indent = self._detect_indent(matched)
                        start_idx = self.content.find(matched)
                        return MatchResult(
                            found=True,
                            match_type="whitespace",
                            confidence=0.95,
                            start_index=start_idx,
                            end_index=start_idx + len(matched),
                            matched_content=matched,
                            original_indent=indent,
                            line_start=line_start,
                            line_end=line_end,
                        )

        return MatchResult(found=False)

    def _indent_match(self, search: str) -> MatchResult:
        """Layer 3: Indentation-flexible match."""
        # Strip common leading indentation from search
        dedented_search = textwrap.dedent(search)
        search_lines = dedented_search.strip().split("\n")

        if not search_lines:
            return MatchResult(found=False)

        # Slide window over content lines
        for i in range(len(self.lines) - len(search_lines) + 1):
            window = self.lines[i:i + len(search_lines)]

            # Dedent window and compare
            window_text = "\n".join(window)
            dedented_window = textwrap.dedent(window_text)
            window_lines = dedented_window.strip().split("\n")

            if self._lines_match_stripped(search_lines, window_lines):
                matched = "\n".join(window)
                line_start = i + 1
                line_end = i + len(search_lines)
                indent = self._detect_indent(matched)
                start_idx = self._find_line_start_index(i)
                end_idx = self._find_line_end_index(i + len(search_lines) - 1)
                return MatchResult(
                    found=True,
                    match_type="indent",
                    confidence=0.90,
                    start_index=start_idx,
                    end_index=end_idx,
                    matched_content=matched,
                    original_indent=indent,
                    line_start=line_start,
                    line_end=line_end,
                )

        return MatchResult(found=False)

    def _fuzzy_match(self, search: str) -> MatchResult:
        """Layer 4: Fuzzy match using difflib."""
        search_lines = search.strip().split("\n")
        search_len = len(search_lines)

        best_ratio = 0.0
        best_start = -1
        best_window = []

        # Slide window and find best similarity
        for i in range(len(self.lines) - search_len + 1):
            window = self.lines[i:i + search_len]
            window_text = "\n".join(window)

            ratio = SequenceMatcher(None, search.strip(), window_text.strip()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i
                best_window = window

        if best_ratio >= self.FUZZY_THRESHOLD and best_start >= 0:
            matched = "\n".join(best_window)
            line_start = best_start + 1
            line_end = best_start + search_len
            indent = self._detect_indent(matched)
            start_idx = self._find_line_start_index(best_start)
            end_idx = self._find_line_end_index(best_start + search_len - 1)
            return MatchResult(
                found=True,
                match_type="fuzzy",
                confidence=best_ratio,
                start_index=start_idx,
                end_index=end_idx,
                matched_content=matched,
                original_indent=indent,
                line_start=line_start,
                line_end=line_end,
            )

        return MatchResult(found=False)

    def _find_closest_for_feedback(self, search: str) -> MatchResult:
        """Find closest match for error feedback."""
        search_lines = search.strip().split("\n")
        search_len = len(search_lines)

        # Guard: skip O(n²) fuzzy scan on large files to prevent freezing
        if len(self.lines) > 2000:
            return MatchResult(found=False)

        best_ratio = 0.0
        best_start = -1
        best_window = []

        # Allow variable window sizes around search length
        for window_size in [search_len, search_len - 1, search_len + 1, search_len - 2, search_len + 2]:
            if window_size < 1 or window_size > len(self.lines):
                continue
            for i in range(len(self.lines) - window_size + 1):
                window = self.lines[i:i + window_size]
                window_text = "\n".join(window)

                ratio = SequenceMatcher(None, search.strip(), window_text.strip()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    best_window = window

        closest_match = "\n".join(best_window) if best_window else None
        closest_line_start = best_start + 1 if best_start >= 0 else -1
        closest_line_end = best_start + len(best_window) if best_start >= 0 else -1

        return MatchResult(
            found=False,
            closest_match=closest_match,
            closest_similarity=best_ratio,
            closest_line_start=closest_line_start,
            closest_line_end=closest_line_end,
        )

    def _normalize_whitespace(self, text: str) -> str:
        """Collapse multiple whitespace to single space."""
        return re.sub(r'[ \t]+', ' ', text)

    def _lines_match_whitespace(self, a: List[str], b: List[str]) -> bool:
        """Check if lines match with whitespace normalization."""
        if len(a) != len(b):
            return False
        for la, lb in zip(a, b):
            if self._normalize_whitespace(la.strip()) != self._normalize_whitespace(lb.strip()):
                return False
        return True

    def _lines_match_stripped(self, a: List[str], b: List[str]) -> bool:
        """Check if lines match when stripped."""
        if len(a) != len(b):
            return False
        for la, lb in zip(a, b):
            if la.strip() != lb.strip():
                return False
        return True

    def _map_normalized_to_original(self, normalized_idx: int, search: str) -> Optional[int]:
        """Map index in normalized content back to original (approximate)."""
        # Simple approximation: find first line that contains start of normalized search
        first_line = search.split('\n')[0].strip() if search else ""
        if not first_line:
            return None
        for i, line in enumerate(self.lines):
            if first_line in line:
                return self._find_line_start_index(i)
        return None

    def _index_to_lines(self, start_idx: int, end_idx: int) -> Tuple[int, int]:
        """Convert character indices to line numbers (1-indexed)."""
        line_start = self.content[:start_idx].count('\n') + 1
        line_end = self.content[:end_idx].count('\n') + 1
        return line_start, line_end

    def _find_line_start_index(self, line_num: int) -> int:
        """Find character index where line starts (0-indexed line_num)."""
        if line_num == 0:
            return 0
        idx = 0
        for i, line in enumerate(self.lines):
            if i == line_num:
                return idx
            idx += len(line) + 1  # +1 for newline
        return idx

    def _find_line_end_index(self, line_num: int) -> int:
        """Find character index where line ends (0-indexed line_num)."""
        idx = 0
        for i, line in enumerate(self.lines):
            idx += len(line)
            if i == line_num:
                return idx
            idx += 1  # newline
        return idx

    def _detect_indent(self, text: str) -> str:
        """Detect the indentation of the first non-empty line."""
        for line in text.split("\n"):
            if line.strip():
                return line[:len(line) - len(line.lstrip())]
        return ""


def apply_with_indent_preservation(
    content: str,
    match_result: MatchResult,
    replacement: str,
) -> str:
    """
    Apply replacement while preserving indentation.

    The function detects the base indentation of the matched content and
    adjusts the replacement to use that same base indentation, while
    preserving relative indentation within the replacement.

    Args:
        content: Original full content
        match_result: Result from ContentMatcher
        replacement: New content to insert

    Returns:
        Modified content with replacement applied
    """
    if not match_result.found:
        return content

    # Get the base indentation from the matched content
    original_indent = match_result.original_indent
    replacement_lines = replacement.split("\n")

    # Detect replacement's base indent (from first non-empty line)
    replacement_base_indent = ""
    for line in replacement_lines:
        if line.strip():
            replacement_base_indent = line[:len(line) - len(line.lstrip())]
            break

    # Adjust replacement indentation to match original
    adjusted_lines = []
    for line in replacement_lines:
        if line.strip():
            stripped = line.lstrip()
            line_indent = line[:len(line) - len(stripped)]

            # Calculate relative indent (how much more than base)
            if replacement_base_indent:
                if line_indent.startswith(replacement_base_indent):
                    relative_indent = line_indent[len(replacement_base_indent):]
                elif len(line_indent) > len(replacement_base_indent):
                    # Different indent chars, try to preserve relative depth
                    relative_indent = line_indent[len(replacement_base_indent):]
                else:
                    relative_indent = ""
            else:
                # No base indent in replacement, use line indent as relative
                relative_indent = line_indent

            adjusted_lines.append(original_indent + relative_indent + stripped)
        else:
            adjusted_lines.append(line)  # Keep empty lines as-is

    adjusted_replacement = "\n".join(adjusted_lines)

    # Apply the replacement
    before = content[:match_result.start_index]
    after = content[match_result.end_index:]
    return before + adjusted_replacement + after


def parse_search_replace_blocks(text: str) -> List[SearchReplaceOperation]:
    """
    Parse search/replace blocks from text.

    Format:
    ```
    filename.py
    <<<<<<< SEARCH
    content to find
    =======
    replacement content
    >>>>>>> REPLACE
    ```

    Args:
        text: Text containing search/replace blocks

    Returns:
        List of SearchReplaceOperation objects
    """
    operations = []

    # Strip markdown code fences that wrap Search/Replace blocks
    # The prompts tell LLMs to wrap blocks in ```, but the regex patterns
    # expect raw text. This preprocessing handles both fenced and unfenced blocks.
    # Pattern 1: Strip opening ```[lang] before a line containing <<<<<<< SEARCH
    # (handles optional filename line between fence and SEARCH marker)
    text = re.sub(r'```[a-zA-Z]*\n((?:[^\n]*\n)?<<<<<<< SEARCH)', r'\1', text)
    # Pattern 2: Strip closing ``` after >>>>>>> REPLACE
    text = re.sub(r'(>>>>>>> REPLACE)\n```', r'\1', text)

    # File extensions to match
    extensions = r'(?:py|js|ts|tsx|jsx|java|cpp|c|h|go|rs|rb|php|swift|kt|scala|sh|yml|yaml|json|md|html|css|scss|sql)'

    # Pattern 1: Filename on its own line before SEARCH
    # \n? before separators allows empty search/replace content (deletions/insertions)
    pattern1 = re.compile(
        r'^([^\n]*?([a-zA-Z0-9_\-./]+\.' + extensions + r'))\s*\n'
        r'<<<<<<< SEARCH\n'
        r'(.*?)'
        r'\n?=======\n'
        r'(.*?)'
        r'\n?>>>>>>> REPLACE',
        re.MULTILINE | re.DOTALL
    )

    # Pattern 2: Filename embedded in text before SEARCH (same line or previous line)
    pattern2 = re.compile(
        r'([a-zA-Z0-9_\-./]+\.' + extensions + r')\s*\n'
        r'<<<<<<< SEARCH\n'
        r'(.*?)'
        r'\n?=======\n'
        r'(.*?)'
        r'\n?>>>>>>> REPLACE',
        re.MULTILINE | re.DOTALL
    )

    # Pattern 3: No filename, just SEARCH/REPLACE block (defaults to coder_output)
    pattern3 = re.compile(
        r'<<<<<<< SEARCH\n'
        r'(.*?)'
        r'\n?=======\n'
        r'(.*?)'
        r'\n?>>>>>>> REPLACE',
        re.MULTILINE | re.DOTALL
    )

    # Track matched SEARCH blocks by their content hash to avoid duplicates
    matched_searches = set()

    def add_operation(filename: str, search: str, replace: str):
        # Use search content as dedup key
        search_key = hash(search.strip())
        if search_key in matched_searches:
            return
        matched_searches.add(search_key)

        # Determine target
        if filename.lower() in ("coder_output", "output", "current"):
            target = "coder_output"
        else:
            target = filename

        operations.append(SearchReplaceOperation(
            target=target,
            search_content=search,
            replace_content=replace,
            reason=f"Edit {filename}",
        ))

    # Try pattern 1 first (filename anywhere on line before SEARCH - most specific)
    for match in pattern1.finditer(text):
        filename_group = match.group(2)
        if filename_group is None:
            continue  # Skip malformed match - group didn't capture
        filename = filename_group.strip()
        if not filename:
            continue  # Skip empty filename
        search_content = match.group(3) or ""
        replace_content = match.group(4) or ""
        add_operation(filename, search_content, replace_content)

    # Try pattern 2 if pattern 1 didn't find the filename
    for match in pattern2.finditer(text):
        filename_group = match.group(1)
        if filename_group is None:
            continue  # Skip malformed match
        filename = filename_group.strip()
        if not filename:
            continue  # Skip empty filename
        search_content = match.group(2) or ""
        replace_content = match.group(3) or ""
        add_operation(filename, search_content, replace_content)

    # Try pattern 3 (no filename - default to coder_output) only if others didn't match
    if not operations:
        for match in pattern3.finditer(text):
            search_content = match.group(1) or ""
            replace_content = match.group(2) or ""
            add_operation("coder_output", search_content, replace_content)

    return operations


def get_context_lines(content: str, line_start: int, line_end: int, context: int = 3) -> str:
    """
    Get lines with surrounding context.

    Args:
        content: Full content
        line_start: Start line (1-indexed)
        line_end: End line (1-indexed)
        context: Number of context lines before/after

    Returns:
        Context with line numbers
    """
    lines = content.split("\n")
    start = max(0, line_start - 1 - context)
    end = min(len(lines), line_end + context)

    result = []
    width = len(str(end))
    for i in range(start, end):
        prefix = ">>> " if line_start - 1 <= i < line_end else "    "
        result.append(f"{prefix}{i + 1:>{width}}: {lines[i]}")

    return "\n".join(result)


class CodeEditor:
    """
    Content-based code editor using search/replace.

    Supports editing:
    - Coder's output in the TextArea
    - Files in the project (with sandbox restrictions)
    """

    def __init__(
        self,
        get_coder_output: Callable[[], str],
        set_coder_output: Callable[[str], None],
        file_read: Optional[Callable[[str], Optional[str]]] = None,
        file_write: Optional[Callable[[str, str], Awaitable[bool]]] = None,
        approval_callback: Optional[Callable[[SearchReplaceOperation], Awaitable[bool]]] = None,
    ):
        """
        Initialize the code editor.

        Args:
            get_coder_output: Function to get current Coder output
            set_coder_output: Function to set Coder output
            file_read: Optional function to read files (sandbox)
            file_write: Optional async function to write files (sandbox)
            approval_callback: Optional async function to request user approval
        """
        self.get_coder_output = get_coder_output
        self.set_coder_output = set_coder_output
        self.file_read = file_read
        self.file_write = file_write
        self.approval_callback = approval_callback

        # Edit history for undo
        self._history: List[EditResult] = []
        self._max_history = 50

    def parse_edits(self, response: str) -> List[SearchReplaceOperation]:
        """
        Parse edit operations from response text.

        Supports both new search/replace format and legacy JSON format.

        Args:
            response: Response text containing edit blocks

        Returns:
            List of SearchReplaceOperation objects
        """
        # Try new search/replace format first
        operations = parse_search_replace_blocks(response)

        if operations:
            return operations

        # Fallback to legacy JSON format for backward compatibility
        import json
        json_pattern = re.compile(
            r'\{[^{}]*"action"\s*:\s*"edit"[^{}]*\}',
            re.DOTALL
        )

        for match in json_pattern.finditer(response):
            try:
                data = json.loads(match.group())
                if data.get("action") == "edit":
                    # Convert legacy format
                    op = SearchReplaceOperation(
                        target=data.get("file", "coder_output"),
                        search_content=data.get("old_content", ""),
                        replace_content=data.get("new_content", ""),
                        reason=data.get("reason", ""),
                    )
                    operations.append(op)
            except (json.JSONDecodeError, ValueError, KeyError):
                continue

        return operations

    def apply_edit_sync(
        self,
        operation: SearchReplaceOperation,
    ) -> EditResult:
        """
        Synchronous version of apply_edit - no approval callback.

        Use this from worker threads to avoid event loop conflicts.
        Does not support file writes (only coder_output edits).

        Args:
            operation: The operation to apply

        Returns:
            EditResult with success status and details
        """
        # Get current content
        if operation.target == "coder_output":
            current_content = self.get_coder_output()
            if current_content is None or current_content == "":
                return EditResult(
                    success=False,
                    operation=operation,
                    message=(
                        "No Coder output available to edit. "
                        "Generate code first, then request edits."
                    ),
                )
        elif self.file_read:
            current_content = self.file_read(operation.target)
            if current_content is None:
                return EditResult(
                    success=False,
                    operation=operation,
                    message=f"Cannot read file: {operation.target}",
                )
        else:
            return EditResult(
                success=False,
                operation=operation,
                message="File editing not configured",
            )

        # Find match using 4-layer algorithm
        matcher = ContentMatcher(current_content)
        match_result = matcher.find_match(operation.search_content)

        if not match_result.found:
            # Generate detailed error feedback
            error = self._create_error_feedback(
                operation, current_content, match_result
            )
            return EditResult(
                success=False,
                operation=operation,
                message=error.format_feedback(),
                match_result=match_result,
            )

        # Update operation with match info
        operation.match_type = match_result.match_type
        operation.match_confidence = match_result.confidence
        operation.original_indent = match_result.original_indent

        # Apply the edit with indentation preservation
        new_content = apply_with_indent_preservation(
            current_content,
            match_result,
            operation.replace_content,
        )

        # Write back
        if operation.target == "coder_output":
            self.set_coder_output(new_content)
            result = EditResult(
                success=True,
                operation=operation,
                message=f"Edit applied ({match_result.match_type} match, {match_result.confidence:.0%} confidence)",
                before_content=match_result.matched_content,
                after_content=operation.replace_content,
                match_result=match_result,
            )
            self._add_to_history(result)
            return result
        else:
            # File edits: return pending write for caller to handle
            # Match succeeded - pending_file_write signals "needs file I/O"
            return EditResult(
                success=True,  # Match succeeded; pending_file_write means needs write
                operation=operation,
                message=f"File edit ready ({match_result.match_type} match, {match_result.confidence:.0%} confidence)",
                before_content=match_result.matched_content,
                after_content=operation.replace_content,
                match_result=match_result,
                pending_file_write=new_content,  # Caller handles async write
            )

    async def apply_edit(
        self,
        operation: SearchReplaceOperation,
        require_approval: bool = True,
    ) -> EditResult:
        """
        Apply a single search/replace operation.

        Args:
            operation: The operation to apply
            require_approval: Whether to require user approval

        Returns:
            EditResult with success status and details
        """
        # Get current content
        if operation.target == "coder_output":
            current_content = self.get_coder_output()
            if current_content is None or current_content == "":
                return EditResult(
                    success=False,
                    operation=operation,
                    message=(
                        "No Coder output available to edit. "
                        "Generate code first, then request edits."
                    ),
                )
        elif self.file_read:
            current_content = self.file_read(operation.target)
            if current_content is None:
                return EditResult(
                    success=False,
                    operation=operation,
                    message=f"Cannot read file: {operation.target}",
                )
            # Note: Empty file is valid for file targets (user may want to search "")
        else:
            return EditResult(
                success=False,
                operation=operation,
                message="File editing not configured",
            )

        # Find match using 4-layer algorithm
        matcher = ContentMatcher(current_content)
        match_result = matcher.find_match(operation.search_content)

        if not match_result.found:
            # Generate detailed error feedback
            error = self._create_error_feedback(
                operation, current_content, match_result
            )
            return EditResult(
                success=False,
                operation=operation,
                message=error.format_feedback(),
                match_result=match_result,
            )

        # Update operation with match info
        operation.match_type = match_result.match_type
        operation.match_confidence = match_result.confidence
        operation.original_indent = match_result.original_indent

        # Request approval if needed
        if require_approval and self.approval_callback:
            approved = await self.approval_callback(operation)
            if not approved:
                return EditResult(
                    success=False,
                    operation=operation,
                    message="Edit rejected by user",
                    before_content=match_result.matched_content,
                    match_result=match_result,
                )

        # Apply the edit with indentation preservation
        new_content = apply_with_indent_preservation(
            current_content,
            match_result,
            operation.replace_content,
        )

        # Write back
        if operation.target == "coder_output":
            self.set_coder_output(new_content)
            success = True
        elif self.file_write:
            success = await self.file_write(operation.target, new_content)
        else:
            success = False

        result = EditResult(
            success=success,
            operation=operation,
            message=f"Edit applied ({match_result.match_type} match, {match_result.confidence:.0%} confidence)" if success else "Failed to write",
            before_content=match_result.matched_content,
            after_content=operation.replace_content,
            match_result=match_result,
        )

        # Add to history
        if success:
            self._add_to_history(result)

        return result

    async def apply_edits(
        self,
        operations: List[SearchReplaceOperation],
        require_approval: bool = True,
    ) -> List[EditResult]:
        """
        Apply multiple edit operations.

        Operations should be ordered bottom-to-top for same-file edits
        to prevent line drift issues.

        Args:
            operations: List of operations
            require_approval: Whether to require approval for each

        Returns:
            List of EditResult objects
        """
        results = []
        failed_targets = set()

        for operation in operations:
            # Skip if we already failed on this target
            if operation.target in failed_targets:
                results.append(EditResult(
                    success=False,
                    operation=operation,
                    message=f"Skipped due to earlier failure on {operation.target}",
                ))
                continue

            result = await self.apply_edit(operation, require_approval)
            results.append(result)

            if not result.success:
                failed_targets.add(operation.target)

        return results

    def _create_error_feedback(
        self,
        operation: SearchReplaceOperation,
        content: str,
        match_result: MatchResult,
    ) -> EditError:
        """Create detailed error feedback for LLM."""
        error = EditError(
            error_type="no_match",
            target=operation.target,
            search_content=operation.search_content,
        )

        if match_result.closest_match:
            error.closest_match = match_result.closest_match
            error.closest_similarity = match_result.closest_similarity
            error.line_range = (
                match_result.closest_line_start,
                match_result.closest_line_end,
            )

            # Get surrounding context
            if match_result.closest_line_start > 0:
                error.closest_context = get_context_lines(
                    content,
                    match_result.closest_line_start,
                    match_result.closest_line_end,
                    context=3,
                )

            error.suggestion = "Copy the EXACT content from the closest match above into your SEARCH block."
        else:
            error.suggestion = "The search content was not found. Check the file and try again with exact content."

        return error

    def _add_to_history(self, result: EditResult) -> None:
        """Add a successful edit to history."""
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    def get_history(self) -> List[EditResult]:
        """Get edit history."""
        return list(self._history)

    def get_last_edit(self) -> Optional[EditResult]:
        """Get the last successful edit."""
        if self._history:
            return self._history[-1]
        return None

    async def undo_last_edit(self) -> Optional[EditResult]:
        """
        Undo the last edit.

        Returns:
            EditResult of the undo operation, or None if no history
        """
        if not self._history:
            return None

        last = self._history.pop()

        # Create reverse operation
        undo_op = SearchReplaceOperation(
            target=last.operation.target,
            search_content=last.after_content,
            replace_content=last.before_content,
            reason=f"Undo: {last.operation.reason}",
        )

        # Apply without adding to history
        result = await self.apply_edit(undo_op, require_approval=False)
        return result

    def clear_history(self) -> int:
        """
        Clear edit history.

        Returns:
            Number of entries cleared
        """
        count = len(self._history)
        self._history.clear()
        return count


# Utility functions for backward compatibility
def add_line_numbers(code: str) -> str:
    """
    Add line numbers to code for display.

    Args:
        code: Source code

    Returns:
        Code with line numbers
    """
    lines = code.split("\n")
    width = len(str(len(lines)))
    numbered = []
    for i, line in enumerate(lines, 1):
        numbered.append(f"{i:>{width}}: {line}")
    return "\n".join(numbered)


def extract_lines(code: str, start: int, end: int) -> str:
    """
    Extract lines from code.

    Args:
        code: Source code
        start: Start line (1-indexed)
        end: End line (inclusive)

    Returns:
        Extracted lines
    """
    lines = code.split("\n")
    if 0 < start <= end <= len(lines):
        return "\n".join(lines[start - 1 : end])
    return ""
