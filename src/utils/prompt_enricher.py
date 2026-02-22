"""
Prompt Enricher

Utility functions for extracting file paths from user prompts and 
auto-injecting focused file context before sending to the LLM.
"""

import re
from pathlib import Path
from typing import Optional, Any

class PromptEnricher:
    """Methods to automatically find code context to enrich user prompts."""
    
    @staticmethod
    def extract_file_path(text: str) -> Optional[str]:
        """
        Extract a file path from user text.
        
        Looks for common patterns like:
        - "edit app.py"
        - "in file src/utils.py"
        - "modify models/user.py"
        """
        # Common file extensions
        extensions = r'\.(?:py|js|ts|tsx|jsx|java|cpp|c|h|go|rs|rb|php|swift|kt|scala|sh|yml|yaml|json|md|html|css|scss|sql)'

        # Patterns to match file paths
        patterns = [
            rf'in\s+(?:file\s+)?([^\s]+{extensions})',
            rf'(?:edit|modify|update|change|fix)\s+([^\s]+{extensions})',
            rf'^([^\s]+{extensions})',
            rf'([a-zA-Z0-9_\-./]+{extensions})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                file_path = match.group(1)
                file_path = file_path.strip('"\'')
                return file_path

        return None

    @staticmethod
    def enrich_prompt_with_context(prompt: str, planner_agent: Optional[Any] = None) -> str:
        """
        Auto-inject focused file context into a prompt.
        
        Scans the prompt for a file path and identifiers (class/function notes)
        that appear in Planner's project context. If found, does a focused read
        and prepends the exact code section so the LLM has exact text for edits.
        """
        # We need these from explore_agent
        from ..agents.explore_agent import find_file_in_project, focused_read

        # 1. Try to find a file path in the prompt
        file_name = PromptEnricher.extract_file_path(prompt)
        
        if not file_name:
            # Check Planner's project context for a previously explored file
            if planner_agent and getattr(planner_agent, '_project_context', None):
                header = re.match(r"^#\s+(\S+)", planner_agent._project_context)
                if header:
                    file_name = header.group(1)

        if not file_name:
            return prompt

        # 2. Resolve the file
        project_root = Path.cwd()
        resolved = find_file_in_project(file_name, project_root)
        if not resolved:
            return prompt

        # Display path for Planner (relative to project root)
        if resolved.is_relative_to(project_root):
            display_path = str(resolved.relative_to(project_root))
        else:
            display_path = str(resolved)

        # 3. Extract identifiers from the prompt
        identifiers: list[str] = []
        # CamelCase identifiers 
        identifiers += re.findall(r'\b([A-Z][a-zA-Z0-9]+)\b', prompt)
        
        for m in re.finditer(r'(?:class|function|def|method)\s+(\w{3,})', prompt, re.IGNORECASE):
            identifiers.append(m.group(1))
        for m in re.finditer(r'(\w{3,})\s+(?:class|function|def|method)\b', prompt, re.IGNORECASE):
            identifiers.append(m.group(1))
            
        # snake_case identifiers anywhere in the prompt
        identifiers += re.findall(r'\b(\w+_\w+)\b', prompt)
        
        # Deduplicate, skip common English words
        skip_words = {"The", "This", "That", "Let", "Can", "How", "What", "Use",
                      "Make", "Add", "Remove", "Edit", "Modify", "Update", "Fix",
                      "Change", "Delete", "From", "With", "Into", "Also", "Not",
                      "Search", "Replace", "Code", "File", "Line", "Class",
                      "Function", "Method", "Please", "Could", "Would", "Should"}
        targets = list(dict.fromkeys(t for t in identifiers if t not in skip_words))

        if not targets:
            return prompt

        # 4. Do focused reads for each target
        sections = []
        for target in targets[:3]:  # Cap at 3 to avoid context explosion
            section = focused_read(resolved, target)
            if section:
                sections.append(section)

        if not sections:
            return prompt

        # 5. Prepend the focused context to the prompt
        context_block = "\n\n".join(sections)
        enriched = (
            f"[Auto-read from {display_path}]\n"
            f"{context_block}\n\n"
            f"Use the EXACT content above for any Search/Replace edits. "
            f"Analyze what is provided directly.\n\n"
            f"{prompt}"
        )
        return enriched
