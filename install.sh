#!/usr/bin/env bash
# Instala o visual completo para o usuário atual. Pode ser rodado de novo sem problema.

set -euo pipefail

readonly PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/thinkpad-widgets"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/thinkpad-monitor"
readonly UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly THEME_BACKUP="$CONFIG_DIR/theme-backup/antes-do-crepusculo.ron"
readonly BIN_DIR="$HOME/.local/bin"
readonly RDP_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/rdp-pd/rdp-pd.conf"

if [[ "${XDG_CURRENT_DESKTOP:-}" != *COSMIC* ]]; then
    echo 'Este projeto precisa do desktop COSMIC, em uma sessão gráfica.' >&2
    exit 1
fi

missing=()
python3 -c 'import cairo' 2>/dev/null || missing+=(python3-cairo)
python3 -c 'import psutil' 2>/dev/null || missing+=(python3-psutil)
python3 -c "import gi; gi.require_version('GdkPixbuf', '2.0')" 2>/dev/null || missing+=(python3-gi gir1.2-gdkpixbuf-2.0)
command -v playerctl >/dev/null || missing+=(playerctl)
command -v parec >/dev/null || missing+=(pulseaudio-utils)
command -v wpctl >/dev/null || missing+=(wireplumber)
fc-list 'Open Sans' | grep -q . || missing+=(fonts-open-sans)
if (( ${#missing[@]} )); then
    echo 'Instale as dependências e rode de novo:' >&2
    echo "  sudo apt install ${missing[*]}" >&2
    exit 1
fi

if ! cosmic-settings appearance import --help >/dev/null 2>&1; then
    echo 'Este COSMIC é antigo demais: atualize o sistema (faltou "appearance import").' >&2
    exit 1
fi

install -d "$SHARE_DIR/thinkpad_widgets" "$(dirname -- "$THEME_BACKUP")"
install -m644 "$PROJECT_DIR"/thinkpad_widgets/*.py "$SHARE_DIR/thinkpad_widgets/"
install -Dm644 "$PROJECT_DIR/systemd/thinkpad-widgets.service" "$UNIT_DIR/thinkpad-widgets.service"
if [[ ! -e "$CONFIG_DIR/widgets.conf" ]]; then
    install -Dm644 "$PROJECT_DIR/config/widgets.conf" "$CONFIG_DIR/widgets.conf"
fi

if [[ ! -e "$THEME_BACKUP" ]]; then
    cosmic-settings appearance export "$THEME_BACKUP"
fi
python3 "$PROJECT_DIR/setup/desktop.py" apply
cosmic-settings appearance import "$PROJECT_DIR/config/crepusculo.ron"

systemctl --user daemon-reload
systemctl --user enable thinkpad-widgets.service
systemctl --user restart thinkpad-widgets.service

# Acesso remoto (opcional): o comando é instalado sempre; avisa o que faltar para usar.
install -Dm755 "$PROJECT_DIR/rdp/rdp-pd" "$BIN_DIR/rdp-pd"
rdp_missing=()
flatpak info com.freerdp.FreeRDP >/dev/null 2>&1 || rdp_missing+=('flatpak install flathub com.freerdp.FreeRDP')
rdp_packages=()
command -v openvpn >/dev/null || rdp_packages+=(openvpn)
command -v socat >/dev/null || rdp_packages+=(socat)
command -v xprop >/dev/null || rdp_packages+=(x11-utils)
(( ${#rdp_packages[@]} )) && rdp_missing+=("sudo apt install ${rdp_packages[*]}")
if (( ${#rdp_missing[@]} )); then
    echo 'Para usar o rdp-pd, falta instalar:'
    printf '  %s\n' "${rdp_missing[@]}"
fi
if [[ ! -e "$RDP_CONFIG" ]]; then
    echo "Para usar o rdp-pd, copie rdp/rdp-pd.conf.example para $RDP_CONFIG e preencha."
fi

echo 'Pronto. Para desfazer tudo: ./uninstall.sh'
