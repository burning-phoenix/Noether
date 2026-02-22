"""
Sandbox setup for Noether.

Installs the Anthropic Sandbox Runtime (SRT) to ~/.noether/ for global use.
Can be run standalone or via `noether setup-sandbox`.
"""

import subprocess
import sys
import json
from pathlib import Path


# Global install paths
NOETHER_HOME = Path.home() / ".noether"
SANDBOX_REPO_DIR = NOETHER_HOME / "sandbox-runtime"
CONFIG_FILE = NOETHER_HOME / "srt-config.json"


def check_node_environment():
    """Verify Node.js and npm are available with correct versions."""
    try:
        result = subprocess.run(
            ["node", "--version"], check=True, capture_output=True, text=True
        )
        version_str = result.stdout.strip().lstrip("v")
        major = int(version_str.split(".")[0])
        if major < 18:
            print(f"Node.js version {version_str} detected, but >= 18 is required.")
            print("Remediation: Install Node.js 18+ from https://nodejs.org/ or use nvm:")
            print("  nvm install 18 && nvm use 18")
            sys.exit(1)
        print(f"Node.js v{version_str} verified.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Node.js not found.")
        print("Remediation: Install Node.js 18+ from https://nodejs.org/ or use nvm:")
        print("  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash")
        print("  nvm install 18")
        sys.exit(1)

    try:
        subprocess.run(["npm", "--version"], check=True, capture_output=True)
        print("npm verified.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("npm not found. It should come with Node.js.")
        print("Remediation: Reinstall Node.js from https://nodejs.org/")
        sys.exit(1)


def clone_sandbox_repo(interactive: bool = True):
    """Clone the sandbox runtime repo to ~/.noether/sandbox-runtime/."""
    if SANDBOX_REPO_DIR.exists():
        print(f"Sandbox repo found at {SANDBOX_REPO_DIR}")
        return True

    print(f"Sandbox runtime not found at {SANDBOX_REPO_DIR}")

    if interactive:
        response = input("Clone sandbox-runtime to ~/.noether/? [Y/n] ").strip().lower()
        if response and response != "y":
            return False

    try:
        NOETHER_HOME.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone",
             "https://github.com/anthropic-experimental/sandbox-runtime",
             str(SANDBOX_REPO_DIR)],
            check=True,
        )
        print("Clone successful.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Clone failed: {e}")
        return False


def build_sandbox_runtime():
    """Build the sandbox-runtime project."""
    print(f"Building sandbox runtime in {SANDBOX_REPO_DIR}...")

    if not SANDBOX_REPO_DIR.exists():
        print(f"Error: {SANDBOX_REPO_DIR} does not exist.")
        sys.exit(1)

    try:
        print("  Running npm install...")
        subprocess.run(["npm", "install"], cwd=SANDBOX_REPO_DIR, check=True, capture_output=False)

        print("  Running npm run build...")
        subprocess.run(["npm", "run", "build"], cwd=SANDBOX_REPO_DIR, check=True, capture_output=False)
        print("Sandbox runtime built successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Build failed with exit code {e.returncode}.")
        print("Remediation:")
        print(f"  cd {SANDBOX_REPO_DIR}")
        print("  npm install")
        print("  npm run build")
        sys.exit(1)


def verify_build():
    """Confirm the build output exists."""
    cli_js = SANDBOX_REPO_DIR / "dist" / "cli.js"
    if cli_js.exists():
        print(f"Build verified: {cli_js}")
        return True
    else:
        print(f"Build output not found at {cli_js}")
        print("Remediation: Re-run the build:")
        print(f"  cd {SANDBOX_REPO_DIR} && npm run build")
        return False


def setup_config():
    """Generate the global sandbox configuration file at ~/.noether/srt-config.json."""
    print("Generating sandbox configuration...")

    config = {
        "network": {
            "allowedDomains": [
                "pypi.org",
                "*.pypi.org",
                "files.pythonhosted.org",
                "github.com",
                "*.github.com"
            ],
            "deniedDomains": [],
            "allowLocalBinding": False
        },
        "filesystem": {
            "denyRead": [
                "~/.ssh",
                "~/.aws",
                "~/.config",
                "~/.bash_history",
                "~/.zshrc",
                ".env"
            ],
            "allowWrite": [
                ".",
                "/tmp"
            ],
            "denyWrite": [
                ".env",
                "*.key",
                "secrets/",
                ".git/"
            ]
        }
    }

    NOETHER_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"Configuration written to {CONFIG_FILE}")


def sanity_check() -> bool:
    """Run a quick echo command through SRT to verify end-to-end functionality."""
    cli_js = SANDBOX_REPO_DIR / "dist" / "cli.js"
    if not cli_js.exists() or not CONFIG_FILE.exists():
        return False
    try:
        result = subprocess.run(
            ["node", str(cli_js), "--settings", str(CONFIG_FILE), "-c", "echo noether_ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "noether_ok" in result.stdout
    except Exception:
        return False


def run_setup(interactive: bool = True):
    """Run the full sandbox setup pipeline."""
    print("Noether Sandbox Setup")
    print("=" * 40)
    print(f"Install directory: {NOETHER_HOME}\n")

    check_node_environment()

    if not clone_sandbox_repo(interactive=interactive):
        print("\nSetup incomplete: sandbox repo not available.")
        print("The application will run without sandbox isolation.")
        return False

    build_sandbox_runtime()

    if not verify_build():
        print("\nSetup incomplete: build verification failed.")
        return False

    setup_config()

    # Sanity check: verify end-to-end functionality
    print("Running sanity check...")
    if sanity_check():
        print("Sanity check passed!")
    else:
        print("Warning: Sanity check failed. SRT may not work correctly.")
        print("Try running manually: node ~/.noether/sandbox-runtime/dist/cli.js --settings ~/.noether/srt-config.json -c 'echo hello'")

    print("\nSetup complete! The sandbox is ready for use.")
    return True


def main():
    run_setup(interactive=True)


if __name__ == "__main__":
    main()
