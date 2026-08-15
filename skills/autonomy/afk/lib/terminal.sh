# Каким терминалом открыть окно и как передать ему команду.
#
# Подключается и установщиком (`setup.sh`), и сторожем (`afk-watch`), чтобы
# правило было одно на всех: `afk_detect_terminal` печатает готовое начало
# команды («kitty -e»), к которому дописывается всё остальное.

# Терминалы, которые умеем запускать. Порядок — очередь при переборе.
AFK_KNOWN_TERMINALS="kitty alacritty foot ghostty wezterm konsole gnome-terminal
kgx ptyxis tilix xfce4-terminal mate-terminal lxterminal deepin-terminal
terminator qterminal urxvt st xterm"

# Чем терминал отделяет свои флаги от команды: у большинства это -e, но
# у семейства GNOME это --, у wezterm — start --, а у terminator — -x.
afk_term_flag() {
  case "$1" in
    gnome-terminal|kgx|ptyxis|tilix) printf -- '--' ;;
    wezterm)                         printf 'start --' ;;
    terminator|xfce4-terminal)       printf -- '-x' ;;
    *)                               printf -- '-e' ;;
  esac
}

# Терминал человека: сначала то, чем он сам представился, потом дерево
# процессов (самый честный признак — кто держит окно), потом что установлено.
afk_detect_terminal() {
  local name="" parent comm known

  case "${TERM_PROGRAM:-}" in
    ghostty) name=ghostty ;;
    WezTerm) name=wezterm ;;
    kitty)   name=kitty ;;
  esac

  if [[ -z "$name" ]]; then
    case "${TERM:-}" in
      xterm-kitty)   name=kitty ;;
      foot*)         name=foot ;;
      alacritty)     name=alacritty ;;
      xterm-ghostty) name=ghostty ;;
      wezterm)       name=wezterm ;;
      konsole*)      name=konsole ;;
      rxvt*)         name=urxvt ;;
    esac
  fi

  if [[ -z "$name" ]]; then
    parent="${PPID:-0}"
    while [[ "$parent" =~ ^[0-9]+$ ]] && (( parent > 1 )); do
      comm="$(ps -o comm= -p "$parent" 2>/dev/null | tr -d ' ')"
      for known in $AFK_KNOWN_TERMINALS; do
        if [[ "$comm" == "$known" || "$comm" == "$known-wrapped" ]]; then
          name="$known"
          break 2
        fi
      done
      parent="$(ps -o ppid= -p "$parent" 2>/dev/null | tr -d ' ')"
    done
  fi

  if [[ -z "$name" ]]; then
    for known in $AFK_KNOWN_TERMINALS; do
      command -v "$known" >/dev/null 2>&1 && { name="$known"; break; }
    done
  fi

  [[ -n "$name" ]] && command -v "$name" >/dev/null 2>&1 || return 1
  printf '%s %s' "$name" "$(afk_term_flag "$name")"
}
