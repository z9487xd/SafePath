#!/bin/bash
# 讓一般使用者可以燒錄 ESP32（CH340 / CP210x / FTDI / ESP32 原生 USB）
set -e
cat > /etc/udev/rules.d/99-esp32-serial.rules <<'RULES'
SUBSYSTEMS=="usb", ATTRS{idVendor}=="1a86", MODE:="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE:="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0403", MODE:="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="303a", MODE:="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
RULES
udevadm control --reload-rules
udevadm trigger
usermod -aG dialout "${SUDO_USER:-$USER}"
echo "OK：udev 規則已安裝，${SUDO_USER:-$USER} 已加入 dialout。拔插一次 USB 即可。"
