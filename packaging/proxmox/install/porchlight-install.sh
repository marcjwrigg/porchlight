#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# Author: marcjwrigg
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/marcjwrigg/porchlight

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Dependencies"
$STD apt install -y git nginx python3 python3-yaml
msg_ok "Installed Dependencies"

msg_info "Installing Porchlight"
# Kept as a checkout under src/ so update_script can `git pull` and re-run the
# installer, which is the project's own supported upgrade path.
$STD git clone --depth 1 https://github.com/marcjwrigg/porchlight /opt/porchlight/src
cd /opt/porchlight/src
PREFIX=/opt/porchlight $STD bash install.sh
msg_ok "Installed Porchlight"

motd_ssh
customize

msg_info "Cleaning up"
$STD apt -y autoremove
$STD apt -y autoclean
msg_ok "Cleaned"
