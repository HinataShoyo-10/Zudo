# Local FYERS authentication helper (data-only)

This is a local-only Streamlit UI for Zudo. It sends the user to FYERS for authentication and any 2FA, validates the returned state, exchanges the temporary authorization code with the official FYERS v3 SDK, and saves only the access token to the repository-root `.env`.

## Security boundaries

- Never enter FYERS password, PIN, OTP or authenticator code into Zudo.
- Never paste an authorization URL/code/token into chat, an issue, a PR, or a commit.
- Keep `.env` ignored by Git. Do not run this app on a public host or bind it to `0.0.0.0`.
- This helper does not place orders. The app should remain non-trading with order placement permission disabled.
- The app's redirect URI must exactly match the FYERS dashboard. This helper uses the currently registered redirect URI from `FYERS_REDIRECT_URI`.
- The callback is entered as a full URL in a password-masked field so the helper can verify the `state` value. If FYERS redirects to `https://127.0.0.1/` and the browser reports a connection error, check whether the address bar still contains the callback query parameters.
- Access tokens are time-limited. Log in again when FYERS requires a new token; do not depend on refresh-token behavior unless verified against current official documentation.

## Setup in GitHub Codespaces

1. In the repository root, create `.env` from `.env.example`.
2. Set `FYERS_APP_ID`, `FYERS_SECRET_ID`, and `FYERS_REDIRECT_URI` locally. For the screenshot shared during setup, the redirect URI is `https://127.0.0.1/`. Use the exact value from the dashboard.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Confirm the secret file is ignored: `git check-ignore .env`.
5. Run: `streamlit run scripts/fyers_auth_web.py --server.address 127.0.0.1 --server.port 8501`.
6. Open the local forwarded port only for your own Codespaces session. Click Generate login link, sign in on the official FYERS site, complete its verification there, then paste the full redirected URL into the masked field.
7. After success, the access token is saved into `.env`. Verify with `git status --short` that `.env` is not listed.

## FYERS flow

The v3 user-app flow is: generate the auth URL using the registered app ID and redirect URI; authenticate directly on FYERS; receive an `auth_code` and the matching `state` on the registered redirect; exchange the code through the SDK to obtain an access token. The temporary code is not the access token.

Official references:
- https://support.fyers.in/portal/en/kb/articles/how-does-the-authentication-and-login-flow-work-for-user-apps-on-fyers
- https://support.fyers.in/portal/en/kb/articles/what-is-the-purpose-of-a-redirect-uri-in-fyers-system
- https://support.fyers.in/portal/en/kb/articles/how-do-i-activate-the-new-app-for-api-trading-after-april-1-2026

If callback URL matching or HTTPS redirect behavior prevents completion, do not weaken validation or expose credentials. Update the registered redirect URI to a controlled callback URL and configure the same exact URI in `.env`, or use the existing CLI flow.
