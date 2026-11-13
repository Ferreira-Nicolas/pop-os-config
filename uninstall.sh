#!/usr/bin/env bash
# Remove os widgets e volta o desktop ao estado de antes da instalação.

set -euo pipefail

readonly PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/thinkpad-widgets"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/thinkpad-monitor"
readonly UNIT_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/thinkpad-widgets.service"
readonly THEME_BACKUP="$CONFIG_DIR/theme-backup/antes-do-crepusculo.ron"

systemctl --user disable --now thinkpad-widgets.service 2>/dev/null || true
rm -f "$UNIT_FILE"
systemctl --user daemon-reload

python3 "$PROJECT_DIR/setup/desktop.py" restore
if [[ -e "$THEME_BACKUP" ]]; then
    cosmic-settings appearance import "$THEME_BACKUP"
fi

rm -rf "$SHARE_DIR"
rm -f "$HOME/.local/bin/rdp-pd"
echo "Desinstalado. Suas cores e backups continuam em $CONFIG_DIR."
echo "A configuração do rdp-pd continua em ${XDG_CONFIG_HOME:-$HOME/.config}/rdp-pd."
