"""
Settings and Trace Screen pane.

Allows configuration of LLM providers and models, and provides a
live trace view for observability via the custom tracer.
"""

from typing import Any
import json
import threading
import urllib.request

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Input, RadioSet, RadioButton, Button, Select, Switch, RichLog, ProgressBar
from textual.events import Resize
from textual import on, work

from ..messages import ProviderChanged
from ..modals.indexing_modal import IndexApprovalModal
from ...config.settings import NoetherSettings
from ...backends.provider_registry import get_provider_config, PROVIDERS
from ...observability.tracer import tracer
from ...sandbox.embeddings import CodebaseRAG, HAS_EMBEDDINGS


class SettingsTracePane(Container):
    """Pane for configuration and live observability trace."""

    DEFAULT_CSS = """
    SettingsTracePane {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1fr;
        height: 100%;
    }

    /* Stack them vertically if terminal is too narrow */
    SettingsTracePane.-narrow {
        grid-size: 1;
        grid-columns: 1fr;
        grid-rows: auto 1fr;
    }

    #settings-section {
        height: 100%;
        border-right: solid $surface-lighten-2;
        padding: 1 2;
        overflow-y: auto;
    }

    SettingsTracePane.-narrow #settings-section {
        height: auto;
        max-height: 50%;
        border-right: none;
        border-bottom: solid $surface-lighten-2;
    }

    #trace-section {
        height: 100%;
        padding: 1 2;
    }

    .settings-row {
        height: auto;
        margin-bottom: 1;
        align-vertical: middle;
    }

    .settings-label {
        width: 16;
        text-align: right;
        margin-right: 2;
    }

    .settings-input {
        width: 1fr;
    }

    #trace-log {
        height: 1fr;
        border: solid $surface-lighten-2;
        background: $panel;
    }
    
    #trace-toolbar {
        height: auto;
        margin-bottom: 1;
        align-vertical: middle;
    }

    .hidden {
        display: none;
    }

    .index-section-title {
        text-style: bold;
        margin-top: 2;
        margin-bottom: 1;
    }

    #index-progress {
        margin: 0 0 0 0;
    }

    #index-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, settings: NoetherSettings) -> None:
        super().__init__()
        self.settings = settings
        self.provider = settings.provider
        self.available_models = []

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-section"):
            yield Static("Settings", classes="title")
            
            with Horizontal(classes="settings-row"):
                yield Static("Provider:", classes="settings-label")
                with RadioSet(id="provider-select"):
                    yield RadioButton("Fireworks AI", id="fw-radio", value=(self.provider == "fireworks"))
                    yield RadioButton("OpenRouter", id="or-radio", value=(self.provider == "openrouter"))

            with Horizontal(classes="settings-row"):
                yield Static("API Key:", classes="settings-label")
                current_key = self.settings.active_api_key
                yield Input(
                    value=current_key,
                    placeholder="Enter API Key",
                    password=True,
                    id="api-key-input",
                    classes="settings-input"
                )

            with Horizontal(classes="settings-row"):
                yield Static("Chat Model:", classes="settings-label")
                yield Input(value=self.settings.chat_model, placeholder="Model override (optional)", id="chat-model-input", classes="settings-input")

            with Horizontal(classes="settings-row"):
                yield Static("Coder Model:", classes="settings-label")
                yield Input(value=self.settings.coder_model, placeholder="Model override (optional)", id="coder-model-input", classes="settings-input")

            with Horizontal(classes="settings-row"):
                yield Static("Explorer Model:", classes="settings-label")
                yield Input(value=self.settings.explorer_model, placeholder="Model override (optional)", id="explorer-model-input", classes="settings-input")

            with Horizontal(classes="settings-row"):
                yield Static("", classes="settings-label")
                yield Button("Save & Apply", id="save-settings", variant="success")

            yield Static("Codebase Index", classes="index-section-title")
            with Horizontal(classes="settings-row"):
                yield Static("", classes="settings-label")
                yield Button("Index Codebase", id="index-codebase", variant="primary")
            yield ProgressBar(total=100, id="index-progress", classes="hidden")
            yield Static("", id="index-status", classes="hidden")

        with Vertical(id="trace-section"):
            with Horizontal(id="trace-toolbar"):
                yield Static("Live Trace Pipeline", classes="title")
                yield Button("Clear", id="clear-trace", variant="primary", classes="toolbar-btn")
            yield RichLog(id="trace-log", highlight=True, markup=True)

    def on_resize(self, event: Resize) -> None:
        """Handle terminal resize events to adjust layout dynamically."""
        if event.size.width <= 100:
            self.add_class("-narrow")
        else:
            self.remove_class("-narrow")

    def on_mount(self) -> None:
        """Called when the widget is added to the app."""
        # Subscribe to trace events
        tracer.add_listener(self._handle_trace_event)

    def _handle_trace_event(self, event) -> None:
        """Handle incoming trace events and format them for the RichLog.
        
        Note: `event` is a TraceEvent dataclass, not a dict.
        """
        try:
            log = self.query_one("#trace-log", RichLog)
            evt_type = getattr(event, "event_type", None)
            name = getattr(event, "name", "unknown")
            
            if evt_type == "span_end":
                dur = getattr(event, "duration_ms", 0) or 0
                meta = getattr(event, "metadata", {})
                error = getattr(event, "error", None)
                status = "🔴" if error else "🟢"
                meta_str = json.dumps(meta) if meta else ""
                msg = f"{status} [bold blue]{name}[/] ({dur:.1f}ms) {meta_str}"
                self.app.call_from_thread(log.write, msg)
            elif evt_type == "metric":
                meta = getattr(event, "metadata", {})
                val = meta.get("value", "")
                unit = meta.get("unit", "")
                msg = f"📊 [bold green]{name}[/]: {val}{unit}"
                self.app.call_from_thread(log.write, msg)
            elif evt_type == "error":
                err = getattr(event, "error", "")
                msg = f"❌ [bold red]ERROR in {name}[/]: {err}"
                self.app.call_from_thread(log.write, msg)
        except Exception as e:
            # Log instead of silently swallowing
            import logging
            logging.getLogger("noether.trace_ui").warning(f"Trace event display error: {e}")

    @on(RadioSet.Changed, "#provider-select")
    def on_provider_changed(self, event: RadioSet.Changed) -> None:
        """Handle provider radio button changes."""
        radio_id = event.pressed.id
        self.provider = "fireworks" if radio_id == "fw-radio" else "openrouter"
        
        # Update UI with the appropriate key
        key_input = self.query_one("#api-key-input", Input)
        if self.provider == "fireworks":
            key_input.value = self.settings.fireworks_api_key
        else:
            key_input.value = self.settings.openrouter_api_key
    # Removed refresh models logic as we use free-text inputs now

    @on(Button.Pressed, "#clear-trace")
    def on_clear_trace(self, event: Button.Pressed) -> None:
        """Clear the trace log."""
        self.query_one("#trace-log", RichLog).clear()

    @on(Button.Pressed, "#save-settings")
    def on_save_settings(self, event: Button.Pressed) -> None:
        """Save settings and notify app to apply changes."""
        key_val = self.query_one("#api-key-input", Input).value.strip()
        
        # Update settings object
        self.settings.provider = self.provider
        if self.provider == "fireworks":
            self.settings.fireworks_api_key = key_val
        else:
            self.settings.openrouter_api_key = key_val
            
        self.settings.chat_model = self.query_one("#chat-model-input", Input).value.strip()
        self.settings.coder_model = self.query_one("#coder-model-input", Input).value.strip()
        self.settings.explorer_model = self.query_one("#explorer-model-input", Input).value.strip()
        
        # Save to disk
        self.settings.save()
        
        # Notify app to re-initialize backends
        self.post_message(ProviderChanged(
            provider=self.provider,
            fireworks_key=self.settings.fireworks_api_key,
            openrouter_key=self.settings.openrouter_api_key,
            chat_model=self.settings.chat_model,
            coder_model=self.settings.coder_model,
            explorer_model=self.settings.explorer_model,
        ))
        
        self.app.notify("Settings saved!")

    # Models are now configured via text inputs. No remote fetch needed.

    # --- Codebase Indexing ---

    @on(Button.Pressed, "#index-codebase")
    def on_index_codebase(self, event: Button.Pressed) -> None:
        """Kick off the indexing workflow."""
        self._run_indexing()

    @work(thread=True)
    def _run_indexing(self) -> None:
        """Worker: pre-scan, show approval modal, index with progress."""
        from pathlib import Path

        if not HAS_EMBEDDINGS:
            self.app.call_from_thread(
                self.app.notify,
                "Embedding dependencies not installed.\nRun: pip install fastembed qdrant-client",
                severity="error",
            )
            return

        self.app.call_from_thread(self._set_indexing_ui_state, True)

        # Pre-scan to get file count
        project_root = Path.cwd()
        file_list = CodebaseRAG.scan_files(project_root)
        file_count = len(file_list)

        if file_count == 0:
            self.app.call_from_thread(self._set_indexing_ui_state, False)
            self.app.call_from_thread(self.app.notify, "No indexable files found.", severity="warning")
            return

        # Collect unique extensions present in the scan
        file_types = sorted({p.suffix for p in file_list})

        # Show approval modal (worker-safe pattern: push_screen + Event)
        approved = False
        modal_event = threading.Event()

        def _on_modal_dismiss(result: bool) -> None:
            nonlocal approved
            approved = result
            modal_event.set()

        modal = IndexApprovalModal(file_count=file_count, file_types=file_types)
        self.app.call_from_thread(self.app.push_screen, modal, _on_modal_dismiss)

        if not modal_event.wait(timeout=120):
            # Timed out waiting for user
            self.app.call_from_thread(self._set_indexing_ui_state, False)
            return

        if not approved:
            self.app.call_from_thread(self._set_indexing_ui_state, False)
            return

        # Show progress bar and start indexing
        self.app.call_from_thread(self._show_progress, True)

        rag = CodebaseRAG(project_root=str(project_root))

        def _on_progress(current: int, total: int, rel_path: str) -> None:
            pct = int((current / total) * 100) if total else 100
            self.app.call_from_thread(self._update_index_progress, pct, current, total, rel_path)

        try:
            indexed = rag.index_repository(on_progress=_on_progress)
            # Release the Qdrant lock BEFORE checking coverage
            rag.close()
            self.app.call_from_thread(
                self.app.notify, f"Indexing complete — {indexed} files indexed."
            )
            # Refresh index coverage in status bar (will open a fresh client)
            self.app.call_from_thread(self.app._update_index_coverage)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, f"Indexing failed: {exc}", severity="error"
            )
        finally:
            rag.close()  # Ensure lock released even on error
            self.app.call_from_thread(self._show_progress, False)
            self.app.call_from_thread(self._set_indexing_ui_state, False)

    # --- Indexing UI helpers (called on main thread via call_from_thread) ---

    def _set_indexing_ui_state(self, indexing: bool) -> None:
        """Enable/disable the index button and update its label."""
        btn = self.query_one("#index-codebase", Button)
        btn.disabled = indexing
        btn.label = "Indexing..." if indexing else "Index Codebase"

    def _show_progress(self, visible: bool) -> None:
        """Toggle visibility of the progress bar and status label."""
        bar = self.query_one("#index-progress", ProgressBar)
        status = self.query_one("#index-status", Static)
        if visible:
            bar.remove_class("hidden")
            status.remove_class("hidden")
            bar.update(progress=0)
            status.update("")
        else:
            bar.add_class("hidden")
            status.add_class("hidden")

    def _update_index_progress(self, pct: int, current: int, total: int, file_path: str) -> None:
        """Update progress bar value and status text."""
        self.query_one("#index-progress", ProgressBar).update(progress=pct)
        self.query_one("#index-status", Static).update(
            f"Indexing file {current}/{total}: {file_path}"
        )
