#!/usr/bin/env python3
"""
send.py — emails the digest through Brevo.

Reads digest.html (produced by build_digest.py) and sends it as the body of an
email. Uses only Python's built-in libraries, so there is nothing extra to
install and nothing that can break on a dependency update.

Environment variables it expects (GitHub Actions supplies these):

  BREVO_API_KEY   (secret)   your Brevo API key
  DIGEST_FROM     (in digest.yml)  the sender address you verified in Brevo
  DIGEST_TO       (in digest.yml)  where the digest should be delivered
  DIGEST_SUBJECT  (optional)  set automatically by build_digest.py
  TEST_MODE       (optional)  "true" sends a short test email instead of the
                              digest, so you can check delivery for free
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.brevo.com/v3/smtp/email"
DIGEST_PATH = "digest.html"

TEST_HTML = """<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:24px">
<h2 style="margin:0 0 12px">Test email</h2>
<p>If you are reading this, delivery works: GitHub Actions reached Brevo and
Brevo reached your inbox.</p>
<p style="color:#666;font-size:13px">No news research ran, so this cost nothing
in API usage. Uncheck the test box to send a real digest.</p>
</body></html>"""


def require(name):
    """Fetch a required environment variable or exit with a clear explanation."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"ERROR: {name} is not set.\n"
            f"  If this is BREVO_API_KEY, add it under:\n"
            f"    Settings -> Secrets and variables -> Actions -> New repository secret\n"
            f"  If it is DIGEST_FROM or DIGEST_TO, set it in .github/workflows/digest.yml"
        )
    return value


def post_to_brevo(api_key, payload):
    """Send one request to Brevo. Returns (status_code, response_body_text)."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        sys.exit(f"ERROR: could not reach Brevo ({exc.reason}).")


def explain(status, body):
    """Turn Brevo's response into advice a human can act on."""
    hints = {
        401: (
            "Brevo rejected the API key.\n"
            "  - Check the BREVO_API_KEY secret is the API key from\n"
            "    Settings -> SMTP & API -> API Keys, NOT the SMTP password.\n"
            "  - Check the key has not expired (Brevo keys can be given an expiry date)."
        ),
        400: (
            "Brevo rejected the request contents.\n"
            "  - The usual cause is DIGEST_FROM not matching a verified sender.\n"
            "  - In Brevo: Settings -> Senders, and confirm the address is verified."
        ),
        403: "Your Brevo account is not permitted to send. Check the account status.",
    }
    message = hints.get(status)
    return f"\n{message}" if message else ""


def main():
    api_key = require("BREVO_API_KEY")
    from_email = require("DIGEST_FROM")
    to_email = require("DIGEST_TO")

    test_mode = os.environ.get("TEST_MODE", "").strip().lower() == "true"

    if test_mode:
        subject = "News Digest — test email"
        html = TEST_HTML
        print("TEST MODE: sending a short test email, not the digest.")
    else:
        if not os.path.exists(DIGEST_PATH):
            sys.exit(f"ERROR: {DIGEST_PATH} not found. Did build_digest.py run successfully?")
        with open(DIGEST_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        if len(html) < 500:
            sys.exit("ERROR: digest.html looks empty or truncated. Not sending.")
        subject = os.environ.get("DIGEST_SUBJECT", "").strip() or "Your Twice-Weekly Digest"

    payload = {
        "sender": {"email": from_email, "name": "News Digest"},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }

    status, body = post_to_brevo(api_key, payload)

    if 200 <= status < 300:
        try:
            message_id = json.loads(body).get("messageId", "(no id)")
        except json.JSONDecodeError:
            message_id = "(no id)"
        print(f"Sent to {to_email}. Brevo status {status}, messageId {message_id}.")
        return

    sys.exit(f"ERROR: Brevo returned status {status}.\n{body}{explain(status, body)}")


if __name__ == "__main__":
    main()
