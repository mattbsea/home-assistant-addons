# Paseo add-on: persistent, user-installable tool locations (see run.sh). Sourced by login shells.
export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
for _paseo_dir in "${NPM_CONFIG_PREFIX}/bin" "${HOME}/.cargo/bin" "${HOME}/.local/bin"; do
    case ":${PATH}:" in
        *":${_paseo_dir}:"*) ;;
        *) PATH="${_paseo_dir}:${PATH}" ;;
    esac
done
unset _paseo_dir
export PATH
