"""Tests for FileOperationParser."""

import pytest
from src.orchestration.file_operations import FileOperationParser, FileOpType


@pytest.fixture
def parser():
    return FileOperationParser()


class TestCodeBlockParsing:
    """Test parsing code blocks with filename attribute."""

    def test_parse_filename_attribute(self, parser):
        output = '''Here's the code:

```python filename="app.py"
from flask import Flask
app = Flask(__name__)
```
'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "app.py"
        assert ops[0].op_type == FileOpType.CREATE
        assert "Flask" in ops[0].content

    def test_parse_filename_with_single_quotes(self, parser):
        output = """```python filename='utils.py'
def helper():
    pass
```"""
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "utils.py"

    def test_parse_nested_path(self, parser):
        output = '''```python filename="models/user.py"
class User:
    pass
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "models/user.py"

    def test_parse_multiple_files(self, parser):
        output = '''```python filename="app.py"
from flask import Flask
```

Some explanation.

```python filename="models.py"
class User:
    pass
```'''
        ops = parser.parse(output)
        assert len(ops) == 2
        assert ops[0].path == "app.py"
        assert ops[1].path == "models.py"


class TestFileCommentParsing:
    """Test parsing code blocks with file comment."""

    def test_parse_file_comment(self, parser):
        output = '''```python
# file: routes.py
from flask import Blueprint
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "routes.py"

    def test_parse_filepath_comment(self, parser):
        output = '''```python
# filepath: src/app.py
print("hello")
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "src/app.py"


class TestMixedFormats:
    """Test mixed format parsing in one response."""

    def test_mixed_attribute_and_comment(self, parser):
        output = '''```python filename="app.py"
from flask import Flask
```

```python
# file: utils.py
def helper():
    pass
```'''
        ops = parser.parse(output)
        assert len(ops) == 2
        paths = [op.path for op in ops]
        assert "app.py" in paths
        assert "utils.py" in paths


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_content(self, parser):
        output = '''```python filename="empty.py"
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].content.strip() == ""

    def test_no_code_blocks(self, parser):
        output = "Just some text with no code blocks."
        ops = parser.parse(output)
        assert len(ops) == 0

    def test_code_block_without_filename(self, parser):
        output = '''```python
print("no filename")
```'''
        ops = parser.parse(output)
        # No filename attribute and no file comment => not parsed as file op
        assert len(ops) == 0

    def test_missing_filename_value(self, parser):
        output = '''```python
# Just a normal comment
print("hello")
```'''
        ops = parser.parse(output)
        assert len(ops) == 0


class TestBareFilenameParsing:
    """Test parsing code blocks with bare filename comments (no 'file:' prefix)."""

    def test_bare_filename_comment(self, parser):
        output = '''```python
# calculator.py
def add(a, b):
    return a + b
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "calculator.py"
        assert "def add" in ops[0].content

    def test_bare_filename_with_path(self, parser):
        output = '''```python
# src/utils/helpers.py
def helper():
    pass
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "src/utils/helpers.py"

    def test_bare_filename_js(self, parser):
        output = '''```javascript
// app.js
console.log("hello");
```'''
        ops = parser.parse(output)
        assert len(ops) == 1
        assert ops[0].path == "app.js"

    def test_bare_filename_not_matched_for_random_comment(self, parser):
        output = '''```python
# This is just a comment about the code
print("hello")
```'''
        ops = parser.parse(output)
        assert len(ops) == 0


class TestParseWithHint:
    """Test parse_with_hint fallback when standard parsing finds nothing."""

    def test_hint_used_for_unnamed_block(self, parser):
        output = '''Here's the implementation:

```python
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
```'''
        ops = parser.parse_with_hint(output, "calculator.py")
        assert len(ops) == 1
        assert ops[0].path == "calculator.py"
        assert "def add" in ops[0].content

    def test_hint_not_used_when_filename_present(self, parser):
        output = '''```python filename="math_utils.py"
def add(a, b):
    return a + b
```'''
        ops = parser.parse_with_hint(output, "calculator.py")
        assert len(ops) == 1
        assert ops[0].path == "math_utils.py"  # Uses actual filename, not hint

    def test_hint_not_used_when_none(self, parser):
        output = '''```python
def add(a, b):
    return a + b
```'''
        ops = parser.parse_with_hint(output, None)
        assert len(ops) == 0

    def test_hint_extracts_first_word(self, parser):
        output = '''```python
def main():
    print("hello")
```'''
        ops = parser.parse_with_hint(output, "main.py with entry point")
        assert len(ops) == 1
        assert ops[0].path == "main.py"

    def test_hint_rejected_without_extension(self, parser):
        output = '''```python
def main():
    print("hello")
```'''
        ops = parser.parse_with_hint(output, "create the main module")
        assert len(ops) == 0  # "create" has no dot, so rejected
