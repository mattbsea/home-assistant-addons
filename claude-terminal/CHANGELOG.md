# Changelog

## 2.0.11

### ✨ Improvements
- **Media folder access**: The add-on now maps the Home Assistant `/media` folder (`media:rw`), so mounted storage such as `/media/USBDisk` is accessible from inside the terminal. Requires reinstalling/updating the add-on and a restart for the new mount to take effect.
- **Tabs repaint on switch**: Switching to a tab now forces a full xterm repaint (`refresh`) after the fit, so terminals that were hidden (`display:none`) re-render their visible contents immediately instead of showing stale or garbled output until the next keystroke.

## 2.0.10

### ✨ Improvements
- **Clearer auto-continue logging**: When a rate/usage/session limit is detected, the add-on now logs an explicit `limit detected — "<message>"` line at the moment of detection, followed by a separate `will send "continue" at <time>` line noting exactly when the resume will fire. Previously detection and scheduling were fused into a single line that only appeared for newly-scheduled events.

## 2.0.9

### ✨ New Features
- **Version in header**: The tab bar now shows "Claude Terminal vX.Y.Z" on the right, so you can see at a glance which add-on version is running. On narrow (phone) screens only the version number is shown to save space.

## 2.0.8

### ✨ New Features
- **Auto-continue after rate limits**: When Claude hits a rate, usage, or session limit (e.g. "5-hour limit reached - resets 3pm (UTC)", "Claude usage limit reached. Resets at 2pm", "Please try again in 5 hours"), the add-on now parses the reset time from the message, waits until 1 minute after the limit lifts, and automatically types `continue` + Enter so the session resumes unattended — even with the browser tab closed, since detection runs server-side on the PTY output stream
  - Handles absolute reset times with optional timezones ("resets 3pm (Europe/Dublin)") and relative ones ("try again in 5 hours"); if a limit is detected but no time can be parsed, it retries every 30 minutes
  - Applies to every Claude tab independently; disable with the new `auto_continue: false` add-on option

## 2.0.7

### ✨ New Features
- **SSH agent always available**: The terminal server now runs under `ssh-agent`, so a single agent is started for the add-on and every terminal tab (Claude or shell) inherits `SSH_AUTH_SOCK`. Run `ssh-add` once and your key is available across all tabs and across tab restarts — useful for `git` over SSH and remote logins. The agent is torn down automatically when the add-on stops.
- **Escape & arrow keys on mobile**: The mobile toolbar now includes Esc and ←/↑/↓/→ buttons alongside Paste/Copy, so phones and tablets (whose soft keyboards usually lack these) can navigate the shell, edit command-line history, and dismiss prompts. Like the Paste/Copy buttons, they only appear on touch devices with no mouse.

## 2.0.6

### 🐛 Bug Fixes
- **Mobile toolbar no longer appears on desktops with touchscreens**: Tightened the CSS gate from `(hover: none) and (pointer: coarse)` to `(any-pointer: coarse) and (not (any-pointer: fine))`, so the floating Paste/Copy buttons only show when the device has no mouse at all (real phones/tablets). Touchscreen laptops and 2-in-1s see the desktop UI again.

## 2.0.5

### ✨ New Features
- **Mobile copy/paste support**: Floating Paste/Copy buttons now appear on touch devices (phones/tablets), making the terminal usable from mobile browsers and the Home Assistant Companion app
  - **Paste**: tries the native Clipboard API first; falls back to a textarea modal where you can long-press to paste from the system menu — works even when the ingress iframe blocks clipboard access
  - **Copy**: copies the current xterm.js selection to the clipboard with `execCommand('copy')` fallback
  - Buttons are CSS-gated to `(hover: none) and (pointer: coarse)` so desktop users see no change
