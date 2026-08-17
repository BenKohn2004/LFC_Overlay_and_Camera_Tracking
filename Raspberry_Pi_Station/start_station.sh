#!/bin/bash
# Autostart launcher: give the webcam a moment to enumerate, then run the
# station. The wait used to be 30 s because station.py died without a camera;
# it now starts regardless and polls for one, so this only avoids a brief
# "NO CAMERA" flash on a normal boot. Don't lengthen it -- with no camera
# attached this is dead time before the review UI appears.
for i in $(seq 1 5); do
    [ -e /dev/video0 ] && break
    sleep 1
done
cd /home/ben/skewered
# The debug logger listened for the ESP8266 transmitter's beacons on UDP 4211.
# The box is read over BLE now (skewered-ble-bridge.service) and there is no
# transmitter left to beacon, so its unit is disabled deliberately -- and the
# fallback that used to start it by hand here would quietly undo that decision
# every boot, logging SILENCE forever.
exec python3 station.py >> station.log 2>&1
