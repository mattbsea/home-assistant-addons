#!/usr/bin/with-contenv bashio

# Enable strict error handling
set -e
set -o pipefail

# Initialize environment for Claude Code CLI using /data (HA best practice)
init_environment() {
    # Use /data exclusively - guaranteed writable by HA Supervisor
    local data_home="/data/home"
    local config_dir="/data/.config"
    local cache_dir="/data/.cache"
    local state_dir="/data/.local/state"
    local claude_config_dir="/data/.config/claude"

    bashio::log.info "Initializing Claude Code environment in /data..."

    # Create all required directories
    if ! mkdir -p "$data_home" "$config_dir/claude" "$cache_dir" "$state_dir" "/data/.local"; then
        bashio::log.error "Failed to create directories in /data"
        exit 1
    fi

    # Set permissions
    chmod 755 "$data_home" "$config_dir" "$cache_dir" "$state_dir" "$claude_config_dir"

    # Persist /home/claude by symlinking it to /data/home
    # This makes /home/claude survive container restarts as /data is a mounted volume
    if [ ! -L /home/claude ]; then
        rm -rf /home/claude
        ln -sf "$data_home" /home/claude
        bashio::log.info "  - /home/claude -> $data_home (persistent symlink created)"
    fi

    # Re-enter the home directory so the shell holds a valid CWD.
    # The rm -rf above may have invalidated our working directory.
    cd "$data_home" || cd /

    # Ensure Claude native binary is available at $HOME/.local/bin/claude
    # The native installer placed it in /home/claude/.local/bin/ during build.
    # At runtime HOME=/data/home, so Claude's self-check looks in /data/home/.local/bin/
    local native_bin_dir="$data_home/.local/bin"
    if [ ! -d "$native_bin_dir" ]; then
        mkdir -p "$native_bin_dir"
    fi
    if [ -f /home/claude/.local/bin/claude ] && [ ! -f "$native_bin_dir/claude" ]; then
        ln -sf /home/claude/.local/bin/claude "$native_bin_dir/claude"
        bashio::log.info "  - Claude native binary linked: $native_bin_dir/claude"
    fi

    # Set XDG and application environment variables
    export HOME="/home/claude"
    export XDG_CONFIG_HOME="$config_dir"
    export XDG_CACHE_HOME="$cache_dir"
    export XDG_STATE_HOME="$state_dir"
    export XDG_DATA_HOME="/data/.local/share"

    # Claude-specific environment variables
    export ANTHROPIC_CONFIG_DIR="$claude_config_dir"
    export ANTHROPIC_HOME="/data"

    # Migrate any existing authentication files from legacy locations
    migrate_legacy_auth_files "$claude_config_dir"

    # Install tmux configuration to user home directory
    if [ -f "/opt/scripts/tmux.conf" ]; then
        cp /opt/scripts/tmux.conf "$data_home/.tmux.conf"
        chmod 644 "$data_home/.tmux.conf"
        bashio::log.info "tmux configuration installed to $data_home/.tmux.conf"
    fi

    # Transfer ownership of all /data files to the non-root claude user.
    # Done last so every file created above (symlinks, tmux.conf, migrated
    # auth files) is included in the chown.
    chown -R claude:claude /data

    bashio::log.info "Environment initialized:"
    bashio::log.info "  - Home: $HOME"
    bashio::log.info "  - Config: $XDG_CONFIG_HOME"
    bashio::log.info "  - Claude config: $ANTHROPIC_CONFIG_DIR"
    bashio::log.info "  - Cache: $XDG_CACHE_HOME"
}

