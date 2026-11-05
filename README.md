# ethtool-GUI

ethtool-GUI is a GTK desktop interface for inspecting and configuring Ethernet
interfaces on Debian and SysLinuxOS. It uses the system `ethtool` program and
opens privileged operations through polkit.

The **Simple** view shows the link, negotiated speed and duplex, error rates,
diagnostic suggestions and common controls. **Advanced** provides link modes,
driver information, live counters, pause, ring, channels, coalescing, offload,
EEE, Wake-on-LAN, RSS, optical module readings, hardware tests, raw dumps,
snapshots and boot settings. Unsupported driver capabilities remain visible
with a reason.

## Install

Install the Debian package with `sudo apt install
./ethtool-gui_<version>_all.deb`.

Requirements: Debian 13 or a compatible system, Python 3.11 or newer, GTK 3,
`ethtool`, polkit and systemd. NetworkManager is recommended for boot settings
on interfaces it manages.

The interface is available in English, Italian, French, German and Spanish.

## Use

Launch **ethtool-GUI** from *SysLinuxOS Tools* or run `ethtool-gui`. Select an
interface on the left and choose Simple or Advanced. The application reads
interface status as your normal user. A password is requested only when an
operation needs administrator privileges.

Changes to link settings show a 15-second **Keep** prompt. If you do not keep
the change, the previous settings are restored. **Keep and save for boot**
persists a confirmed change where NetworkManager or systemd supports it.
Advanced mode also provides explicit boot-setting controls. Cable and offline
self-tests can interrupt the link and require a separate confirmation.

Some controls depend on the network driver and hardware. Optical readings
require a compatible SFP or QSFP module. Snapshots are stored in your user
data directory and can be compared or exported as text.

## License

GPL-3.0. See [LICENSE](LICENSE).
