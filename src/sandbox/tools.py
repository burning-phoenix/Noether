"""
Native API Tool Schemas.

Defines the JSON Schemas passed to OpenAI/Anthropic/DeepSeek models for native tool calling.
Split into read-only tools (auto loop) and chat tools (full access).
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from .schemas import ReadOnlyShellCommand, FullShellCommand


# Define remaining native tools as Pydantic models

class EditFile(BaseModel):
    """
    Applies exact Search/Replace chunks to modify an existing file.
    Use this over bash `sed` for code editing.
    """
    target_file: str = Field(description="The absolute or relative path to the file to edit.")
    search_replace_chunks: List[dict] = Field(
        description="A list of objects containing 'search' and 'replace' strings. The 'search' string must perfectly match the existing file contents.",
    )

    @field_validator("search_replace_chunks")
    @classmethod
    def validate_chunks(cls, chunks):
        for i, chunk in enumerate(chunks):
            if not chunk.get("search", "").strip():
                raise ValueError(f"Chunk {i}: 'search' must contain non-empty content copied from the file")
        return chunks


class AskUser(BaseModel):
    """
    Stop the autonomous loop and request clarification or permission from the user.
    """
    question: str = Field(description="The specific question or clarification needed.")


class SemanticSearch(BaseModel):
    """
    Searches the entire codebase using vector embeddings.
    Returns the most conceptually relevant code snippets and their file locations.
    """
    query: str = Field(description="A natural language description of what you are looking for (e.g. 'Authentication routing logic').")


def _pydantic_to_tool(name: str, description: str, model: type[BaseModel]) -> dict:
    """Helper to convert Pydantic model to OpenAI tool format."""
    # Support both Pydantic v1 and v2
    schema = model.model_json_schema() if hasattr(model, "model_json_schema") else model.schema()

    # Remove title/descriptions from root schema to keep it clean for API
    if "title" in schema:
        del schema["title"]

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        }
    }


def get_readonly_tools() -> list[dict]:
    """Tools for the auto loop: bash (read-only schema), semantic_search, ask_user.

    No edit_file — the auto loop is read-only.
    """
    return [
        _pydantic_to_tool(
            "execute_bash",
            "Execute a read-only bash command in the project sandbox.",
            ReadOnlyShellCommand,
        ),
        _pydantic_to_tool(
            "semantic_search",
            "Queries codebase embeddings to find conceptually relevant code locations.",
            SemanticSearch,
        ),
        _pydantic_to_tool(
            "ask_user",
            "Pause execution and prompt the user for feedback or answers.",
            AskUser,
        ),
    ]


def get_chat_tools() -> list[dict]:
    """Tools for the chat path: bash (full schema), edit_file, semantic_search, ask_user."""
    return [
        _pydantic_to_tool(
            "execute_bash",
            "Execute a headless bash command in the project sandbox.",
            FullShellCommand,
        ),
        _pydantic_to_tool(
            "edit_file",
            "Applies precise Search/Replace chunks to edit a file. "
            "Each chunk MUST have a non-empty 'search' string copied EXACTLY from the file. "
            "The tool will FAIL if search is empty or not found in the file.",
            EditFile,
        ),
        _pydantic_to_tool(
            "semantic_search",
            "Queries codebase embeddings to find conceptually relevant code locations.",
            SemanticSearch,
        ),
        _pydantic_to_tool(
            "ask_user",
            "Pause execution and prompt the user for feedback or answers.",
            AskUser,
        ),
    ]


# Backward-compatible alias
def get_native_tools() -> list[dict]:
    """Legacy alias — returns chat tools (full access)."""
    return get_chat_tools()
