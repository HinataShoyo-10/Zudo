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

run_fyers_auth() {
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

  cleanup_auth() {
    if kill -0 "$AUTH_PID" 2>/dev/null; then
      kill "$AUTH_PID" 2>/dev/null || true
      wait "$AUTH_PID" 2>/dev/null || true
    fi
  }
  trap cleanup_auth EXIT INT TERM

  read -r -p "Press Enter after FYERS reports that the access token was saved (Ctrl+C to cancel)..." _

  cleanup_auth
  trap - EXIT INT TERM
}

echo "=== Zudo: checking FYERS token age ==="
TOKEN_CHECK="$("$PYTHON" - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from dotenv import dotenv_values

env_path = Path(".env")
env = dotenv_values(env_path)
token = (env.get("FYERS_ACCESS_TOKEN") or "").strip()
stamp_key = "FYERS_ACCESS_TOKEN_UPDATED_AT"
stamp = (env.get(stamp_key) or "").strip()

if not token:
    print("No FYERS access token is configured.")
    print("MISSING")
    raise SystemExit(0)

if not stamp:
    # One-time migration for existing .env files: this is an estimate because
    # older versions did not record the token's own update time.
    stamp_dt = datetime.fromtimestamp(env_path.stat().st_mtime, tz=timezone.utc)
    stamp = stamp_dt.isoformat(timespec="seconds")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.strip().startswith(stamp_key + "=")]
    lines.append(f"{stamp_key}={stamp}")
    fd, temp_name = tempfile.mkstemp(prefix=".env.", dir=str(env_path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, env_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print("No token timestamp existed; initialized an estimated timestamp from the existing .env file modification time.")

try:
    updated_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    updated_at = updated_at.astimezone(timezone.utc)
    age_hours = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600)
except (TypeError, ValueError):
    print("Stored token timestamp is invalid.")
    print("STALE")
    raise SystemExit(0)

print("Last token update (UTC): " + updated_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
print(f"Token age: {age_hours:.1f} hours")
print("STALE" if age_hours > 12 else "FRESH")
PY
)"
printf "%s\n" "$TOKEN_CHECK"
TOKEN_STATUS="${TOKEN_CHECK##*$'\n'}"

case "$TOKEN_STATUS" in
  MISSING)
    echo "No existing token found; authentication is required."
    run_fyers_auth
    ;;
  STALE)
    echo
    echo "The token timestamp is more than 12 hours old (or invalid)."
    read -r -p "Re-authenticate now? [y/N]: " REAUTH_CHOICE
    case "$REAUTH_CHOICE" in
      y|Y|yes|YES)
        run_fyers_auth
        ;;
      *)
        echo "Continuing with the existing token as requested. FYERS may reject it if it has expired."
        ;;
    esac
    ;;
  FRESH)
    echo "Token is within the 12-hour window; skipping authentication."
    ;;
  *)
    echo "ERROR: Could not determine FYERS token status."
    exit 1
    ;;
esac

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
