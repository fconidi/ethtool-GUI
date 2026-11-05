Fixture provenance: file, real|synthetic, source
settings-r8169-linkdown.txt: real: desktop Realtek NIC (r8169), link down
driver-r8169.txt: real: desktop Realtek NIC (r8169)
eee-r8169.txt: real: desktop Realtek NIC (r8169)
module-unsupported-netlink.txt: real: desktop Realtek NIC, module query unsupported
settings-veth.txt: real: virtual interface (veth), link up
eee-unsupported.txt: real: virtual interface, EEE unsupported
settings-bridge.txt: real: Linux bridge, link up
settings-e1000e.txt: real: server Intel NIC (e1000e), link up 1000 Mb/s full
driver-e1000e.txt: real: server Intel NIC (e1000e)
nodevice.txt: real: nonexistent interface name
unsupported.txt: synthetic: operations not supported by the driver
stats-e1000e.txt: synthetic: ethtool -S, e1000e style
pause-e1000e.txt: synthetic: ethtool -a, e1000e style
ring-e1000e.txt: synthetic: ethtool -g, e1000e style
channels-e1000e.txt: synthetic: ethtool -l, e1000e style
coalesce-e1000e.txt: synthetic: ethtool -c, e1000e style
features-e1000e.txt: synthetic: ethtool -k, e1000e style
module-sfp.txt: synthetic: ethtool -m, SFP with diagnostics
cable-test.txt: synthetic: ethtool --cable-test, one open pair
rss-e1000e.txt: synthetic: ethtool -x, e1000e style
udevadm-net-setup-link-ifnames0.stdout.txt: real: udevadm test-builtin net_setup_link, stdout, systemd 257, net.ifnames=0
udevadm-net-setup-link-ifnames0.stderr.txt: real: udevadm test-builtin net_setup_link, stderr, systemd 257, net.ifnames=0, repeated warnings trimmed
default-link.txt: real: /usr/lib/systemd/network/99-default.link, systemd 257, comments removed
