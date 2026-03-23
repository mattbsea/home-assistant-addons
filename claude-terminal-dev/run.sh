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
        # Preserve build-time files (Claude CLI, nvm) before destroying /home/claude
        # These are backed up at /opt/claude-cli during docker build
        if [ -d /opt/claude-cli ] && [ ! -d "$data_home/.local/bin" ]; then
            cp -a /opt/claude-cli "$data_home/.local"
            bashio::log.info "  - Claude CLI copied from build to $data_home/.local"
        fi
        # Preserve nvm installation from build
        if [ -d /home/claude/.nvm ] && [ ! -d "$data_home/.nvm" ]; then
            cp -a /home/claude/.nvm "$data_home/.nvm"
            bashio::log.info "  - nvm copied from build to $data_home/.nvm"
        fi
        rm -rf /home/claude
        ln -sf "$data_home" /home/claude
        bashio::log.info "  - /home/claude -> $data_home (persistent symlink created)"
    fi

    # Re-enter the home directory so the shell holds a valid CWD.
    # The rm -rf above may have invalidated our working directory.
    cd "$data_home" || cd /

    # Ensure Claude native binary is on PATH via /data/home/.local/bin
    local native_bin_dir="$data_home/.local/bin"
    if [ ! -d "$native_bin_dir" ]; then
        mkdir -p "$native_bin_dir"
    fi
    # Symlink from /usr/local/bin if not already pointing to the right place
    if [ -f "$native_bin_dir/claude" ]; then
        ln -sf "$native_bin_dir/claude" /usr/local/bin/claude
        bashio::log.info "  - Claude binary linked: /usr/local/bin/claude -> $native_bin_dir/claude"
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

    # Transfer ownership of all /data files to the non-root claude user.
    # Done last so every file created above (symlinks, migrated
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

# Verify nvm and Node.js 20 are available (installed at build time)
verify_node() {
    local nvm_dir="/home/claude/.nvm"
    if [ -s "$nvm_dir/nvm.sh" ]; then
        bashio::log.info "nvm found at $nvm_dir"
        local node_version
        node_version=$(gosu claude bash -c 'export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && node --version' 2>/dev/null || echo "unknown")
        bashio::log.info "Node.js version: $node_version"
    else
        bashio::log.warning "nvm not found, falling back to system Node.js"
    fi
}

