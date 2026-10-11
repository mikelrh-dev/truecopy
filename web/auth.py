"""Argon2 user store backed by a gitignored `.streamlit-users.json`.

Conventions established here:

* Every operator-facing error is Spanish; the code path is English, because
  the auth layer is infrastructure, not operator dialogue (web-access-auth
  spec: "Spanish UI, English manual content").
* The file is created lazily and written atomically via a tempfile rename so
  a mid-write power loss never turns ``.streamlit-users.json`` into a half-serialized
  object. The file is gitignored by repo rules and MUST never be committed.
* Password verification uses Argon2id through ``argon2-cffi``; plaintext
  passwords never persist, and rejection paths never disclose whether the
  code existed (the error message is the same for unknown code and wrong
  password, which is acceptable for a LAN-only single-operator tool).
* The owner flag is a single boolean per user record. Extending to finer
  roles is explicitly out of scope (web-access-auth spec: "SSO and
  fine-grained roles are out of scope").
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from argon2.low_level import Type

# The store lives next to this module by default; the web app can override it
# so integration tests can point at a disposable path.
DEFAULT_USERS_PATH = Path(__file__).resolve().parent / "users.json"

# Argon2id parameters chosen for a LAN-only tool on modest hardware: strong
# enough to be non-trivial to crack from a stolen file, cheap enough that a
# login attempt does not become annoying.
_PH = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    type=Type.ID,
)


def _normalize_users(raw: object) -> dict[str, dict[str, object]]:
    """Validate and normalize the users file shape, raising on corruption."""
    if not isinstance(raw, dict):
        raise ValueError("users.json must contain a JSON object")
    out: dict[str, dict[str, object]] = {}
    for code, record in raw.items():
        if not isinstance(code, str) or not code.strip():
            raise ValueError("users.json contains an empty or non-string code")
        if not isinstance(record, dict):
            raise ValueError(f"user record for {code!r} must be a JSON object")
        if not isinstance(record.get("password_hash"), str) or not record["password_hash"].strip():
            raise ValueError(f"user record for {code!r} is missing a password_hash")
        if not isinstance(record.get("owner"), bool):
            raise ValueError(f"user record for {code!r} must set owner to true or false")
        out[code.strip()] = {
            "password_hash": record["password_hash"].strip(),
            "owner": bool(record["owner"]),
        }
    return out


def _serialize_users(users: dict[str, dict[str, object]]) -> str:
    return json.dumps(users, indent=2, ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class User:
    """A resolved operator identity after a successful login."""

    code: str
    owner: bool


class AuthStore:
    """Read/write Argon2-backed operator store for the LAN web UI.

    The store is bound to one file path for its lifetime. Two stores must not
    share a path concurrently; the web app keeps one store per session lifecycle.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_USERS_PATH
        self._users = self._read()
        # Rebuild the hasher lazily on first use so import time stays quiet.
        self._ph: PasswordHasher | None = None

    @property
    def _hasher(self) -> PasswordHasher:
        if self._ph is None:
            self._ph = _PH
        return self._ph

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def authenticate(self, code: str, password: str) -> User:
        """Return the logged-in ``User`` if the code and password match.

        Raises:
            ValueError: on a missing code, blank password, or corrupted store.
            WrongPasswordError: on a valid-shaped but incorrect password/code.
        """
        code = code.strip()
        if not code:
            raise ValueError("El código no puede estar vacío")
        # Validate emptiness on the trimmed form, but verify the EXACT string
        # ``add_user`` hashed: surrounding spaces are part of the secret. The
        # previous asymmetry (hash raw, verify stripped) made any account whose
        # password had leading/trailing whitespace impossible to log into.
        if not password.strip():
            raise ValueError("La contraseña no puede estar vacía")

        record = self._users.get(code)
        if record is None:
            raise WrongPasswordError("Código o contraseña incorrectos")

        try:
            self._hasher.verify(record["password_hash"], password)
        except VerificationError:
            raise WrongPasswordError("Código o contraseña incorrectos")
        except VerifyMismatchError:
            raise WrongPasswordError("Código o contraseña incorrectos")

        return User(code=code, owner=record["owner"])

    def owner(self, code: str) -> User | None:
        """Return the owner user for ``code``, or ``None`` if it is not the owner."""
        if not code.strip():
            return None
        record = self._users.get(code)
        if record is None:
            return None
        if not record["owner"]:
            return None
        return User(code=code, owner=True)

    def has_user(self, code: str) -> bool:
        """True when ``code`` is registered in the store."""
        return code.strip() in self._users

    def list_codes(self) -> list[str]:
        """Registered operator codes, sorted for stable UI rendering."""
        return sorted(self._users.keys())

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def add_user(self, code: str, password: str, owner: bool = False) -> User:
        """Register a new operator. Fails if the code already exists."""
        code = code.strip()
        if not code:
            raise ValueError("El código no puede estar vacío")
        if not password:
            raise ValueError("La contraseña no puede estar vacía")
        if code in self._users:
            raise ValueError(f"Ya existe un operador con el código {code!r}")

        self._users[code] = {
            "password_hash": self._hasher.hash(password),
            "owner": bool(owner),
        }
        self._write()
        return User(code=code, owner=bool(owner))

    def remove_user(self, code: str) -> None:
        """Remove an operator. Fails if the code does not exist."""
        code = code.strip()
        if not code:
            raise ValueError("El código no puede estar vacío")
        if code not in self._users:
            raise ValueError(f"No existe ningún operador con el código {code!r}")
        del self._users[code]
        self._write()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("users.json está corrupto") from exc
        return _normalize_users(raw)

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".users.json.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(_serialize_users(self._users))
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, str(self._path))
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


class WrongPasswordError(Exception):
    """Invalid code or password — Spanish error for the UI gate."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def create_default_users(
    path: Path | None = None, *, password: str | None = None
) -> AuthStore:
    """Create a store with one owner account on first boot if none exist.

    This is an operator setup helper, not authentication itself. The owner
    password is RANDOM per install (``secrets.token_urlsafe``) and printed
    ONCE to the server console, so no fixed credential ships in the source and
    nothing predictable can be guessed before the owner rotates it. ``password``
    is injectable for tests.
    """
    store = AuthStore(path)
    if store.list_codes():
        return store

    owner_code = "admin"
    owner_password = password or secrets.token_urlsafe(16)
    store.add_user(owner_code, owner_password, owner=True)
    print(
        f"TrueCopy: owner creado — código '{owner_code}', contraseña: "
        f"{owner_password}",
        file=sys.stderr,
    )
    print(
        "Cámbiela en la UI (Owner > Códigos) antes de operar.", file=sys.stderr
    )
    return store
