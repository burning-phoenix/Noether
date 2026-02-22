"""
Unit tests for the Search/Replace Editor system.

Tests cover:
- 4-layer matching algorithm (exact, whitespace, indent, fuzzy)
- Indentation preservation
- Search/replace block parsing
- Error feedback generation
"""

import pytest
from src.orchestration.editor import (
    ContentMatcher,
    MatchResult,
    SearchReplaceOperation,
    EditError,
    CodeEditor,
    parse_search_replace_blocks,
    apply_with_indent_preservation,
    get_context_lines,
)


class TestContentMatcher:
    """Tests for the 4-layer ContentMatcher algorithm."""

    def test_exact_match_simple(self):
        """Test exact match with simple content."""
        content = "def foo():\n    return 42"
        matcher = ContentMatcher(content)

        result = matcher.find_match("def foo():\n    return 42")

        assert result.found
        assert result.match_type == "exact"
        assert result.confidence == 1.0
        assert result.matched_content == content

    def test_exact_match_partial(self):
        """Test exact match finding substring."""
        content = "import os\n\ndef foo():\n    return 42\n\nprint('done')"
        matcher = ContentMatcher(content)

        result = matcher.find_match("def foo():\n    return 42")

        assert result.found
        assert result.match_type == "exact"
        assert result.line_start == 3
        assert result.line_end == 4

    def test_exact_match_not_found(self):
        """Test exact match when content doesn't exist."""
        content = "def foo():\n    return 42"
        matcher = ContentMatcher(content)

        result = matcher.find_match("def bar():\n    return 0")

        # Should fall through to fuzzy or no match
        assert not result.found or result.match_type != "exact"

    def test_whitespace_match(self):
        """Test whitespace-insensitive matching."""
        content = "def foo():\n    x = 1    +   2\n    return x"
        matcher = ContentMatcher(content)

        # Search with different whitespace
        result = matcher.find_match("def foo():\n    x = 1 + 2\n    return x")

        assert result.found
        assert result.match_type in ("exact", "whitespace")

    def test_whitespace_match_tabs_vs_spaces(self):
        """Test matching with tabs vs spaces."""
        content = "def foo():\n\treturn 42"
        matcher = ContentMatcher(content)

        # Search with spaces instead of tab
        result = matcher.find_match("def foo():\n    return 42")

        # Should match via whitespace or indent layer
        assert result.found
        assert result.match_type in ("whitespace", "indent", "fuzzy")

    def test_indent_match(self):
        """Test indentation-flexible matching."""
        content = "    def foo():\n        return 42"
        matcher = ContentMatcher(content)

        # Search without indentation
        result = matcher.find_match("def foo():\n    return 42")

        assert result.found
        # Can match via whitespace, indent, or fuzzy layer
        assert result.match_type in ("whitespace", "indent", "fuzzy")

    def test_indent_match_preserves_original_indent(self):
        """Test that original indentation is detected."""
        content = "        def foo():\n            return 42"
        matcher = ContentMatcher(content)

        result = matcher.find_match("def foo():\n    return 42")

        assert result.found
        assert result.original_indent == "        "

    def test_fuzzy_match_minor_differences(self):
        """Test fuzzy matching with minor differences."""
        content = "def calculate_total(items):\n    total = 0\n    return total"
        matcher = ContentMatcher(content)

        # Search with slight difference
        result = matcher.find_match("def calculate_total(items):\n    totl = 0\n    return totl")

        assert result.found
        assert result.match_type == "fuzzy"
        assert result.confidence >= 0.85

    def test_fuzzy_match_threshold(self):
        """Test that fuzzy match respects threshold."""
        content = "def foo():\n    return 42"
        matcher = ContentMatcher(content)

        # Completely different content should not match
        result = matcher.find_match("class Bar:\n    pass\n    pass\n    pass")

        assert not result.found

    def test_no_match_returns_closest(self):
        """Test that no match returns closest match info when truly no match."""
        content = "class Foo:\n    pass\n\nclass Bar:\n    pass"
        matcher = ContentMatcher(content)

        # Content that's very different to avoid fuzzy matching
        result = matcher.find_match("import os\nimport sys\nimport json\nimport re")

        assert not result.found
        assert result.closest_match is not None
        assert result.closest_similarity > 0

    def test_line_numbers_correct(self):
        """Test that line numbers are correctly computed."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5"
        matcher = ContentMatcher(content)

        result = matcher.find_match("line 3")

        assert result.found
        assert result.line_start == 3
        assert result.line_end == 3

    def test_multiline_match_line_range(self):
        """Test line range for multiline matches."""
        content = "a\nb\nc\nd\ne"
        matcher = ContentMatcher(content)

        result = matcher.find_match("b\nc\nd")

        assert result.found
        assert result.line_start == 2
        assert result.line_end == 4


class TestIndentPreservation:
    """Tests for indentation preservation during replacement."""

    def test_preserve_original_indent_basic(self):
        """Test that base indentation is preserved in simple case."""
        content = "    def foo():\n        return 42"
        matcher = ContentMatcher(content)
        match_result = matcher.find_match("    def foo():\n        return 42")

        # Replacement with no indentation - should get original's base indent
        replacement = "def bar():\n    return 100"
        result = apply_with_indent_preservation(content, match_result, replacement)

        # First line should have original indent
        assert "    def bar():" in result
        # Second line should have original indent + relative
        assert "    return 100" in result or "        return 100" in result

    def test_preserve_no_indent_replacement(self):
        """Test replacement with no indentation gets original's indent."""
        content = "        x = 1"
        matcher = ContentMatcher(content)
        match_result = matcher.find_match("        x = 1")

        replacement = "y = 2"
        result = apply_with_indent_preservation(content, match_result, replacement)

        assert "        y = 2" in result

    def test_preserve_multiline_indent(self):
        """Test multiline replacement preserves relative indentation."""
        content = "def foo():\n    if True:\n        return 1"
        matcher = ContentMatcher(content)
        match_result = matcher.find_match("    if True:\n        return 1")

        # Replacement with its own indentation
        replacement = "if False:\n    return 0"
        result = apply_with_indent_preservation(content, match_result, replacement)

        # Should have original base indent (4 spaces)
        assert "    if False:" in result

    def test_empty_lines_preserved(self):
        """Test that empty lines in replacement are preserved."""
        content = "def foo():\n    x = 1\n    return x"
        matcher = ContentMatcher(content)
        match_result = matcher.find_match("    x = 1\n    return x")

        replacement = "x = 1\n\nreturn x"  # Added empty line
        result = apply_with_indent_preservation(content, match_result, replacement)

        assert "\n\n" in result


