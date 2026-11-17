"""Superadmin management from the server shell.

    docker compose exec app python cli.py create-superadmin you@example.com
    docker compose exec app python cli.py reset-2fa you@example.com
    docker compose exec app python cli.py reset-password you@example.com
"""

import getpass
import sys

import qrcode

from db import SessionLocal, engine
from models import Base, SuperAdmin
from security import hash_password, new_totp_secret, totp_uri
from services import audit


def ask_password():
    while True:
        pw = getpass.getpass("Password (min 12 characters): ")
        if len(pw) < 12:
            print("Too short.")
            continue
        if pw != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            continue
        return pw


def show_totp(secret, email):
    uri = totp_uri(secret, email)
    print("\nScan this QR code with an authenticator app (Google Authenticator, Aegis, 1Password):\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)
    print(f"\nOr enter the secret manually: {secret}\n")


def main(argv):
    if len(argv) != 3 or argv[1] not in ("create-superadmin", "reset-2fa", "reset-password"):
        print(__doc__)
        return 2
    command, email = argv[1], argv[2].strip().lower()
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        admin = db.query(SuperAdmin).filter_by(email=email).first()
        if command == "create-superadmin":
            if admin:
                print("Superadmin already exists.")
                return 1
            secret = new_totp_secret()
            db.add(SuperAdmin(email=email, password_hash=hash_password(ask_password()), totp_secret=secret))
            audit(db, "cli", "superadmin.created", email)
            db.commit()
            show_totp(secret, email)
            print("Superadmin created.")
            return 0
        if not admin:
            print("Superadmin not found.")
            return 1
        if command == "reset-2fa":
            admin.totp_secret = new_totp_secret()
            admin.last_totp_step = 0
            audit(db, "cli", "superadmin.2fa_reset", email)
            db.commit()
            show_totp(admin.totp_secret, email)
        else:
            admin.password_hash = hash_password(ask_password())
            admin.failed_attempts = 0
            admin.locked_until = None
            audit(db, "cli", "superadmin.password_reset", email)
            db.commit()
            print("Password changed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