- **Bun preinstalled**: The [Bun](https://bun.sh) JavaScript runtime and package manager is now installed system-wide at `/usr/local/bin/bun` on amd64 and aarch64 builds (armv7 is skipped — Bun has no armv7 binaries)

## 1.24.2

### 🐛 Bug Fixes
- **Remote control directories now appear as tmux tabs**: Changed remote control directories from separate tmux sessions to windows within the main session, so they appear as switchable tabs in the status bar (Ctrl-b n/p or click)

## 1.21.0

### ✨ New Features
- **Persistent `/home/claude` directory**: `/home/claude` is now symlinked to `/data/home` at startup, so all files in the home directory survive container restarts
- **`/home/claude` as default working directory**: Dockerfile `WORKDIR` and runtime `HOME` are both set to `/home/claude`, giving a consistent and intuitive working location

## 1.20.1

### 🐛 Bug Fixes
- **Fixed non-root user file ownership**: `chown -R claude:claude /data` now runs after all files are created in `init_environment()`, so symlinks, tmux config, and migrated auth files are all owned by the `claude` user
- **Fixed Claude binary access**: Claude Code is now installed as the `claude` user during the Docker build so all supporting files in `~/.local/` are owned by uid 1000 and accessible at runtime

## 1.20.0

### 🔒 Security
- **Run as non-root user**: The terminal process (ttyd, bash, Claude) now runs as an unprivileged `claude` user (uid/gid 1000) instead of root
  - `gosu` performs a clean privilege drop after startup tasks (apt installs, dir setup) complete as root
  - `/data` directory ownership transferred to the `claude` user on startup
  - Claude binary made world-executable so the non-root user can invoke it

## 1.19.1

### 🐛 Bug Fix
- **Fixed `claude_args` not being passed to Claude**: Single quotes in the tmux command prevented shell variable expansion, so configured arguments were silently ignored. Args are now embedded directly into the command string.

## 1.9.0

### ✨ New Features
- **Claude CLI arguments**: New `claude_args` configuration option lets you pass extra command-line flags to Claude on every launch (e.g. `--model claude-opus-4-5`, `--verbose`)
  - Works with both auto-launch mode and all session picker options (new, continue, resume)
  - Leave blank for default behaviour

### 🔒 Security
- **Data-only volume mount**: The add-on no longer mounts the Home Assistant `/config` folder
  - Only the add-on's own `/data` directory is mounted, preventing unintended access to HA configuration files
  - Working directory changed from `/config` to `/data`

### 🛠️ Technical Details
- `ttyd` is now installed at build time from the official GitHub release binary (was failing at runtime because `ttyd` is not in Debian apt repositories)
- `CLAUDE_ARGS` environment variable propagated from `run.sh` into the session picker so all launch paths respect the configured arguments

## 1.8.0

### 🔄 Breaking Change - Switched from Alpine to Debian
- **Base image changed to Debian 12 (bookworm)**: Resolves incompatibility between Claude Code CLI and Alpine's musl libc
  - Claude Code requires glibc, which is provided by Debian but not Alpine (musl)
  - All three architectures updated: amd64, aarch64, armv7
- **`persistent_apk_packages` renamed to `persistent_apt_packages`**: Update your add-on configuration if you use this option
  - `persist-install apk` command renamed to `persist-install apt`
  - All package management now uses `apt-get` instead of `apk`

### 🛠️ Technical Details
- `apt-get` replaces `apk` for all package installation
- Python packages renamed to Debian conventions (e.g. `py3-pip` → `python3-pip`)
- `yq` installed as a pre-built binary (mikefarah/yq) for multi-arch compatibility
- Persistent packages JSON config key changed from `apk_packages` to `apt_packages`

### 📦 Migration Guide
If you have `persistent_apk_packages` set in your add-on configuration, rename it to `persistent_apt_packages`. Package names remain the same — most Alpine package names have direct apt equivalents.

```yaml
# Before
persistent_apk_packages:
  - vim
  - htop

# After
persistent_apt_packages:
  - vim
  - htop
```

## 1.7.0

### ✨ New Features
- **Session Persistence with tmux** (#46): Claude sessions now survive browser navigation
  - Sessions persist when navigating away from the terminal in Home Assistant
  - New "Reconnect to existing session" option in session picker (option 0)
  - Seamless session resumption - conversations continue exactly where you left off
  - tmux integration provides robust session management
  - Contributed by [@petterl](https://github.com/petterl)

### 🛠️ Technical Details
- Added tmux package to container
- Custom tmux configuration optimized for web terminals:
  - Mouse mode intelligently disabled when using ttyd (prevents conflicts)
  - OSC 52 clipboard support for copy/paste to browser
  - 50,000 line history buffer for extensive scrollback
  - Vi-style keybindings in copy mode
  - Visual improvements with better status bar
- Session picker enhanced with reconnection logic
- Automatic session cleanup and management

### 🎯 User Experience
- No more lost work when switching between Home Assistant pages
- Browser refresh no longer interrupts Claude conversations
- Tab switching preserves full session state including history
- Improved reliability for long-running Claude sessions

## 1.6.1

### 🐛 Bug Fix - Native Install Path Mismatch
- **Fixed "installMethod is native, but directory does not exist" error**: Claude binary now available at `$HOME/.local/bin/claude` at runtime
  - **Root cause**: Native installer places Claude at `/root/.local/bin/claude` during Docker build, but at runtime `HOME=/data/home`, so Claude's self-check looks in `/data/home/.local/bin/claude` which didn't exist
  - **Solution**: Symlink created from `/data/home/.local/bin/claude` → `/root/.local/bin/claude` on startup
  - **Result**: Claude native binary resolves correctly regardless of HOME directory change
  - Ref: [ESJavadex/claude-code-ha#3](https://github.com/ESJavadex/claude-code-ha/issues/3)

## 1.6.0 - 2026-01-26

### 🔄 Changed
- **Native Claude Code Installation**: Switched from npm package to official native installer
  - Uses `curl -fsSL https://claude.ai/install.sh | bash` instead of `npm install -g @anthropic-ai/claude-code`
  - Native binary provides automatic background updates from Anthropic
  - Faster startup (no Node.js interpreter overhead)
  - Claude binary symlinked to `/usr/local/bin/claude` for easy access
- **Simplified execution**: All scripts now call `claude` directly instead of `node $(which claude)`
- **Cleaner Dockerfile**: Removed npm retry/timeout configuration (no longer needed)

### 📦 Notes
- Node.js and npm remain available as development tools
- Existing authentication and configuration files are unaffected

## 1.5.0

### ✨ New Features
- **Persistent Package Management** (#32): Install APK and pip packages that survive container restarts
  - New `persist-install` command for installing packages from the terminal
  - Configuration options: `persistent_apk_packages` and `persistent_pip_packages`
  - Packages installed via command or config are automatically reinstalled on startup
  - Supports both Home Assistant add-on config and local state file
  - Inspired by community contribution from [@ESJavadex](https://github.com/ESJavadex)

### 📦 Usage Examples
```bash
# Install APK packages persistently
persist-install apk vim htop

# Install pip packages persistently
persist-install pip requests pandas numpy

# List all persistent packages
persist-install list

# Remove from persistence (package remains until restart)
persist-install remove apk vim
```

### 🛠️ Configuration
Add to your add-on config to auto-install packages:
```yaml
persistent_apk_packages:
  - vim
  - htop
persistent_pip_packages:
  - requests
  - pandas
```

## 1.4.1

### 🐛 Bug Fixes
- **Actually include Python and development tools** (#30): Fixed Dockerfile to include tools documented in v1.4.0
  - Resolves #27 (Add git to container)
  - Resolves #29 (v1.4.0 missing Python and development tools)
- **Added yq**: YAML processor for Home Assistant configuration files

## 1.4.0

### ✨ New Features
- **Added Python and development tools** (#26): Enhanced container with scripting and automation capabilities
  - **Python 3.11** with pip and commonly-used libraries (requests, aiohttp, yaml, beautifulsoup4)
  - **git** for version control
  - **vim** for advanced text editing
  - **jq** for JSON processing (essential for API work)
  - **tree** for directory visualization
  - **wget** and **netcat** for network operations

### 📦 Notes
- Image size increased from ~300 MB to ~457 MB (+52%) to accommodate new tools

## 1.3.2

### 🐛 Bug Fixes
- **Improved installation reliability** (#16): Enhanced resilience for network issues during installation
  - Added retry logic (3 attempts) for npm package installation
  - Configured npm with longer timeouts for slow/unstable connections
  - Explicitly set npm registry to avoid DNS resolution issues
  - Added 10-second delay between retry attempts

### 🛠️ Improvements
- **Enhanced network diagnostics**: Better troubleshooting for connection issues
  - Added DNS resolution checks to identify network configuration problems
  - Check connectivity to GitHub Container Registry (ghcr.io)
  - Extended connection timeouts for virtualized environments
  - More detailed error messages with specific solutions
- **Better virtualization support**: Improved guidance for VirtualBox and Proxmox users
  - Enhanced VirtualBox detection with detailed configuration requirements
  - Added Proxmox/QEMU environment detection
  - Specific network adapter recommendations for VM installations
  - Clear guidance on minimum resource requirements (2GB RAM, 8GB disk)

## 1.3.1

### 🐛 Critical Fix
- **Restored config directory access**: Fixed regression where add-on couldn't access Home Assistant configuration files
  - Re-added `config:rw` volume mapping that was accidentally removed in 1.2.0
  - Users can now properly access and edit their configuration files again

## 1.3.0

### ✨ New Features
- **Full Home Assistant API Access**: Enabled complete API access for automations and entity control
  - Added `hassio_api`, `homeassistant_api`, and `auth_api` permissions
  - Set `hassio_role` to 'manager' for full Supervisor access
  - Created comprehensive API examples script (`ha-api-examples.sh`)
  - Includes Supervisor API, Core API, and WebSocket examples
  - Python and bash code examples for entity control

### 🐛 Bug Fixes
- **Fixed authentication paste issues** (#14): Added authentication helper for clipboard problems
  - New authentication helper script with multiple input methods
  - Manual code entry option when clipboard paste fails
  - File-based authentication via `/config/auth-code.txt`
  - Integrated into session picker as menu option

### 🛠️ Improvements
- **Enhanced diagnostics** (#16): Added comprehensive health check system
  - System resource monitoring (memory, disk space)
  - Permission and dependency validation
  - VirtualBox-specific troubleshooting guidance
  - Automatic health check on startup
  - Improved error handling with strict mode

## 1.2.1

### 🔧 Internal Changes
- Fixed YAML formatting issues for better compatibility
- Added document start marker and fixed line lengths

## 1.2.0

### 🔒 Authentication Persistence Fix (PR #15)
- **Fixed OAuth token persistence**: Tokens now survive container restarts
  - Switched from `/config` to `/data` directory (Home Assistant best practice)
  - Implemented XDG Base Directory specification compliance
  - Added automatic migration for existing authentication files
  - Removed complex symlink/monitoring systems for simplicity
  - Maintains full backward compatibility

## 1.1.4

### 🧹 Maintenance
- **Cleaned up repository**: Removed erroneously committed test files (thanks @lox!)
- **Improved codebase hygiene**: Cleared unnecessary temporary and test configuration files

## 1.1.3

### 🐛 Bug Fixes
- **Fixed session picker input capture**: Resolved issue with ttyd intercepting stdin, preventing proper user input
- **Improved terminal interaction**: Session picker now correctly captures user choices in web terminal environment

## 1.1.2

### 🐛 Bug Fixes
- **Fixed session picker input handling**: Improved compatibility with ttyd web terminal environment
- **Enhanced input processing**: Better handling of user input with whitespace trimming
- **Improved error messages**: Added debugging output showing actual invalid input values
- **Better terminal compatibility**: Replaced `echo -n` with `printf` for web terminals

## 1.1.1

### 🐛 Bug Fixes  
- **Fixed session picker not found**: Moved scripts from `/config/scripts/` to `/opt/scripts/` to avoid volume mapping conflicts
- **Fixed authentication persistence**: Improved credential directory setup with proper symlink recreation
- **Enhanced credential management**: Added proper file permissions (600) and logging for debugging
- **Resolved volume mapping issues**: Scripts now persist correctly without being overwritten

## 1.1.0

### ✨ New Features
- **Interactive Session Picker**: New menu-driven interface for choosing Claude session types
  - 🆕 New interactive session (default)
  - ⏩ Continue most recent conversation (-c)
  - 📋 Resume from conversation list (-r) 
  - ⚙️ Custom Claude command with manual flags
  - 🐚 Drop to bash shell
  - ❌ Exit option
- **Configurable auto-launch**: New `auto_launch_claude` setting (default: true for backward compatibility)
- **Added nano text editor**: Enables `/memory` functionality and general text editing

### 🛠️ Architecture Changes
- **Simplified credential management**: Removed complex modular credential system
- **Streamlined startup process**: Eliminated problematic background services
- **Cleaner configuration**: Reduced complexity while maintaining functionality
- **Improved reliability**: Removed sources of startup failures from missing script dependencies

### 🔧 Improvements
- **Better startup logging**: More informative messages about configuration and setup
- **Enhanced backward compatibility**: Existing users see no change in behavior by default
- **Improved error handling**: Better fallback behavior when optional components are missing

## 1.0.2

### 🔒 Security Fixes
- **CRITICAL**: Fixed dangerous filesystem operations that could delete system files
- Limited credential searches to safe directories only (`/root`, `/home`, `/tmp`, `/config`)
- Replaced unsafe `find /` commands with targeted directory searches
- Added proper exclusions and safety checks in cleanup scripts

### 🐛 Bug Fixes
- **Fixed architecture mismatch**: Added missing `armv7` support to match build configuration
- **Fixed NPM package installation**: Pinned Claude Code package version for reliable builds
- **Fixed permission conflicts**: Standardized credential file permissions (600) across all scripts
- **Fixed race conditions**: Added proper startup delays for credential management service
- **Fixed script fallbacks**: Implemented embedded scripts when modules aren't found

### 🛠️ Improvements
- Added comprehensive error handling for all critical operations
- Improved build reliability with better package management
- Enhanced credential management with consistent permission handling
- Added proper validation for script copying and execution
- Improved startup logging for better debugging

### 🧪 Development
- Updated development environment to use Podman instead of Docker
- Added proper build arguments for local testing
- Created comprehensive testing framework with Nix development shell
- Added container policy configuration for rootless operation

## 1.0.0

- First stable release of Claude Terminal add-on:
  - Web-based terminal interface using ttyd
  - Pre-installed Claude Code CLI
  - User-friendly interface with clean welcome message
  - Simple claude-logout command for authentication
  - Direct access to Home Assistant configuration
  - OAuth authentication with Anthropic account
  - Auto-launches Claude in interactive mode