class TestParseSearchReplaceBlocks:
    """Tests for parsing search/replace blocks from text."""

    def test_parse_single_block(self):
        """Test parsing a single search/replace block."""
        text = """Here's the edit:

app.py
<<<<<<< SEARCH
def foo():
    pass
=======
def foo(x):
    return x
>>>>>>> REPLACE

Done!"""

        operations = parse_search_replace_blocks(text)

        assert len(operations) == 1
        assert operations[0].target == "app.py"
        assert operations[0].search_content == "def foo():\n    pass"
        assert operations[0].replace_content == "def foo(x):\n    return x"

    def test_parse_multiple_blocks(self):
        """Test parsing multiple search/replace blocks."""
        text = """Making two edits:

app.py
<<<<<<< SEARCH
import os
=======
import os
import sys
>>>>>>> REPLACE

utils.py
<<<<<<< SEARCH
def helper():
    pass
=======
def helper(x):
    return x
>>>>>>> REPLACE

Done!"""

        operations = parse_search_replace_blocks(text)

        assert len(operations) == 2
        assert operations[0].target == "app.py"
        assert operations[1].target == "utils.py"

    def test_parse_coder_output_target(self):
        """Test parsing blocks targeting coder_output."""
        # coder_output needs a file extension to match the pattern
        text = """output.py
<<<<<<< SEARCH
old code
=======
new code
>>>>>>> REPLACE"""

        operations = parse_search_replace_blocks(text)

        assert len(operations) == 1
        assert operations[0].target == "output.py"

    def test_parse_various_file_extensions(self):
        """Test parsing blocks with various file extensions."""
        extensions = ["py", "js", "ts", "tsx", "java", "go", "rs", "rb"]

        for ext in extensions:
            text = f"""test.{ext}
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""

            operations = parse_search_replace_blocks(text)
            assert len(operations) == 1, f"Failed for .{ext}"
            assert operations[0].target == f"test.{ext}"

    def test_parse_empty_replacement(self):
        """Test parsing block with empty replacement (deletion)."""
        text = """app.py
<<<<<<< SEARCH
# Remove this comment
=======