# One-time migration of existing authentication files
migrate_legacy_auth_files() {
    local target_dir="$1"
    local migrated=false

    bashio::log.info "Checking for existing authentication files to migrate..."

    # Check common legacy locations
    local legacy_locations=(
        "/root/.config/anthropic"
        "/root/.anthropic" 
        "/config/claude-config"
        "/tmp/claude-config"
    )

    for legacy_path in "${legacy_locations[@]}"; do
        if [ -d "$legacy_path" ] && [ "$(ls -A "$legacy_path" 2>/dev/null)" ]; then
            bashio::log.info "Migrating auth files from: $legacy_path"
            
            # Copy files to new location
            if cp -r "$legacy_path"/* "$target_dir/" 2>/dev/null; then
                # Set proper permissions
                find "$target_dir" -type f -exec chmod 600 {} \;
                
                # Create compatibility symlink if this is a standard location
                if [[ "$legacy_path" == "/root/.config/anthropic" ]] || [[ "$legacy_path" == "/root/.anthropic" ]]; then
                    rm -rf "$legacy_path"
                    ln -sf "$target_dir" "$legacy_path"
                    bashio::log.info "Created compatibility symlink: $legacy_path -> $target_dir"
                fi
                
                migrated=true
                bashio::log.info "Migration completed from: $legacy_path"
            else
                bashio::log.warning "Failed to migrate from: $legacy_path"
            fi
        fi
    done

    if [ "$migrated" = false ]; then
        bashio::log.info "No existing authentication files found to migrate"
    fi
}

# Update Claude binary to the latest version
update_claude() {
    bashio::log.info "Updating Claude Code to latest version..."
    if gosu claude bash -c 'curl -fsSL https://claude.ai/install.sh | bash' 2>&1; then
        bashio::log.info "Claude Code updated successfully"
    else
        bashio::log.warning "Claude Code update failed, continuing with existing version"
    fi
}

# Install nvm and Node 20
install_nvm() {
    local nvm_dir="/home/claude/.nvm"
    bashio::log.info "Installing nvm and Node.js 20..."
    if gosu claude bash -c 'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash && export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm install 20' 2>&1; then
        bashio::log.info "nvm and Node.js 20 installed successfully"
        bashio::log.info "Installing happy-coder..."
        if gosu claude bash -c 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && npx happy-coder' 2>&1; then
            bashio::log.info "happy-coder installed successfully"
        else
            bashio::log.warning "happy-coder installation failed, continuing without it"
        fi
    else
        bashio::log.warning "nvm installation failed, continuing with system Node.js"
    fi
}

# Install required tools
install_tools() {
    bashio::log.info "Installing additional tools..."
    apt-get update -qq
    if ! apt-get install -y --no-install-recommends jq curl tmux; then
        bashio::log.error "Failed to install required tools"
        exit 1
    fi
    bashio::log.info "Tools installed successfully"
}

# Install persistent packages from config and saved state
install_persistent_packages() {
    bashio::log.info "Checking for persistent packages..."

    local persist_config="/data/persistent-packages.json"
    local apk_packages=""
    local pip_packages=""

    # Collect APT packages from Home Assistant config
    if bashio::config.has_value 'persistent_apt_packages'; then
        local config_apk
        config_apk=$(bashio::config 'persistent_apt_packages')
        if [ -n "$config_apk" ] && [ "$config_apk" != "null" ]; then
            apk_packages="$config_apk"
            bashio::log.info "Found APT packages in config: $apk_packages"
        fi
    fi

    # Collect pip packages from Home Assistant config
    if bashio::config.has_value 'persistent_pip_packages'; then
        local config_pip
        config_pip=$(bashio::config 'persistent_pip_packages')
        if [ -n "$config_pip" ] && [ "$config_pip" != "null" ]; then
            pip_packages="$config_pip"
            bashio::log.info "Found pip packages in config: $pip_packages"
        fi
    fi

    # Also check local persist-install config file
    if [ -f "$persist_config" ]; then
        bashio::log.info "Found local persistent packages config"

        # Get APT packages from local config
        local local_apk
        local_apk=$(jq -r '.apt_packages | join(" ")' "$persist_config" 2>/dev/null || echo "")
        if [ -n "$local_apk" ]; then
            apk_packages="$apk_packages $local_apk"
        fi

        # Get pip packages from local config
        local local_pip
        local_pip=$(jq -r '.pip_packages | join(" ")' "$persist_config" 2>/dev/null || echo "")
        if [ -n "$local_pip" ]; then
            pip_packages="$pip_packages $local_pip"
        fi
    fi

    # Trim whitespace and remove duplicates
    apk_packages=$(echo "$apk_packages" | tr ' ' '\n' | sort -u | tr '\n' ' ' | xargs)
    pip_packages=$(echo "$pip_packages" | tr ' ' '\n' | sort -u | tr '\n' ' ' | xargs)

    # Install APT packages
    if [ -n "$apk_packages" ]; then
        bashio::log.info "Installing persistent APT packages: $apk_packages"
        # shellcheck disable=SC2086
        if apt-get install -y --no-install-recommends $apk_packages; then
            bashio::log.info "APT packages installed successfully"
        else
            bashio::log.warning "Some APT packages failed to install"
        fi
    fi

    # Install pip packages
    if [ -n "$pip_packages" ]; then
        bashio::log.info "Installing persistent pip packages: $pip_packages"
        # shellcheck disable=SC2086
        if pip3 install --break-system-packages --no-cache-dir $pip_packages; then
            bashio::log.info "pip packages installed successfully"
        else
            bashio::log.warning "Some pip packages failed to install"
        fi
    fi

    if [ -z "$apk_packages" ] && [ -z "$pip_packages" ]; then
        bashio::log.info "No persistent packages configured"
    fi
}

# Setup session picker script
setup_session_picker() {
    # Copy session picker script from built-in location
    if [ -f "/opt/scripts/claude-session-picker.sh" ]; then
        if ! cp /opt/scripts/claude-session-picker.sh /usr/local/bin/claude-session-picker; then
            bashio::log.error "Failed to copy claude-session-picker script"
            exit 1
        fi
        chmod +x /usr/local/bin/claude-session-picker
        bashio::log.info "Session picker script installed successfully"
    else
        bashio::log.warning "Session picker script not found, using auto-launch mode only"
    fi

    # Setup authentication helper if it exists
    if [ -f "/opt/scripts/claude-auth-helper.sh" ]; then
        chmod +x /opt/scripts/claude-auth-helper.sh
        bashio::log.info "Authentication helper script ready"
    fi

    # Setup persist-install script if it exists
    if [ -f "/opt/scripts/persist-install.sh" ]; then
        if ! cp /opt/scripts/persist-install.sh /usr/local/bin/persist-install; then
            bashio::log.warning "Failed to copy persist-install script"
        else
            chmod +x /usr/local/bin/persist-install
            bashio::log.info "Persist-install script installed successfully"
        fi
    fi
}

# Legacy monitoring functions removed - using simplified /data approach

# Build the tmux launch command that creates all sessions and attaches.
# The main session is created first, then remote control directories are
# added as additional sessions in the same tmux server.
get_claude_launch_command() {
    local auto_launch_claude
    local claude_args

    # Get configuration value, default to true for backward compatibility
    auto_launch_claude=$(bashio::config 'auto_launch_claude' 'true')

    # Get optional extra CLI arguments for claude (may be empty)
    claude_args=$(bashio::config 'claude_args' '')
    if [ -n "$claude_args" ]; then
        bashio::log.info "Using additional Claude args: ${claude_args}"
        export CLAUDE_ARGS="$claude_args"
    fi

    # Determine the main session command
    local main_session_name="claude"
    local main_cmd
    if [ "$auto_launch_claude" = "true" ]; then
        main_cmd="claude ${claude_args}"
    else
        if [ -f /usr/local/bin/claude-session-picker ]; then
            main_session_name="claude-picker"
            main_cmd="/usr/local/bin/claude-session-picker"
        else
            bashio::log.warning "Session picker not found, falling back to auto-launch"
            main_cmd="claude ${claude_args}"
        fi
    fi

    # Build a script that reuses existing sessions on reconnect or creates
    # new ones on first launch, then attaches to the main session.
    local cmds=""

    # If the session already exists (reconnect), just attach.
    # Otherwise create it along with any remote control windows.
    cmds="if tmux has-session -t ${main_session_name} 2>/dev/null; then tmux attach-session -t ${main_session_name}; exit; fi"

    # Create the main session (first launch)
    cmds="${cmds}; tmux new-session -d -s ${main_session_name} '${main_cmd}'"

    # Add remote control directories as windows in the main session
    if bashio::config.has_value 'remote_control_directories'; then
        local count
        count=$(bashio::config 'remote_control_directories | length')

        local i
        for ((i = 0; i < count; i++)); do
            local directory rc_args rc_prompt
            directory=$(bashio::config "remote_control_directories[${i}].directory")
            rc_args=$(bashio::config "remote_control_directories[${i}].args" '')
            rc_prompt=$(bashio::config "remote_control_directories[${i}].prompt" '')

            # Validate directory exists
            if [ ! -d "$directory" ]; then
                bashio::log.warning "Remote control directory does not exist, creating: ${directory}"
                mkdir -p "$directory"
                chown claude:claude "$directory"
            fi

            # Derive window name from directory basename
            local window_name
            window_name=$(basename "$directory")

            # Build claude command with optional args and prompt
            local claude_cmd="claude"
            if [ -n "$rc_args" ]; then
                claude_cmd="${claude_cmd} ${rc_args}"
            fi
            if [ -n "$rc_prompt" ]; then
                claude_cmd="${claude_cmd} \"${rc_prompt}\""
            fi

            # Wrap in a restart loop with 32s sleep on crash
            local loop_cmd="while true; do ${claude_cmd}; sleep 32; done"

            bashio::log.info "  Remote control window '${window_name}' in ${directory}"
            cmds="${cmds} && tmux new-window -t ${main_session_name} -n '${window_name}' -c '${directory}' '${loop_cmd}'"
        done

        # Return focus to the first window (main claude session)
        cmds="${cmds} && tmux select-window -t ${main_session_name}:0"

        bashio::log.info "Created ${count} remote control window(s). Switch with Ctrl-b n/p or click the tab."
    fi

    # Attach to the main session
    cmds="${cmds} && tmux attach-session -t ${main_session_name}"

    echo "$cmds"
}

# Start main web terminal
start_web_terminal() {
    local port=7681
    bashio::log.info "Starting web terminal on port ${port}..."
    
    # Log environment information for debugging
    bashio::log.info "Environment variables:"
    bashio::log.info "ANTHROPIC_CONFIG_DIR=${ANTHROPIC_CONFIG_DIR}"
    bashio::log.info "HOME=${HOME}"

    # Get the appropriate launch command based on configuration
    local launch_command
    launch_command=$(get_claude_launch_command)
    
    # Log the configuration being used
    local auto_launch_claude
    auto_launch_claude=$(bashio::config 'auto_launch_claude' 'true')
    bashio::log.info "Auto-launch Claude: ${auto_launch_claude}"
    bashio::log.info "Claude args: ${CLAUDE_ARGS:-<none>}"
    
    # Set TTYD environment variable for tmux configuration
    # This disables tmux mouse mode since ttyd has better mouse handling for web terminals
    export TTYD=1

    # Drop from root to the claude user for the terminal process
    # gosu performs a clean privilege drop (no sudo, no setuid shell overhead)
    # Run ttyd with keepalive configuration to prevent WebSocket disconnects
    # See: https://github.com/heytcass/home-assistant-addons/issues/24
    exec gosu claude ttyd \
        --port "${port}" \
        --interface 0.0.0.0 \
        --writable \
        --ping-interval 30 \
        --client-option enableReconnect=true \
        --client-option reconnect=10 \
        --client-option reconnectInterval=5 \
        bash -c "$launch_command"
}

# Run health check
run_health_check() {
    if [ -f "/opt/scripts/health-check.sh" ]; then
        bashio::log.info "Running system health check..."
        chmod +x /opt/scripts/health-check.sh
        /opt/scripts/health-check.sh || bashio::log.warning "Some health checks failed but continuing..."
    fi
}

# Main execution
main() {
    bashio::log.info "Initializing Claude Terminal add-on..."

    # Run diagnostics first (especially helpful for VirtualBox issues)
    run_health_check

    init_environment
    update_claude
    install_nvm
    install_tools
    setup_session_picker
    install_persistent_packages
    start_web_terminal
}

# Execute main function
main "$@"
