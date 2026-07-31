#!/bin/bash
# Autostart launcher: wait for the webcam, then run the fencing station.
for i in $(seq 1 30); do
    [ -e /dev/video0 ] && break
    sleep 1
done
cd /home/ben/skewered
# The debug logger now runs from boot via skewered-debug-logger.service, so the
# cold-start window is captured -- launching it here missed it, because of the
# webcam wait above. Two instances would both receive the broadcast and
# interleave lines into the same file, so only start it as a fallback when the
# unit is not installed.
if ! systemctl is-active --quiet skewered-debug-logger; then
    nohup python3 debug_logger.py >/dev/null 2>&1 &
fi
exec python3 station.py >> station.log 2>&1
