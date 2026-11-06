# Changelog

## 0.1.6

- Refreshed the current Advanced page after a change is reverted, automatically or manually.
- Hid the Apply buttons on pages that the driver does not support and showed the reason instead.
- Translated the "not reported by the driver" message and tidied field labels.
- Removed duplicated and empty lines from the Statistics, RSS and SFP pages.
- Removed the empty Wake-on-LAN entry shown when capabilities cannot be read.
- Disabled the Advanced link controls when no interface is selected.

## 0.1.5

- The application name is now spelled Ethtool-GUI in the applications menu, in the authorization prompts and in messages.

## 0.1.4

- Refreshed Advanced link controls when returning from Simple.
- Ignored older Advanced reads when a newer reading is available.

## 0.1.3

- Fixed hidden Keep, Keep and save, and Revert buttons in link-change confirmation.

## 0.1.2

- Disabled manual speed and duplex controls while auto-negotiation is active.
- Refreshed link status when switching views and after applying Advanced changes.
- Kept pending link-change confirmation visible until kept or reverted.

## 0.1.1

- Fixed Advanced pages staying empty after switching tabs.
- Localized Advanced controls and messages in Italian, French, German and Spanish.
- Added authorized reading of Wake-on-LAN capabilities when ordinary reads omit them.
- Ignored delayed interface readings after the window closes.

## 0.1.0

- Added Simple and Advanced views for Ethernet status, settings and diagnostics.
- Added guarded link changes with a timed restore prompt.
- Added cable and self-tests, optical module readings, live counter graphs,
  snapshots and boot-setting controls.
