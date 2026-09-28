# Paseo add-on: persistent, user-installable tool locations (see run.sh). Sourced by login shells.
export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
# The Paseo CLI of the installed daemon version (run.sh installs it per start).
for _paseo_dir in "${PASEO_APP_ROOT:-/data/paseo-app}/current/bin" "${NPM_CONFIG_PREFIX}/bin" \
    "${HOME}/.bun/bin" "${HOME}/.cargo/bin" "${HOME}/.local/bin"; do
    case ":${PATH}:" in
        *":${_paseo_dir}:"*) ;;
        *) PATH="${_paseo_dir}:${PATH}" ;;
    esac
done
unset _paseo_dir
export PATH
