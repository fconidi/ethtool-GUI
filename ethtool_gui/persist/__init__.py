"""``ethtool-gui-persist``: keeps settings across reboots.

It writes a NetworkManager profile (``nmcli connection modify``) or a systemd
``.link`` file, and undoes exactly what it wrote. It runs as root through
pkexec, one request per run (see ``__main__``).
"""
