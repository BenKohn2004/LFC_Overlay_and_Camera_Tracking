#!/bin/bash
# Autostart launcher: wait for the webcam, then run the fencing station.
for i in $(seq 1 30); do
    [ -e /dev/video0 ] && break
    sleep 1
done
cd /home/ben/skewered
nohup python3 debug_logger.py >/dev/null 2>&1 &
exec python3 station.py >> station.log 2>&1