# Install required tools
install_tools() {
    bashio::log.info "Installing additional tools..."
    apt-get update -qq
    if ! apt-get install -y --no-install-recommends jq curl; then
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

# Setup helper scripts
setup_scripts() {
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

# Build tab configuration JSON from add-on config and export as env var
build_tab_config() {
    local auto_launch_claude
    local claude_args

    auto_launch_claude=$(bashio::config 'auto_launch_claude' 'true')
    claude_args=$(bashio::config 'claude_args' '')

    # Normalize em-dashes to double hyphens (mobile keyboards auto-correct -- to —)
    claude_args=$(echo "$claude_args" | sed 's/—/--/g')
    # Handle bashio "null" for unset values
    if [ "$claude_args" = "null" ]; then
        claude_args=""
    fi

    bashio::log.info "Auto-launch Claude: ${auto_launch_claude}"
    bashio::log.info "Claude args: ${claude_args:-<none>}"

    # Start building JSON array
    local tabs_json="["

    # Main tab: Claude or Shell depending on auto_launch_claude
    if [ "$auto_launch_claude" = "true" ]; then
        # Parse claude_args into a JSON array of arguments
        local args_json="[]"
        if [ -n "$claude_args" ]; then
            # Convert space-separated args to JSON array
            args_json=$(echo "$claude_args" | jq -R 'split(" ") | map(select(length > 0))')
        fi
        tabs_json="${tabs_json}{\"label\":\"Claude\",\"command\":\"claude\",\"args\":${args_json},\"cwd\":\"${HOME}\",\"autoStart\":true}"
    else
        tabs_json="${tabs_json}{\"label\":\"Shell\",\"command\":\"/bin/bash\",\"args\":[],\"cwd\":\"${HOME}\",\"autoStart\":true}"
    fi

    # Add configured Claude tabs
    if bashio::config.has_value 'claude_tabs'; then
        local count
        count=$(bashio::config 'claude_tabs | length')

        local i
        for ((i = 0; i < count; i++)); do
            local directory tab_args tab_prompt
            directory=$(bashio::config "claude_tabs[${i}].directory")
            tab_args=$(bashio::config "claude_tabs[${i}].args" '')
            tab_prompt=$(bashio::config "claude_tabs[${i}].prompt" '')

            # Trim leading/trailing whitespace from directory and args
            directory=$(echo "$directory" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            tab_args=$(echo "$tab_args" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

            # Normalize em-dashes to double hyphens
            tab_args=$(echo "$tab_args" | sed 's/—/--/g')

            # Handle bashio "null" for unset optional values
            if [ "$tab_prompt" = "null" ] || [ -z "$tab_prompt" ]; then
                tab_prompt=""
            fi
            if [ "$tab_args" = "null" ] || [ -z "$tab_args" ]; then
                tab_args=""
            fi

            # Validate directory exists
            if [ ! -d "$directory" ]; then
                bashio::log.warning "Claude tab directory does not exist, creating: ${directory}"
                mkdir -p "$directory"
                chown claude:claude "$directory"
            fi

            # Derive tab label from directory basename
            local label
            label=$(basename "$directory")

            # Build args array: split tab_args + append prompt as positional arg
            local args_json="[]"
            if [ -n "$tab_args" ] && [ -n "$tab_prompt" ]; then
                args_json=$(jq -n --arg a "$tab_args" --arg p "$tab_prompt" '$a | split(" ") | map(select(length > 0)) + [$p]')
            elif [ -n "$tab_args" ]; then
                args_json=$(jq -n --arg a "$tab_args" '$a | split(" ") | map(select(length > 0))')
            elif [ -n "$tab_prompt" ]; then
                args_json=$(jq -n --arg p "$tab_prompt" '[$p]')
            fi

            bashio::log.info "  Claude tab '${label}' in ${directory}"
            tabs_json="${tabs_json},{\"label\":\"${label}\",\"command\":\"claude\",\"args\":${args_json},\"cwd\":\"${directory}\",\"autoStart\":true,\"restart\":true,\"restartDelay\":32}"
        done

        bashio::log.info "Configured ${count} claude tab(s)"
    fi

    tabs_json="${tabs_json}]"

    export CLAUDE_TAB_CONFIG="$tabs_json"
    bashio::log.info "Tab config: ${tabs_json}"
}

# Start main web terminal
start_web_terminal() {
    local port=7682
    bashio::log.info "Starting web terminal on port ${port}..."

    # Log environment information for debugging
    bashio::log.info "Environment variables:"
    bashio::log.info "ANTHROPIC_CONFIG_DIR=${ANTHROPIC_CONFIG_DIR}"
    bashio::log.info "HOME=${HOME}"

    # Build the tab configuration from add-on config
    build_tab_config

    export WEB_TERMINAL_PORT="$port"

    # Drop from root to the claude user for the Node.js server process
    # gosu performs a clean privilege drop (no sudo, no setuid shell overhead)
    # Source nvm to get Node.js 20 on PATH (falls back to system Node.js)
    exec gosu claude bash -c '
        export NVM_DIR="$HOME/.nvm"
        if [ -s "$NVM_DIR/nvm.sh" ]; then
            . "$NVM_DIR/nvm.sh"
        fi
        node /opt/web-terminal/server.js
    '
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
    verify_node
    install_tools
    setup_scripts
    install_persistent_packages
    start_web_terminal
}

# Execute main function
main "$@"
