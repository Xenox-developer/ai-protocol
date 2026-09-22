"""Create local demonstration credentials without printing their values."""

import os
from pathlib import Path
import secrets


TOKENS = (
    "AGENT_TOKEN_1",
    "AGENT_TOKEN_2",
    "PRODUCT_ONLY_TOKEN",
    "INTERACTIVE_TOKEN",
    "OTHER_AGENT_TOKEN",
    "ADMIN_TOKEN",
)


def main():
    destination = Path(__file__).resolve().parent.parent / ".env.demo"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env.demo already exists; credentials were not changed")
    with os.fdopen(descriptor, "w") as output:
        for name in TOKENS:
            output.write(f"export {name}={secrets.token_urlsafe(32)}\n")
    print("Created .env.demo with mode 0600. Load it with: source .env.demo")


if __name__ == "__main__":
    main()
