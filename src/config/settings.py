"""
Settings management for Noether.

Handles persistence of provider and model selections to ~/.noether/settings.json.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class NoetherSettings:
    provider: str = "fireworks"
    fireworks_api_key: str = ""
    openrouter_api_key: str = ""
    chat_model: str = ""
    coder_model: str = ""
    explorer_model: str = ""
    auto_approve: bool = False
    trace_enabled: bool = True

    @property
    def active_api_key(self) -> str:
        """Get the API key for the currently selected provider."""
        if self.provider == "fireworks":
            return self.fireworks_api_key
        elif self.provider == "openrouter":
            return self.openrouter_api_key
        return ""

    def get_model(self, role: str) -> Optional[str]:
        """Get the overridden model for a role, if any."""
        if role == "chat" and self.chat_model:
            return self.chat_model
        if role == "coder" and self.coder_model:
            return self.coder_model
        if role == "explorer" and self.explorer_model:
            return self.explorer_model
        return None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "NoetherSettings":
        """Load settings from JSON file."""
        if path is None:
            path = Path.home() / ".noether" / "settings.json"
            
        if not path.exists():
            return cls()
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Filter to only valid fields
            valid_keys = cls.__dataclass_fields__.keys()
            filtered_data = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**filtered_data)
        except Exception as e:
            import logging
            logging.getLogger("noether.settings").warning(f"Failed to load settings from {path}: {e}")
            return cls()

    def save(self, path: Optional[Path] = None) -> bool:
        """Save settings to JSON file."""
        if path is None:
            path = Path.home() / ".noether" / "settings.json"
            
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=4)
            return True
        except Exception as e:
            import logging
            logging.getLogger("noether.settings").error(f"Failed to save settings to {path}: {e}")
            return False
