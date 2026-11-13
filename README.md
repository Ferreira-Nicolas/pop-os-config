# Pop OS Config

Visual completo para o COSMIC (Pop!_OS), com as cores do wallpaper: roxo das nuvens,
vermelho do banco do trem e vidro ameixa.

- **Widgets de vidro** no fundo da tela, atrás das janelas e sem receber cliques:
  relógio, bateria, volume, brilho, rede, CPU, GPU, temperatura, memória, disco e processos.
- **Painel superior** com música ou vídeo tocando, capa do álbum, barras que acompanham
  o som e um pop-up ao passar o mouse, com álbum, tempo, barra clicável, aleatório, repetir,
  pausar e passar música, e o volume só do player, sem mexer no resto do som (arrastar,
  clicar no alto-falante para silenciar ou usar a rodinha do mouse). Ao lado: CPU,
  memória, temperatura e rede. Sem os ícones de teclado e acessibilidade, e com a
  porcentagem da bateria.
- **Tema do sistema** para janelas, menus e lançador, além de painel e Dock de vidro.
- **Wallpaper** e **cores do VS Code**.
- **Acesso remoto** a um Windows em todas as telas, com o comando `rdp-pd`.

## Instalar

Requer o COSMIC atualizado, com sessão gráfica aberta:

```bash
git clone https://github.com/Ferreira-Nicolas/pop-os-config.git
cd pop-os-config
./install.sh
```

Não precisa de sudo. Se faltar alguma dependência, ele para e mostra o `apt install`
exato. Os widgets aparecem na hora e passam a abrir a cada login.

Para atualizar depois de uma mudança, rode `git pull && ./install.sh`.

## Desfazer

```bash
./uninstall.sh
```

Volta tema, painel, Dock, wallpaper e VS Code ao estado anterior à instalação. Os
originais ficam em `~/.config/thinkpad-monitor/`, e rodar o instalador de novo não os
sobrescreve. Para só desligar os widgets, sem mexer no resto:

```bash
systemctl --user disable --now thinkpad-widgets.service
```

## Personalizar

As cores ficam em `~/.config/thinkpad-monitor/widgets.conf`, uma por linha, e mudam em
até 5 segundos. Apague uma linha para ela seguir o acento da Aparência.

A transparência das janelas fica em Configurações > Aparência, e a do painel em
`~/.config/cosmic/com.system76.CosmicPanel.Panel/v1/opacity`.

Para mudar a frequência de atualização ou a tela principal, acrescente `--interval 5`
ou `--primary eDP-1` ao `ExecStart` de `systemd/thinkpad-widgets.service` e rode o
instalador de novo. A lista de cartões de cada coluna está em
[`thinkpad_widgets/app.py`](thinkpad_widgets/app.py).

## Em outro computador

- Uma tela externa recebe o painel completo, e a do notebook, um resumo. Com uma tela
  só, ela recebe o painel completo.
- A coluna direita tem cerca de 1000 px de altura: em telas menores que 1080p ela é
  cortada. Remova cartões em `right_column`, no `app.py`.
- O uso da GPU vem do kernel em Intel e AMD, e do `nvidia-smi` em NVIDIA. Sem nenhuma
  dessas leituras, o cartão não aparece. Em Intel, o número é o tempo que ela passou
  acordada: pode ficar acima do trabalho real, porque desenhar a tela já a mantém ativa.
- A temperatura lê processadores Intel e AMD. Placas de vídeo dedicadas não aparecem.
- Sem bateria, o cartão de bateria não aparece.
- O alerta de temperatura usa 85 °C. Para mudar, crie
  `~/.config/thinkpad-monitor/temperature.conf` com `TEMPERATURE_LIMIT_C=75`.
- Se o `settings.json` do VS Code tiver comentários, o instalador não mexe nele. Cole
  as cores de `config/vscode-colors.json` manualmente.

## Acesso remoto (RDP em todas as telas)

O comando `rdp-pd` liga a VPN, se precisar, e abre um Windows remoto com uma janela por
monitor. Ele usa o FreeRDP em Flatpak, porque o Remmina não faz multimonitor no COSMIC.

