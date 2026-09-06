# Proxmox VE Helper-Scripts

Two scripts for the [community-scripts](https://github.com/community-scripts/ProxmoxVE)
project, which builds an LXC and installs into it.

They are kept here so they version with the app. They are **not** wired into
anything — community-scripts hosts its own copies, and these are the source to
submit and to keep in step.

## Submitting

New scripts **cannot** go to `community-scripts/ProxmoxVE` directly; PRs adding
one there are closed without review. The route is:

1. Fork and clone
   [community-scripts/ProxmoxVED](https://github.com/community-scripts/ProxmoxVED),
   the testing repo.
2. `git switch -c feat/porchlight`
3. Copy `ct/porchlight.sh` and `install/porchlight-install.sh` in.
4. **Test against a real Proxmox host.** Both a fresh install and the update
   path.
5. Open the PR against **ProxmoxVED**.
6. Maintainers promote it to ProxmoxVE once it has been tested.

Coding standards: <https://community-scripts.org/docs/contribution>

## What they do

`install/porchlight-install.sh` installs `git nginx python3 python3-yaml`,
clones the repo to `/opt/porchlight/src`, and runs the project's own
`install.sh` with `PREFIX=/opt/porchlight`.

`ct/porchlight.sh` declares the container defaults — 1 CPU, 512 MB, 4 GB,
Debian 13, unprivileged, arm64 allowed — and an update path that pulls and
re-runs the installer.

**The checkout under `src/` is deliberate.** Porchlight's supported upgrade is
`git pull && ./install.sh`, which replaces the code and leaves `data/` — the
config, icons, backgrounds and backups — untouched. Keeping the clone means the
update path is the same one the project documents, rather than a second
mechanism that can drift from it.
