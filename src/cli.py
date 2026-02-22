"""
CLI entry point for Noether.

Handles argument parsing, API key resolution, and app launch.
Subcommands:
  (default)        — Launch the TUI
  setup-sandbox    — Install the sandbox runtime to ~/.noether/
"""

import argparse
import os
import sys
from typing import Optional


def _prompt_for_api_key(provider: str) -> Optional[str]:
    """Prompt the user for their API key before TUI launches."""
    import getpass
    from src.backends.provider_registry import get_provider_config
    
    config = get_provider_config(provider)

    print("╔══════════════════════════════════════════════╗")
    print("║              Noether v0.1.0                   ║")
    print("╠══════════════════════════════════════════════╣")
    print("║                                              ║")
    print(f"║  {config['name']} API key required.                 ║")
    print("║                                              ║")
    print("║  Paste your key (input is hidden):           ║")
    print("╚══════════════════════════════════════════════╝")

    for attempt in range(3):
        key = getpass.getpass(prompt="API key: ")
        key = key.strip()
        if not key:
            print("No key entered. Use --no-api for local-only mode.")
            continue
        if config["key_prefix"] and not key.startswith(config["key_prefix"]):
            print(f"Invalid key format (should start with '{config['key_prefix']}'). Try again.")
            continue
        return key

    print("No valid API key provided after 3 attempts.")
    print("Run with --no-api for local-only mode, or provide key in settings.")
    sys.exit(1)


def _run_app(args):
    """Launch the Noether TUI."""
    # Fast mode is the default; local mode only when --model is provided
    fast_mode = args.model is None

    from src.config.settings import NoetherSettings
    from src.backends.provider_registry import get_provider_config
    
    # Base settings from disk
    settings = NoetherSettings.load()
    
    # CLI overrides
    if args.provider:
        settings.provider = args.provider
        
    config = get_provider_config(settings.provider)

    if args.api_key:
        settings.fireworks_api_key = args.api_key
        
    if args.openrouter_key:
        settings.openrouter_api_key = args.openrouter_key
        
    # Attempt env var fallback for current provider if not set in settings/cli
    if not settings.active_api_key:
        env_val = os.environ.get(config["env_key"])
        if env_val:
            if settings.provider == "fireworks":
                settings.fireworks_api_key = env_val
            elif settings.provider == "openrouter":
                settings.openrouter_api_key = env_val

    # Prompt user if still missing
    if not settings.active_api_key and not args.no_api:
        api_key = _prompt_for_api_key(settings.provider)
        if api_key:
            if settings.provider == "fireworks":
                settings.fireworks_api_key = api_key
            elif settings.provider == "openrouter":
                settings.openrouter_api_key = api_key
            settings.save()  # Persist for next time!

    from src.ui.app import run_app
    run_app(
        coder_model_path=args.model,
        settings=settings,
        fast_mode=fast_mode,
    )


def _run_setup_sandbox(args):
    """Run the sandbox setup pipeline."""
    from .setup_sandbox import run_setup
    success = run_setup(interactive=True)
    sys.exit(0 if success else 1)


def main():
    """CLI entry point for Noether."""
    parser = argparse.ArgumentParser(
        description="Noether — Multi-Agent Code Editor"
    )

    subparsers = parser.add_subparsers(dest="command")

    # Default app arguments (on the top-level parser)
    parser.add_argument(
        "--model", "-m",
        help="Path to local Coder GGUF model (enables local mode)",
        default=os.environ.get("CODER_MODEL_PATH"),
    )
    parser.add_argument(
        "--provider", "-p",
        help="LLM provider: fireworks or openrouter (default: fireworks)",
        default=None,
    )
    parser.add_argument(
        "--api-key", "-k",
        help="Fireworks API key (prompted at startup if not provided)",
        default=None,
    )
    parser.add_argument(
        "--openrouter-key",
        help="OpenRouter API key",
        default=None,
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Local-only mode: skip API key prompt, disable Planner/DeepSeek",
    )

    # setup-sandbox subcommand
    subparsers.add_parser(
        "setup-sandbox",
        help="Install the sandbox runtime (SRT) to ~/.noether/",
    )

    args = parser.parse_args()

    if args.command == "setup-sandbox":
        _run_setup_sandbox(args)
    else:
        _run_app(args)
