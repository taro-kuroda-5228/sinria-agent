---
sidebar_position: 2
title: "Installation"
description: "Install Sinria on Linux, macOS, WSL2, native Windows (early beta), or Android via Termux"
---

# Installation

Get Sinria up and running with the one-line installer or a standard Python install.

## Quick Install

### pip install

If you already have Python 3.11+:

```bash
pip install sinria-agent
sinria
```

This installs the Sinria console script and uses `~/.sinria/` as the runtime data directory. It does **not** require the upstream Hermes package or command to be installed separately.

### One-Line Installer (Linux / macOS / WSL2)

For a git-based install that tracks `main` and gives you the latest changes immediately:

```bash
curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash
```

### Windows (native, PowerShell) — Early Beta

:::warning Early BETA
Native Windows support is **early beta**. It installs and works for common CLI, gateway, cron, MCP, and browser-tool paths, but it has not been road-tested as broadly as POSIX installs. For the most battle-tested Windows setup today, run the Linux installer inside **WSL2**. Please file issues at the Sinria repository when you hit rough edges.
:::

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1 | iex
```

The installer handles **everything**: `uv`, Python 3.11, Node.js 22, `ripgrep`, `ffmpeg`, and a portable Git Bash. It clones the repo under `%LOCALAPPDATA%\sinria\sinria-agent`, creates a virtualenv, adds `sinria` to your **User PATH**, and uses `%LOCALAPPDATA%\sinria` as the native Windows runtime home. Restart your terminal, or open a new PowerShell window, after the install so PATH picks up.

**How Git is handled:**

1. If `git` is already on your PATH, the installer uses your existing install.
2. Otherwise it downloads portable **PortableGit** from the official `git-for-windows` GitHub release and unpacks it to `%LOCALAPPDATA%\sinria\git`. No admin rights required. Completely isolated — it will not interfere with any system Git install, broken or otherwise. On 32-bit Windows, the installer falls back to MinGit; bash-dependent terminal/browser features may be limited there.

**Why not use winget?** Earlier designs auto-installed Git via `winget install Git.Git`, but winget fails badly when a system Git install is partial or broken. The portable Git approach sidesteps winget, the Windows installer registry, and any existing system Git. If Sinria's managed Git ever breaks, run:

```powershell
Remove-Item -Recurse -Force $env:LOCALAPPDATA\sinria\git
```

then re-run the installer.

The installer also sets `HERMES_GIT_BASH_PATH` as a legacy compatibility variable pointing to the located `bash.exe` so Sinria resolves Git Bash deterministically in fresh shells.

If you prefer WSL2, the Linux installer above works inside it; native and WSL installs can coexist without conflict. Native data lives under `%LOCALAPPDATA%\sinria`; WSL data lives under `~/.sinria`.

### Android / Termux

Sinria ships a Termux-aware installer path too:

```bash
curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash
```

The installer detects Termux automatically and switches to a tested Android flow:

- uses Termux `pkg` for system dependencies (`git`, `python`, `nodejs`, `ripgrep`, `ffmpeg`, build tools)
- creates the virtualenv with `python -m venv`
- exports `ANDROID_API_LEVEL` automatically for Android wheel builds
- prefers the broad `.[termux-all]` extra and falls back to the smaller `.[termux]` extra, then a base install, if a package fails to compile
- skips the untested browser / WhatsApp bootstrap by default

If you want the fully explicit path, follow the dedicated [Termux guide](./termux.md).

:::note Windows Feature Parity (Early Beta)
Native Windows is in **early beta**. Everything except the browser-based dashboard chat terminal is intended to run natively on Windows:

- **CLI (`sinria chat`, `sinria setup`, `sinria gateway`, …)** — native, uses your default terminal
- **Gateway (Telegram, Discord, Slack, …)** — native, runs as a background PowerShell process
- **Cron scheduler** — native
- **Browser tool** — native via Chromium / Node.js
- **MCP servers** — native, with stdio and HTTP transports
- **Dashboard `/chat` terminal pane** — **WSL2 only** for now, because it relies on a POSIX PTY; the rest of the dashboard can run natively

Set `HERMES_DISABLE_WINDOWS_UTF8=1` only as a legacy compatibility escape hatch if you hit an encoding-related Windows bug and want to fall back to the old cp1252 stdio path for debugging.
:::

### What the Installer Does

The installer handles dependencies, repo clone/update, virtual environment, global `sinria` command setup, config templates, bundled skills, and the initial setup wizard. By the end, you are ready to chat.

#### Install Layout

| Installer | Code lives at | `sinria` binary | Data directory |
|---|---|---|---|
| pip install | Python site-packages | Python console script, usually `~/.local/bin/sinria` or venv `Scripts\sinria.exe` | `~/.sinria/` on POSIX, `%LOCALAPPDATA%\sinria` on native Windows when the installer sets `SINRIA_HOME` |
| Per-user git installer | `~/.sinria/sinria-agent/` on POSIX, `%LOCALAPPDATA%\sinria\sinria-agent` on Windows | `~/.local/bin/sinria` or venv `Scripts\sinria.exe` | `~/.sinria/` or `%LOCALAPPDATA%\sinria` |
| Root-mode POSIX (`sudo curl … \| sudo bash`) | `/usr/local/lib/sinria-agent/` | `/usr/local/bin/sinria` | `/root/.sinria/` or explicit `$SINRIA_HOME` |

The root-mode **FHS layout** (`/usr/local/lib/…`, `/usr/local/bin/sinria`) matches where other system-wide developer tools land on Linux. It is useful for shared-machine deployments where one system install should serve every user. Per-user config, auth, skills, sessions, and logs still live under each user's `~/.sinria/` or explicit `SINRIA_HOME`.

### After Installation

Reload your shell and start chatting:

```bash
source ~/.bashrc   # or: source ~/.zshrc
sinria             # Start chatting!
```

On Windows, open a new PowerShell window and run:

```powershell
sinria
```

To reconfigure individual settings later:

```bash
sinria model          # Choose your LLM provider and model
sinria tools          # Configure which tools are enabled
sinria gateway setup  # Set up messaging platforms
sinria config set     # Set individual config values
sinria setup          # Or run the full setup wizard
```

---

## Prerequisites

**pip install:** Python 3.11+.

**Git installer:** the installer automatically handles the main dependencies:

- **uv** for Python provisioning and package management
- **Python 3.11** via uv
- **Node.js v22** for browser automation and web/TUI assets
- **ripgrep** for fast file search
- **ffmpeg** for audio format conversion
- **Git / Git Bash** when missing on Windows

:::info
You do **not** need to install Python, Node.js, ripgrep, or ffmpeg manually for the git installer. On Windows, the installer can also provision portable Git Bash when Git is missing.
:::

:::tip Nix users
If you use Nix, there is a dedicated setup path with a Nix flake, declarative NixOS module, and optional container mode. See the **[Nix & NixOS Setup](./nix-setup.md)** guide.
:::

---

## Manual / Developer Installation

If you want to clone the repo and install from source — for contributing, running from a specific branch, or controlling the virtual environment — see the [Development Setup](../developer-guide/contributing.md#development-setup) section in the Contributing guide.

---

## Non-Sudo / System Service User Installs

Running Sinria as a dedicated unprivileged user, for example a `sinria` systemd service account, is supported. The only installer step that may need root on POSIX is Playwright's `--with-deps` system-library install for Chromium. The installer detects whether sudo is available and degrades gracefully: it installs the Chromium binary into the service user's Playwright cache and prints the exact command an administrator can run separately.

**Recommended split (Debian/Ubuntu):**

1. **One time, as an admin user with sudo**, install the system libraries Chromium needs:

   ```bash
   sudo npx playwright install-deps chromium
   ```

2. **As the unprivileged service user**, run the regular installer. It will detect missing sudo, skip `--with-deps`, and install Chromium into the user's local Playwright cache:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash
   ```

   To skip browser automation entirely:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash -s -- --skip-browser
   ```

3. **Make `sinria` available to the service user's shells.** The installer writes the launcher to `~/.local/bin/sinria`. Service accounts often have a minimal PATH that does not include `~/.local/bin`:

   ```bash
   # Option A — add to the service user's profile
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

   # Option B — symlink system-wide, run as an admin
   sudo ln -s /home/sinria/.sinria/sinria-agent/venv/bin/sinria /usr/local/bin/sinria
   ```

4. **Verify:**

   ```bash
   sinria doctor
   ```

   If you get `ModuleNotFoundError: No module named 'dotenv'`, you are invoking the repo source `sinria` file with system Python instead of the venv launcher. Point PATH or the symlink at `~/.sinria/sinria-agent/venv/bin/sinria`.

The same pattern works on Arch, Fedora/RHEL, and openSUSE. Those distros do not support Playwright's Debian-style `--with-deps` flow, so an administrator installs system libraries separately; the installer prints the relevant package-manager hints.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `sinria: command not found` | Reload your shell, open a new terminal, or check PATH |
| API key not set | Run `sinria model`, or configure a provider with `sinria config set ...` |
| Missing config after update | Run `sinria config check`, then `sinria config migrate` |
| Windows native install feels unstable | Try the same installer in WSL2 while native support remains early beta |

For more diagnostics, run `sinria doctor`:

```bash
sinria doctor
```
