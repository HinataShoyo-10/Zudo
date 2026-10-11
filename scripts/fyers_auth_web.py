from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import streamlit as st
from dotenv import dotenv_values, load_dotenv
from fyers_apiv3 import fyersModel

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

st.set_page_config(page_title="Zudo · FYERS Connect", page_icon="🔐", layout="centered")
load_dotenv(ENV_PATH, override=False)

st.title("Zudo")
st.subheader("Connect FYERS · data-only")
st.write("Sign in on the official FYERS page. Zudo never asks for your FYERS password, PIN, OTP, or authenticator code.")

st.warning(
    "Local development helper only. Run bound to 127.0.0.1, do not expose this app publicly, "
    "and never commit .env. This app only requests an access token; it does not place orders."
)

env = dotenv_values(ENV_PATH)
app_id = (env.get("FYERS_APP_ID") or "").strip()
secret_id = (env.get("FYERS_SECRET_ID") or "").strip()
redirect_uri = (env.get("FYERS_REDIRECT_URI") or "").strip()

if not all([app_id, secret_id, redirect_uri]):
    st.error("Missing settings. Add FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI to the repository-root .env file.")
    st.code("FYERS_APP_ID=your_app_id\nFYERS_SECRET_ID=your_app_secret\nFYERS_REDIRECT_URI=https://127.0.0.1/")
    st.stop()

st.caption(f"App ID configured: {'yes' if app_id else 'no'} · Redirect URI configured: {'yes' if redirect_uri else 'no'}")
st.caption("The redirect URI must exactly match the one registered in your FYERS dashboard.")

if "fyers_state" not in st.session_state:
    st.session_state.fyers_state = secrets.token_urlsafe(24)
if "fyers_auth_url" not in st.session_state:
    st.session_state.fyers_auth_url = None

if st.button("1 · Generate secure FYERS login link", type="primary", use_container_width=True):
    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_id,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=st.session_state.fyers_state,
    )
    st.session_state.fyers_auth_url = session.generate_authcode()

if st.session_state.fyers_auth_url:
    st.link_button("Open official FYERS sign-in", st.session_state.fyers_auth_url, use_container_width=True)
    st.info(
        "Complete sign-in and any FYERS verification on the FYERS website. "
        "After the redirect, copy the entire URL from the browser address bar. "
        "If the browser shows a connection error at 127.0.0.1, the URL may still contain the callback parameters."
    )
    callback_url = st.text_input(
        "2 · Paste the full redirected URL here",
        type="password",
        help="Keep this private. The URL contains a temporary authorization code. It is processed locally and is not intentionally logged or displayed.",
        placeholder="https://127.0.0.1/?auth_code=…&state=…",
    )

    if st.button("3 · Exchange code and save access token", use_container_width=True):
        try:
            parsed = urlparse(callback_url.strip())
            query = parse_qs(parsed.query)
            auth_code = query.get("auth_code", [""])[0]
            returned_state = query.get("state", [""])[0]

            if not auth_code or not returned_state:
                st.error("That does not look like the full FYERS callback URL. It must include auth_code and state.")
            elif not secrets.compare_digest(returned_state, st.session_state.fyers_state):
                st.error("State verification failed. Generate a new login link and try again.")
            else:
                session = fyersModel.SessionModel(
                    client_id=app_id,
                    secret_key=secret_id,
                    redirect_uri=redirect_uri,
                    response_type="code",
                    grant_type="authorization_code",
                    state=st.session_state.fyers_state,
                )
                session.set_token(auth_code)
                result = session.generate_token()

                if not isinstance(result, dict) or result.get("s") == "error" or not result.get("access_token"):
                    st.error("FYERS did not return an access token. Check app settings and try a fresh login.")
                else:
                    token = result["access_token"]
                    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
                    replacement = "FYERS_ACCESS_TOKEN=" + token
                    found = False
                    new_lines = []
                    for line in lines:
                        if line.startswith("FYERS_ACCESS_TOKEN="):
                            new_lines.append(replacement)
                            found = True
                        else:
                            new_lines.append(line)
                    if not found:
                        new_lines.append(replacement)

                    fd, temp_name = tempfile.mkstemp(prefix=".env.", dir=str(ROOT), text=True)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as file:
                            file.write("\n".join(new_lines) + "\n")
                        os.chmod(temp_name, 0o600)
                        os.replace(temp_name, ENV_PATH)
                    finally:
                        if os.path.exists(temp_name):
                            os.unlink(temp_name)

                    st.session_state.fyers_auth_url = None
                    st.session_state.fyers_state = secrets.token_urlsafe(24)
                    st.success("Access token saved locally to the ignored .env file. Its value was not displayed.")
                    st.caption("Access tokens expire; repeat this login when FYERS requires a fresh session.")
        except Exception:
            st.error("Authentication failed. No credential values are shown here. Verify the redirect URI, app credentials and SDK installation, then retry.")
