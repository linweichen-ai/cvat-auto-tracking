#!/usr/bin/env bash
# Remote-side deploy script for a CVAT serverless tracker function.
# Expected to run on the Linux host where Nuclio is installed.
#
# Usage:
#   bash deploy.sh --function-dir /path/to/func --function-name my-tracker
#
# This is a framework/stub: fill in the real nuctl commands once Nuclio is up.

set -euo pipefail

FUNCTION_DIR=""
FUNCTION_NAME=""
NUCLIO_NAMESPACE="nuclio"   # default nuctl namespace for CVAT serverless

while [[ $# -gt 0 ]]; do
    case "$1" in
        --function-dir)   FUNCTION_DIR="$2"; shift 2 ;;
        --function-name)  FUNCTION_NAME="$2"; shift 2 ;;
        --namespace)      NUCLIO_NAMESPACE="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 1 ;;
    esac
done

if [[ -z "$FUNCTION_DIR" || -z "$FUNCTION_NAME" ]]; then
    echo "ERROR: --function-dir and --function-name are required." >&2
    exit 1
fi

echo "[remote-deploy] function dir : $FUNCTION_DIR"
echo "[remote-deploy] function name: $FUNCTION_NAME"
echo "[remote-deploy] namespace    : $NUCLIO_NAMESPACE"

# 0. Preflight
if ! command -v nuctl >/dev/null 2>&1; then
    echo "[remote-deploy] WARN: nuctl not found on PATH. Install Nuclio CLI first." >&2
    # TODO: install nuctl:
    #   wget -P /usr/local/bin https://github.com/nuclio/nuclio/releases/.../nuctl-...-linux-amd64
    #   chmod +x /usr/local/bin/nuctl
fi

if [[ ! -f "$FUNCTION_DIR/function.yaml" ]]; then
    echo "[remote-deploy] ERROR: function.yaml missing under $FUNCTION_DIR" >&2
    exit 1
fi

# 1. Build / deploy via nuctl
#    CVAT serverless functions are normally deployed with:
#      nuctl deploy --project-name cvat --path <dir> --namespace nuclio
#    or by applying function.yaml:
#      nuctl apply -f function.yaml --namespace nuclio
echo "[remote-deploy] Applying function.yaml ..."
nuctl apply -f "$FUNCTION_DIR/function.yaml" --namespace "$NUCLIO_NAMESPACE" || {
    echo "[remote-deploy] nuctl apply failed; falling back to nuctl deploy ..." >&2
    nuctl deploy --project-name cvat --path "$FUNCTION_DIR" --namespace "$NUCLIO_NAMESPACE"
}

# 2. Wait / status
echo "[remote-deploy] Function status:"
nuctl get function "$FUNCTION_NAME" --namespace "$NUCLIO_NAMESPACE" || true

echo "[remote-deploy] Done. Open CVAT UI -> Models to confirm the function is listed."
