#!/usr/bin/env python3
"""
send.py — emails the digest through SendGrid.

Reads digest.html (produced by build_digest.py) and sends it as the body of an
email. Everything it needs comes from environment variables, which GitHub
Actions supplies from your repository secrets:

  SENDGRID_API_KEY  (secret)  your SendGrid key, starts with "SG."
  DIGEST_FROM       (variable) the address you verified in SendGrid
  DIGEST_TO         (variable) where the digest should be delivered
  DIGEST_SUBJECT    (optional) set automatically by build_digest.py
"""

import os
import sys

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

DIGEST_PATH = "digest.html"


def require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"ERROR: {name} is not set.\n"
            f"Add it under Settings -> Secrets and variables -> Actions in your repo."
        )
    return value


def main():
    api_key = require("SENDGRID_API_KEY")
    from_email = require("DIGEST_FROM")
    to_email = require("DIGEST_TO")
    subject = os.environ.get("DIGEST_SUBJECT", "").strip() or "Your Twice-Weekly Digest"

    if not os.path.exists(DIGEST_PATH):
        sys.exit(f"ERROR: {DIGEST_PATH} not found. Did build_digest.py run successfully?")

    with open(DIGEST_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    if len(html) < 500:
        sys.exit("ERROR: digest.html looks empty or truncated. Not sending.")

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )

    try:
        response = SendGridAPIClient(api_key).send(message)
    except Exception as exc:  # noqa: BLE001
        # SendGrid puts the useful detail in exc.body when it's an API error.
        detail = getattr(exc, "body", None)
        sys.exit(f"ERROR sending via SendGrid: {exc}\n{detail or ''}")

    if 200 <= response.status_code < 300:
        print(f"Sent to {to_email} (SendGrid status {response.status_code}).")
    else:
        sys.exit(f"ERROR: SendGrid returned status {response.status_code}: {response.body}")


if __name__ == "__main__":
    main()