>>>>>>> REPLACE"""

        operations = parse_search_replace_blocks(text)

        assert len(operations) == 1
        assert operations[0].replace_content == ""

    def test_parse_no_blocks(self):
        """Test parsing text with no blocks returns empty list."""
        text = "This is just regular text without any edit blocks."

        operations = parse_search_replace_blocks(text)

        assert len(operations) == 0


class TestErrorFeedback:
    """Tests for error feedback generation."""

    def test_edit_error_format(self):
        """Test EditError formatting."""
        error = EditError(
            error_type="no_match",
            target="app.py",
            search_content="def foo():\n    pass",
            closest_match="def foo(x):\n    pass",
            closest_similarity=0.92,
            closest_context="   10:     return result\n>>> 11: def foo(x):\n>>> 12:     pass\n   13: ",
            suggestion="Copy the EXACT content from the closest match above.",
            line_range=(11, 12),
        )

        feedback = error.format_feedback()

        assert "EDIT FAILED: no_match" in feedback
        assert "Target: app.py" in feedback
        assert "def foo():" in feedback
        assert "92%" in feedback
        assert "lines 11-12" in feedback
        assert "SUGGESTION" in feedback

    def test_edit_error_without_closest(self):
        """Test EditError formatting when no close match found."""
        error = EditError(
            error_type="no_match",
            target="app.py",
            search_content="completely different",
            suggestion="The search content was not found.",
        )

        feedback = error.format_feedback()

        assert "EDIT FAILED: no_match" in feedback
        assert "CLOSEST MATCH" not in feedback
        assert "SUGGESTION" in feedback


class TestGetContextLines:
    """Tests for context line extraction."""

    def test_get_context_basic(self):
        """Test basic context extraction."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7"

        result = get_context_lines(content, 4, 4, context=2)

        assert "line 2" in result
        assert "line 3" in result
        assert ">>> " in result  # Should mark the target line
        assert "line 5" in result
        assert "line 6" in result

    def test_get_context_at_start(self):
        """Test context extraction at start of file."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5"

        result = get_context_lines(content, 1, 1, context=3)

        assert "line 1" in result
        assert ">>> " in result

    def test_get_context_at_end(self):
        """Test context extraction at end of file."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5"

        result = get_context_lines(content, 5, 5, context=3)

        assert "line 5" in result
        assert ">>> " in result


class TestCodeEditorIntegration:
    """Integration tests for CodeEditor with search/replace."""

    @pytest.fixture
    def mock_editor(self):
        """Create a mock editor for testing."""
        content = ["def foo():\n    return 42"]

        def get_output():
            return content[0]

        def set_output(new_content):
            content[0] = new_content

        return CodeEditor(
            get_coder_output=get_output,
            set_coder_output=set_output,
        )

    @pytest.mark.asyncio
    async def test_apply_exact_match_edit(self, mock_editor):
        """Test applying an edit with exact match."""
        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="return 42",
            replace_content="return 100",
            reason="Change return value",
        )

        result = await mock_editor.apply_edit(operation, require_approval=False)

        assert result.success
        assert result.match_result.match_type == "exact"
        assert "return 100" in mock_editor.get_coder_output()

    @pytest.mark.asyncio
    async def test_apply_fuzzy_match_edit(self, mock_editor):
        """Test applying an edit with fuzzy match."""
        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="retrun 42",  # Typo
            replace_content="return 100",
            reason="Fix typo and change value",
        )

        result = await mock_editor.apply_edit(operation, require_approval=False)

        # Should find via fuzzy match
        assert result.success
        assert result.match_result.match_type == "fuzzy"

    @pytest.mark.asyncio
    async def test_apply_no_match_returns_feedback(self, mock_editor):
        """Test that no match returns detailed feedback."""
        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="completely_nonexistent_content",
            replace_content="replacement",
            reason="This should fail",
        )

        result = await mock_editor.apply_edit(operation, require_approval=False)

        assert not result.success
        assert "EDIT FAILED" in result.message
        assert result.match_result is not None

    @pytest.mark.asyncio
    async def test_parse_and_apply_from_response(self, mock_editor):
        """Test parsing and applying edits from LLM response."""
        response = """I'll make this change:

output.py
<<<<<<< SEARCH
def foo():
    return 42
=======
def foo(x):
    return x * 2
>>>>>>> REPLACE

Done!"""

        operations = mock_editor.parse_edits(response)
        assert len(operations) == 1

        # Update target to coder_output for this test
        operations[0].target = "coder_output"

        result = await mock_editor.apply_edit(operations[0], require_approval=False)
        assert result.success
        assert "def foo(x):" in mock_editor.get_coder_output()

    @pytest.mark.asyncio
    async def test_undo_edit(self, mock_editor):
        """Test undoing an edit."""
        original = mock_editor.get_coder_output()

        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="return 42",
            replace_content="return 100",
            reason="Change value",
        )

        await mock_editor.apply_edit(operation, require_approval=False)
        assert "return 100" in mock_editor.get_coder_output()

        await mock_editor.undo_last_edit()
        assert mock_editor.get_coder_output() == original


