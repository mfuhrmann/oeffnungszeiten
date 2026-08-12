#!/usr/bin/env bash
# Point this shell at a changedetection instance. Source it, do not execute it:
#
#     source scripts/cd_env.sh            # laptop -> VPS through the tunnel (default)
#     source scripts/cd_env.sh local      # whatever already listens on localhost:5000
#
# Exports CD_BASE_URL and CHANGEDETECTION_API_KEY, which every script here reads, so the
# usual calls need no flags:
#
#     python3 scripts/entries_sync.py
#     python3 scripts/watch_audit.py
#
# The laptop path is three moving parts that all die on their own — an SSH tunnel to the API
# server, a port-forward to the pod, and the key. Each is created only if missing, so sourcing
# this again after something died repairs just that piece.
#
# The port-forward is the fragile one: it pins a pod, not the service, so every pod recreation
# kills it — and reconcileStrategy: Revision recreates the pod on every commit. A dead forward
# accepts the connection and then resets, which looks like a broken app rather than a broken
# tunnel. Sourcing this again fixes it.

_cd_env_main() {
    local mode="${1:-vps}"
    local ssh_host="${CD_SSH_HOST:-horst@31.70.86.241}"
    local kubeconfig="${CD_KUBECONFIG:-$HOME/.kube/vps.yaml}"
    local api_port=16443 ui_port=5001 ns=changedetection

    if [ "$mode" = "local" ]; then
        export CD_BASE_URL="http://localhost:5000"
        echo "CD_BASE_URL=$CD_BASE_URL (nothing started, expecting something on :5000)"
        return 0
    fi

    command -v kubectl >/dev/null || { echo "kubectl not found" >&2; return 1; }

    # 1. SSH tunnel to the API server. -f -N: background, no shell.
    if ! ss -ltn "sport = :$api_port" 2>/dev/null | grep -q LISTEN; then
        echo "starting API tunnel on :$api_port"
        ssh -o BatchMode=yes -f -N -L "$api_port:127.0.0.1:6443" "$ssh_host" || {
            echo "tunnel failed — is the ssh key loaded?" >&2; return 1; }
        sleep 2
    fi
    export KUBECONFIG="$kubeconfig"

    # 2. Port-forward to the UI. Verify an existing one actually answers: a forward whose pod
    #    is gone still holds the port but resets every connection.
    if ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:$ui_port/"; then
        # Bracket the first letter so the pattern cannot match the shell running it.
        pkill -f "[p]ort-forward.*$ui_port:5000" 2>/dev/null
        echo "starting port-forward on :$ui_port"
        kubectl -n "$ns" port-forward "svc/$ns" "$ui_port:5000" >/tmp/cd-portforward.log 2>&1 &
        sleep 4
        curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:$ui_port/" || {
            echo "port-forward not answering, see /tmp/cd-portforward.log" >&2; return 1; }
    fi
    export CD_BASE_URL="http://127.0.0.1:$ui_port"

    # 3. The key lives in the datastore, not in a secret we can read from here.
    if [ -z "${CHANGEDETECTION_API_KEY:-}" ]; then
        CHANGEDETECTION_API_KEY=$(kubectl -n "$ns" exec "deploy/$ns" -c "$ns" -- python3 -c \
            "import json;print(json.load(open('/datastore/changedetection.json'))['settings']['application']['api_access_token'])" \
            2>/dev/null | tr -d '\r')
        [ -n "$CHANGEDETECTION_API_KEY" ] || { echo "could not read the API key" >&2; return 1; }
        export CHANGEDETECTION_API_KEY
    fi

    echo "CD_BASE_URL=$CD_BASE_URL  key=${#CHANGEDETECTION_API_KEY} chars  KUBECONFIG=$KUBECONFIG"
}

_cd_env_main "$@"
unset -f _cd_env_main
