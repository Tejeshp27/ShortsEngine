# =============================================================
# src/dashboard/set_password.py — create your dashboard login
#
# Run this once before using the dashboard, and again any time
# you want to change your password:
#
#   python -m src.dashboard.set_password
#
# It asks for a username and a password (typed twice, hidden as
# you type) and saves the user in the database with the password
# hashed. Changing the password logs you out on every device.
#
# It also makes sure .env has a SESSION_SECRET (the random value
# that signs the login cookie), creating one if it is missing.
#
# The password is typed here, in the terminal, on purpose: it is
# never written in a file, a command, or your shell history.
# =============================================================

import getpass
import os
import secrets

from dotenv import load_dotenv

from src.dashboard.auth import check_password_rules, set_password
from src.storage.database import create_tables


# The .env file in the folder the command is run from.
ENV_FILE_PATH = ".env"

# Must match MINIMUM_SECRET_LENGTH in app.py.
MINIMUM_SECRET_LENGTH = 32


def ensure_session_secret(env_file_path=ENV_FILE_PATH):
    """
    Make sure a SESSION_SECRET exists, adding one to .env if needed.

    Args:
        env_file_path (str): Path to the .env file.

    Returns:
        str: What happened — "exists" (nothing to do), "added" (a new
             secret was written to .env) or "missing" (there is no .env
             file to write to, so the secret must be set by hand).

    Example:
        ensure_session_secret()   # → "added"
    """
    if len(os.getenv("SESSION_SECRET", "")) >= MINIMUM_SECRET_LENGTH:
        return "exists"

    if not os.path.exists(env_file_path):
        return "missing"

    # token_urlsafe gives random letters, digits, "-" and "_" only, so
    # the value needs no quoting in .env.
    new_secret = secrets.token_urlsafe(48)
    with open(env_file_path, "a", encoding="utf-8") as f:
        f.write("\n# Signs the dashboard login cookie. Changing it logs everyone out.\n")
        f.write(f"SESSION_SECRET={new_secret}\n")
    return "added"


def main():
    """
    Ask for a username and password, then save the dashboard login.

    Example:
        python -m src.dashboard.set_password
    """
    load_dotenv()
    create_tables()

    print("Set the ShortsEngine dashboard login")
    print("-" * 40)

    username = input("Username: ").strip()
    if not username:
        print("ERROR: Username cannot be empty.")
        raise SystemExit(1)

    # getpass hides what you type, like a normal password prompt.
    password = getpass.getpass("Password (at least 12 characters): ")
    problem = check_password_rules(password)
    if problem:
        print(f"ERROR: {problem}")
        raise SystemExit(1)

    repeated = getpass.getpass("Type the password again: ")
    if password != repeated:
        print("ERROR: The two passwords did not match. Nothing was changed.")
        raise SystemExit(1)

    set_password(username, password)
    print(f"Saved. You can now log in as '{username}'.")
    print("If you were logged in anywhere, those sessions have been signed out.")

    secret_result = ensure_session_secret()
    if secret_result == "added":
        print("Added a new SESSION_SECRET to .env.")
    elif secret_result == "missing":
        print("NOTE: SESSION_SECRET is not set. Create one with:")
        print('  python -c "import secrets; print(secrets.token_urlsafe(48))"')
        print("and add it to .env as SESSION_SECRET=<that value>.")


if __name__ == "__main__":
    main()