```bash
flatpak install flathub com.freerdp.FreeRDP
sudo apt install openvpn socat x11-utils
mkdir -p ~/.config/rdp-pd
cp rdp/rdp-pd.conf.example ~/.config/rdp-pd/rdp-pd.conf
chmod 600 ~/.config/rdp-pd/rdp-pd.conf
```

Preencha servidor, usuário e VPN no `rdp-pd.conf` e copie os arquivos da VPN para a
pasta indicada nele. Nada disso entra no repositório: os dados de conexão e os arquivos
da VPN ficam só no seu computador. A senha é pedida a cada conexão.

Dentro da sessão: **Ctrl+Super+Q** minimiza, **Ctrl+Super+D** desconecta e
**Ctrl+Super+Enter** entra e sai da tela cheia. O Alt+Tab vai para o Windows enquanto
a sessão está em foco; o Super+Tab continua no COSMIC. Se o PC desligar no meio da
sessão e o Alt+Tab não voltar, rode `rdp-pd --restaurar`.

## Problemas comuns

**Os widgets não aparecem.** Veja o motivo com:

```bash
journalctl --user -u thinkpad-widgets.service -n 30
```

**A música aparece, mas não sai som.** Os widgets só leem o áudio e não mudam a saída.
Confira em Configurações > Som para onde o áudio vai. Saídas HDMI só tocam se o monitor
tiver alto-falantes; sem eles, use a saída de fone do monitor ou escolha outra saída.
Em notebooks com placa Intel, as caixas podem nem aparecer na lista: elas ficam em outro
perfil da placa, que se escolhe com `pactl set-card-profile`.

**Algum player não aparece.** Só players compatíveis com MPRIS são listados. Teste com
`playerctl --all-players metadata`.

**A capa não aparece.** Ela depende do player informar uma imagem. Teste com
`playerctl metadata mpris:artUrl`. Apps em Flatpak podem informar um arquivo que só
existe dentro do sandbox deles.

**As barras ficam paradas.** Elas acompanham a saída onde o áudio toca e checam isso a
cada 2 segundos. Se continuarem paradas, confirme que há som saindo em Configurações > Som.

## Como funciona

O conky 1.19 do Pop!_OS não tem blur no Wayland, e seu desenho em Cairo/Lua só funciona
em X11. Por isso os widgets são um programa em Python que fala direto com o compositor:

| Arquivo | Função |
| --- | --- |
| [`wayland.py`](thinkpad_widgets/wayland.py) | Protocolo Wayland: camadas, blur e input vazio |
| [`metrics.py`](thinkpad_widgets/metrics.py) | Leitura de sensores, bateria, rede e processos |
| [`gpu.py`](thinkpad_widgets/gpu.py) | Uso da GPU em Intel, AMD e NVIDIA |
| [`media.py`](thinkpad_widgets/media.py) | Música tocando e análise do som |
| [`cards.py`](thinkpad_widgets/cards.py) | Desenho dos cartões |
| [`bar.py`](thinkpad_widgets/bar.py) | Faixas do painel |
| [`popup.py`](thinkpad_widgets/popup.py) | Pop-up de música |
| [`volume.py`](thinkpad_widgets/volume.py) | Volume do player tocando, no fluxo de áudio dele |
| [`theme.py`](thinkpad_widgets/theme.py) | Cores |
| [`setup/desktop.py`](setup/desktop.py) | Aplica e desfaz as mudanças no desktop |

Consumo medido: 50 MB de RAM e 2% de um núcleo em repouso, e 5% enquanto algo toca. O
áudio só é capturado durante a reprodução, sempre da saída e nunca do microfone. A capa é
baixada do endereço informado pelo player, uma vez por música. Bordas
de alto contraste aparecem suavemente atrás do vidro porque o raio do blur é fixo no
compositor. As faixas do painel não são applets: ficam por cima dele. Só a faixa de música
e o pop-up recebem o mouse; os cartões e a faixa de sistema deixam os cliques atravessarem.

Os nomes `thinkpad-widgets` e `thinkpad-monitor` vêm do projeto original. Eles foram
mantidos para que a instalação existente seja atualizada em vez de duplicada.

Testes: `python3 -m unittest discover tests`
