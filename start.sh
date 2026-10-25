#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in your local credentials."
  exit 1
fi

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

if ! "$PYTHON" -c "import dotenv, streamlit, fyers_apiv3" >/dev/null 2>&1; then
  echo "ERROR: Required Python packages are missing."
  echo "Activate your virtual environment and run: python -m pip install -r requirements.txt"
  exit 1
fi

mkdir -p logs

echo
echo "=== Zudo: FYERS authentication ==="
echo "Starting the local FYERS login helper on http://127.0.0.1:8501"
echo "Use the forwarded port in Codespaces if prompted."
echo "Complete the official FYERS sign-in in your browser; never share your password, PIN, OTP, or tokens."
echo "After the page confirms the token was saved, return here and press Enter."
echo

"$PYTHON" -m streamlit run scripts/fyers_auth_web.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  > logs/fyers_auth.log 2>&1 &
AUTH_PID=$!

cleanup() {
  if kill -0 "$AUTH_PID" 2>/dev/null; then
    kill "$AUTH_PID" 2>/dev/null || true
    wait "$AUTH_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

read -r -p "Press Enter after FYERS reports that the access token was saved (Ctrl+C to cancel)..." _

cleanup
trap - EXIT INT TERM

echo
echo "Checking required configuration without displaying secret values..."
if ! "$PYTHON" - <<'PY'
from dotenv import dotenv_values

env = dotenv_values(".env")
app_id = (env.get("FYERS_APP_ID") or "").strip()
access_token = (env.get("FYERS_ACCESS_TOKEN") or "").strip()
telegram_token = (
    (env.get("Telegram_bot_token") or "").strip()
    or (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
)
chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()

missing = []
if not app_id:
    missing.append("FYERS_APP_ID")
if not access_token:
    missing.append("FYERS_ACCESS_TOKEN")
if not telegram_token:
    missing.append("Telegram_bot_token (or TELEGRAM_BOT_TOKEN)")
if not chat_id:
    missing.append("TELEGRAM_CHAT_ID")

if missing:
    print("Missing required .env settings: " + ", ".join(missing))
    raise SystemExit(1)
print("Required FYERS and Telegram settings are present.")
PY
then
  echo "Fix the missing settings in .env and run ./start.sh again."
  exit 1
fi

echo
echo "=== Zudo: Telegram delivery check ==="
if ! "$PYTHON" -m src.telegram_alerts --test; then
  echo "ERROR: Telegram test failed. Fix Telegram configuration/delivery before starting the live feed."
  exit 1
fi

echo
echo "=== Zudo: starting read-only FYERS feed with Telegram alerts ==="
echo "Press Ctrl+C to stop. Zudo does not place orders."
exec "$PYTHON" -m src.main --source fyers --telegram-alerts
