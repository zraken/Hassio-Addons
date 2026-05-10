#!/bin/sh

set -e

# Read options from Home Assistant
OPTIONS_FILE=/data/options.json
if [ -f "${OPTIONS_FILE}" ]; then
    TOKEN="$(grep -o '"token": *"[^"]*"' "${OPTIONS_FILE}" | grep -o '"[^"]*"$' | tr -d '"')"
    EDGE_MODE="$(grep -o '"edge_mode": *\(true\|false\)' "${OPTIONS_FILE}" | grep -o 'true\|false')"
    DOCKHAND_SERVER_URL="$(grep -o '"dockhand_server_url": *"[^"]*"' "${OPTIONS_FILE}" | grep -o '"[^"]*"$' | tr -d '"')"
    ENABLE_TLS="$(grep -o '"enable_tls": *\(true\|false\)' "${OPTIONS_FILE}" | grep -o 'true\|false')"
    CERTDIR="$(grep -o '"certdir": *"[^"]*"' "${OPTIONS_FILE}" | grep -o '"[^"]*"$' | tr -d '"')"
fi

# Check that the token isn't empty
if [ -z "${TOKEN}" ]; then
    echo "ERROR: No token configured. Set a token in the add-on configuration."
    exit 1
fi

# Check that the token has been changed from the default placeholder
if [ "${TOKEN}" = "your-secret-token" ]; then
    echo "ERROR: Token has not been changed from the default. Set a unique secret token in the add-on configuration before starting."
    exit 1
fi

# Check that the docker socket is accessible from within the App
if [ ! -S "/run/docker.sock" ]; then
    echo "ERROR: Docker socket not found at /run/docker.sock."
    echo "ERROR: To fix this, go to the Hawser app (formerly add-on) page in Home Assistant, scroll down to 'Protection mode' and disable it, then restart the add-on."
    exit 1
fi

# Store stacks in HA shared storage so they persist across restarts
mkdir -p /share/hawser/stacks
ln -sf /share/hawser/stacks /data/stacks

# Common config
export TOKEN="${TOKEN}"
export DOCKER_SOCKET="/run/docker.sock"

# TLS configuration
if [ "${ENABLE_TLS}" = "true" ]; then
    CERTDIR="${CERTDIR:-/ssl}"
    TLS_CERT="${CERTDIR}/fullchain.pem"
    TLS_KEY="${CERTDIR}/privkey.pem"

    if [ ! -f "${TLS_CERT}" ]; then
        echo "ERROR: TLS enabled but certificate not found at ${TLS_CERT}"
        exit 1
    fi
    if [ ! -f "${TLS_KEY}" ]; then
        echo "ERROR: TLS enabled but private key not found at ${TLS_KEY}"
        exit 1
    fi

    export TLS_CERT="${TLS_CERT}"
    export TLS_KEY="${TLS_KEY}"
    echo "INFO: TLS enabled, using certs from ${CERTDIR}"
fi

if [ "${EDGE_MODE}" = "true" ]; then
    # Edge Mode: agent connects outbound to Dockhand via WebSocket
    if [ -z "${DOCKHAND_SERVER_URL}" ]; then
        echo "ERROR: Edge mode enabled but no Dockhand server URL configured. Set the Dockhand server URL in the add-on configuration."
        exit 1
    fi

    export DOCKHAND_SERVER_URL="${DOCKHAND_SERVER_URL}"

    echo "INFO: Starting Hawser in Edge mode, connecting to ${DOCKHAND_SERVER_URL}"
    exec /usr/local/bin/hawser
else
    # Standard Mode: agent listens for incoming connections
    export PORT="2376"
    export BIND_ADDRESS="0.0.0.0"

    echo "INFO: Starting Hawser in Standard mode on port ${PORT}"
    exec /usr/local/bin/hawser
fi
