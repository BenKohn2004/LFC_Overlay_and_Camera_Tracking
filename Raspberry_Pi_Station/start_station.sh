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
# The debug logger now runs from boot via skewered-debug-logger.service, so the
# cold-start window is captured -- launching it here missed it, because of the
# webcam wait above. Two instances would both receive the broadcast and
# interleave lines into the same file, so only start it as a fallback when the
# unit is not installed.
if ! systemctl is-active --quiet skewered-debug-logger; then
    nohup python3 debug_logger.py >/dev/null 2>&1 &
fi
exec python3 station.py >> station.log 2>&1
