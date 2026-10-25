#!/usr/bin/env bash
# Install or update the Automation desk as an app, apart from this repo: one version from GitHub in a Python environment
# in the app folder, and the settings file on the first install. An update replaces only the environment.
# Usage: ./install.sh <version>                                    for example ./install.sh v0.1.0
#        AUTOMATION_DESK_HOME=/other/app/folder ./install.sh <version>
set -euo pipefail

VERSION=${1:?Give the version to install, for example: ./install.sh v0.1.0}
APP=${AUTOMATION_DESK_HOME:-$HOME/.local/share/md_apps/automation_desk}
REPO=marcdumon/automation_desk

for tool in uv npm curl; do
    command -v "$tool" >/dev/null || { echo "Missing '$tool': install it first." >&2; exit 1; }
done

mkdir -p "$APP"
[[ -d $APP/env ]] || uv venv --quiet --python 3.13 "$APP/env"
echo "Installing automation_desk $VERSION in $APP/env ..."
uv pip install --quiet --no-sources --python "$APP/env/bin/python" --reinstall-package automation-desk "git+https://github.com/$REPO@$VERSION"

if [[ ! -f $APP/automation_desk.toml ]]; then
    curl -sfL "https://raw.githubusercontent.com/$REPO/$VERSION/automation_desk.example.toml" -o "$APP/automation_desk.toml"
    echo "Made $APP/automation_desk.toml from the example: set your own values in it."
fi
echo "Installed. Start the app with: $APP/env/bin/automation-desk"
