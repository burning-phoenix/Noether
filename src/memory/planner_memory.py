"""
Minimal Viable Memory for Planner Agent.

Components:
1. SimpleConversationMemory: Sliding window of last N exchanges.
2. TaskState: Tracks task decomposition and progress.
3. SessionMemory: Key-Value store for session facts.
"""

from typing import List, Dict, Any, Optional, Union

class SimpleConversationMemory:
    """Dead simple: keep last N exchanges."""
    
    def __init__(self, max_exchanges: int = 10):
        self.buffer: List[Dict[str, str]] = []
        self.max_exchanges = max_exchanges
    
    def add(self, role: str, content: str) -> None:
        """Add message to buffer."""
        self.buffer.append({"role": role, "content": content})
        
        # Sliding window: keep only last N (user+assistant pairs)
        if len(self.buffer) > self.max_exchanges * 2:
            self.buffer = self.buffer[-(self.max_exchanges * 2):]
    
    def get_context(self) -> str:
        """Format for LLM prompt."""
        return "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in self.buffer
        ])


class TaskState:
    """Track what Planner has decomposed and what Coder has completed."""
    
    def __init__(self):
        self.current_task: str = ""
        self.subtasks: List[Any] = []  # Can be Task objects or strings
        self.completed_subtasks: List[str] = [] # IDs of completed tasks
        self.created_files: List[str] = []
    
    def set_task(self, description: str, subtasks: List[Any]) -> None:
        """Planner sets the task decomposition."""
        self.current_task = description
        self.subtasks = subtasks
        self.completed_subtasks = []
    
    def mark_complete(self, subtask_id: str, files_created: List[str]) -> None:
        """Coder marks subtask complete."""
        if subtask_id not in self.completed_subtasks:
            self.completed_subtasks.append(subtask_id)
        self.created_files.extend(files_created)
    
    def get_progress_context(self) -> str:
        """Simple text summary for next LLM call."""
        if not self.current_task:
            return "No active task."
            
        return f"""
Current Task: {self.current_task}

Progress:
- Total subtasks: {len(self.subtasks)}
- Completed: {len(self.completed_subtasks)}
- Files created: {', '.join(self.created_files)}

Next subtask: {self._get_next_subtask()}
"""
    
    def _get_next_subtask(self) -> str:
        # Robustly handle both objects (with .id or .description) and strings
        remaining = []
        for s in self.subtasks:
            # Try to get ID, fall back to string representation
            s_id = getattr(s, 'id', str(s))
            if s_id not in self.completed_subtasks:
                remaining.append(s)
                
        if not remaining:
            return "All complete"
            
        # Return description of next task
        next_task = remaining[0]
        if hasattr(next_task, 'description'):
            return next_task.description
        return str(next_task)


class SessionMemory:
    """Remember key facts during the current session."""
    
    def __init__(self):
        self.facts: Dict[str, str] = {}
    
    def store(self, key: str, value: str) -> None:
        """Store a fact (e.g., 'preferred_framework': 'FastAPI')."""
        self.facts[key] = value
    
    def recall(self, key: str) -> str:
        """Get a fact if it exists."""
        return self.facts.get(key, "")
    
    def get_all_facts(self) -> str:
        """Format all facts for context."""
        if not self.facts:
            return "No stored preferences"
        
        return "\n".join([
            f"- {k}: {v}"
            for k, v in self.facts.items()
        ])


class PlannerMemoryManager:
    """Minimal viable memory for your coding agent MVP."""

    def __init__(self, max_system_events: int = 10):
        self.conversation = SimpleConversationMemory(max_exchanges=10)
        self.task_state = TaskState()
        self.session = SessionMemory()
        self.system_events: List[str] = []  # Command results, errors, etc.
        self.max_system_events = max_system_events

    def get_full_context_for_llm(self) -> str:
        """Build complete context string to prepend to LLM calls."""

        system_events_str = ""
        if self.system_events:
            system_events_str = f"""
=== RECENT SYSTEM EVENTS ===
{chr(10).join(self.system_events[-5:])}
"""

        return f"""
=== SESSION CONTEXT ===
{self.session.get_all_facts()}

=== TASK PROGRESS ===
{self.task_state.get_progress_context()}
{system_events_str}
=== RECENT CONVERSATION ===
{self.conversation.get_context()}
"""

    def record_interaction(self, user_msg: str, assistant_response: str) -> None:
        """After each LLM exchange."""
        self.conversation.add("user", user_msg)
        self.conversation.add("assistant", assistant_response)

    def record_system_event(self, event: str) -> None:
        """Record a system event (command result, error, etc.)."""
        # Truncate long events
        if len(event) > 500:
            event = event[:500] + "... [truncated]"
        self.system_events.append(event)
        # Keep only recent events
        if len(self.system_events) > self.max_system_events:
            self.system_events = self.system_events[-self.max_system_events:]

    def extract_and_store_preference(self, key: str, value: str) -> None:
        """When Planner identifies a user preference."""
        self.session.store(key, value)

    def clear(self) -> None:
        """Reset all memory to a clean state."""
        self.conversation = SimpleConversationMemory(max_exchanges=10)
        self.task_state = TaskState()
        self.session = SessionMemory()
        self.system_events = []

    def update_task_progress(self, subtask_id: str, files: List[str]) -> None:
        """When Coder completes a subtask."""
        self.task_state.mark_complete(subtask_id, files)
