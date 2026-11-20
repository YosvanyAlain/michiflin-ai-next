#!/usr/bin/env bash
set -euo pipefail

# This script configures a local proxy, never a machine-wide VPN.
if [[ "${GITHUB_ACTIONS:-}" != true || -z "${GITHUB_ENV:-}" ]]; then
  echo "WARP setup is restricted to the manually selected Actions probe." >&2
  exit 1
fi
. /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then
  echo "WARP setup requires the reviewed Ubuntu 24.04 runner." >&2
  exit 1
fi
curl --fail --silent --show-error --max-time 30 https://pkg.cloudflareclient.com/pubkey.gpg |
  sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ noble main' |
  sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
sudo apt-get update -qq
sudo apt-get install --yes cloudflare-warp
warp-cli --accept-tos registration new
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port 40000
warp-cli --accept-tos connect
for _ in {1..15}; do
  if curl --fail --silent --max-time 5 --socks5 127.0.0.1:40000 \
      https://www.cloudflare.com/cdn-cgi/trace | grep -qx 'warp=on'; then
    echo 'MICHIFLIN_SOURCE_PROXY=socks5://127.0.0.1:40000' >> "$GITHUB_ENV"
    echo 'WARP proxy verified; only opted-in public source clients will use it.'
    exit 0
  fi
  sleep 2
done
echo 'WARP verification failed; no proxy exported and the probe will not continue.' >&2
exit 1
