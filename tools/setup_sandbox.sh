#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" != 0 ]]; then
    echo 'Run this explicit host setup as root after installing bubblewrap and libseccomp2.' >&2
    exit 1
fi

command -v bwrap >/dev/null
command -v prlimit >/dev/null

# Ubuntu restricts unprivileged user namespaces even for Bubblewrap unless its binary has a profile.
# Grant only the root-owned distribution executable, never disable the global AppArmor restriction.
restriction=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [[ -f "$restriction" && "$(cat "$restriction")" == 1 ]]; then
    command -v apparmor_parser >/dev/null
    binary=$(command -v bwrap)
    if [[ "$binary" != /usr/bin/bwrap || "$(stat -c %u "$binary")" != 0 || -L "$binary" ]]; then
        echo 'The AppArmor exception requires the root-owned distribution /usr/bin/bwrap.' >&2
        exit 1
    fi
    profile=/etc/apparmor.d/michiflin-bwrap
    if [[ -e "$profile" || -L "$profile" ]]; then
        echo 'Refusing to overwrite an existing AppArmor profile; review it manually.' >&2
        exit 1
    fi
    temporary=$(mktemp /etc/apparmor.d/.michiflin-bwrap.XXXXXX)
    trap 'rm -f "$temporary"' EXIT
    cat > "$temporary" <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile michiflin-bwrap /usr/bin/bwrap flags=(unconfined) {
    userns,
}
PROFILE
    chmod 0644 "$temporary"
    apparmor_parser -r "$temporary"
    mv -T "$temporary" "$profile"
    trap - EXIT
fi

echo 'Host namespace policy prepared; the real sandbox probe must still pass.'