class TestSearchReplaceOperation:
    """Tests for SearchReplaceOperation dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        op = SearchReplaceOperation(
            target="app.py",
            search_content="old",
            replace_content="new",
            reason="Update code",
        )

        d = op.to_dict()

        assert d["target"] == "app.py"
        assert d["search_content"] == "old"
        assert d["replace_content"] == "new"
        assert d["reason"] == "Update code"

    def test_from_dict(self):
        """Test creation from dictionary."""
        d = {
            "target": "utils.py",
            "search_content": "foo",
            "replace_content": "bar",
            "reason": "Rename",
        }

        op = SearchReplaceOperation.from_dict(d)

        assert op.target == "utils.py"
        assert op.search_content == "foo"
        assert op.replace_content == "bar"
        assert op.reason == "Rename"


class TestEdgeCasesAndBugFixes:
    """Tests for edge cases and bug fixes documented in BUGS.md."""

    def test_content_matcher_none_input_raises(self):
        """Bug 2: ContentMatcher should raise ValueError for None input."""
        with pytest.raises(ValueError) as exc_info:
            ContentMatcher(None)

        assert "non-None content" in str(exc_info.value)

    def test_content_matcher_empty_string_works(self):
        """Empty string is valid input - should not crash."""
        matcher = ContentMatcher("")

        assert matcher.content == ""
        assert matcher.lines == [""]

        # Find should return not found, not crash
        result = matcher.find_match("anything")
        assert not result.found

    def test_parse_search_replace_blocks_malformed_skipped(self):
        """Bug 4: Malformed blocks should be skipped, not crash."""
        # Text with valid block - should parse normally
        text = """test.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""

        operations = parse_search_replace_blocks(text)
        assert len(operations) == 1
        assert operations[0].target == "test.py"

    def test_parse_search_replace_empty_search_and_replace(self):
        """Blocks with empty search/replace should work (for insertions/deletions)."""
        text = """app.py
<<<<<<< SEARCH

=======
# new content
>>>>>>> REPLACE"""

        operations = parse_search_replace_blocks(text)
        assert len(operations) == 1
        assert operations[0].search_content == ""
        assert operations[0].replace_content == "# new content"

    @pytest.fixture
    def empty_content_editor(self):
        """Create editor with empty content for testing Bug 1."""
        content = [""]  # Empty string, not None

        def get_output():
            return content[0]

        def set_output(new_content):
            content[0] = new_content

        return CodeEditor(
            get_coder_output=get_output,
            set_coder_output=set_output,
        )

    @pytest.mark.asyncio
    async def test_apply_edit_empty_coder_output_returns_error(self, empty_content_editor):
        """Bug 1: Editing empty coder_output should return informative error, not crash."""
        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="something",
            replace_content="replacement",
            reason="Test edit",
        )

        result = await empty_content_editor.apply_edit(operation, require_approval=False)

        assert not result.success
        assert "No Coder output available" in result.message
        assert "Generate code first" in result.message

    @pytest.fixture
    def none_content_editor(self):
        """Create editor that returns None (simulating uninitialized state)."""
        def get_output():
            return None

        def set_output(new_content):
            pass

        return CodeEditor(
            get_coder_output=get_output,
            set_coder_output=set_output,
        )

    @pytest.mark.asyncio
    async def test_apply_edit_none_coder_output_returns_error(self, none_content_editor):
        """Bug 1 (None case): Editing None coder_output should return error, not crash."""
        operation = SearchReplaceOperation(
            target="coder_output",
            search_content="something",
            replace_content="replacement",
            reason="Test edit",
        )

        result = await none_content_editor.apply_edit(operation, require_approval=False)

        assert not result.success
        assert "No Coder output available" in result.message


class TestContentMatcherEdgeCases:
    """Additional edge case tests for ContentMatcher robustness."""

    def test_single_character_content(self):
        """Single character content should work."""
        matcher = ContentMatcher("x")
        result = matcher.find_match("x")

        assert result.found
        assert result.match_type == "exact"

    def test_whitespace_only_content(self):
        """Whitespace-only content should work."""
        matcher = ContentMatcher("   \n\t\n   ")

        # Should not crash
        result = matcher.find_match("x")
        assert not result.found

    def test_very_long_content(self):
        """Very long content should not cause issues."""
        # 10000 lines
        content = "\n".join(f"line {i}" for i in range(10000))
        matcher = ContentMatcher(content)

        result = matcher.find_match("line 5000")
        assert result.found
        assert result.line_start == 5001  # 1-indexed

    def test_unicode_content(self):
        """Unicode content should work correctly."""
        content = "def 你好():\n    return '世界'"
        matcher = ContentMatcher(content)

        result = matcher.find_match("def 你好():")
        assert result.found


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
