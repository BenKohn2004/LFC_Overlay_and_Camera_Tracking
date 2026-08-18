#!/bin/bash
# Set up the Samba share that finished bout videos are written to.
#
# The station no longer uploads anything itself: it cuts a bout, names it after
# the fencers, and drops it in ~/skewered/exports. This makes that folder
# visible to Windows as \\<pi-ip>\exports, so the videos are fetched by hand
# from a machine that is already logged in to wherever they are going.
#
# Run once:  bash setup_share.sh
set -e

SHARE_DIR="$HOME/skewered/exports"
SHARE_NAME="exports"
USER_NAME="${SUDO_USER:-$USER}"

if [ "$EUID" -eq 0 ]; then
    echo "Run this as your normal user, not root -- it uses sudo where needed."
    exit 1
fi

mkdir -p "$SHARE_DIR"

echo "== installing samba =="
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y samba samba-common-bin

echo "== writing share definition =="
# Only the exports folder is shared. The recordings, the database and the rest
# of the home directory stay out of reach: an accidental drag in Explorer
# should not be able to delete a session's footage.
if ! grep -q "^\[$SHARE_NAME\]" /etc/samba/smb.conf; then
    sudo tee -a /etc/samba/smb.conf >/dev/null <<EOF

[$SHARE_NAME]
   comment = Skewered bout exports
   path = $SHARE_DIR
   browseable = yes
   read only = no
   guest ok = no
   create mask = 0664
   directory mask = 0775
   force user = $USER_NAME
EOF
else
    echo "   (already present, leaving it alone)"
fi

echo
echo "== set a password for the share =="
# Windows 10 refuses guest SMB connections by default, so a passwordless share
# fails with a misleading error. This password is local to Samba -- it is not
# the Pi login and has nothing to do with any online account.
echo "This is the password Windows will ask for (user: $USER_NAME)."
sudo smbpasswd -a "$USER_NAME"
sudo smbpasswd -e "$USER_NAME"

sudo systemctl restart smbd
sudo systemctl enable smbd

IP=$(hostname -I | awk '{print $1}')
echo
echo "Done. From Windows Explorer:"
echo "    \\\\${IP:-<pi-ip>}\\$SHARE_NAME"
echo "Log in as '$USER_NAME' with the password you just set, and tick"
echo "'Remember my credentials' so it stops asking."
echo
echo "The station shows this address on its Wi-Fi screen, since the IP"
echo "changes when it joins a different network."
