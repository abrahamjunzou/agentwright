"""Service 8: Fernet Cryptography Vault (Layer 0).

A single Fernet-encrypted JSON blob on disk holding credential references ->
secret values. The master key is loaded from the ``AGENT_RUNTIME_MASTER_KEY``
environment variable (preferred) or a ``chmod 600`` key file (fallback). The
decrypted JSON exists only in memory; the disk file is always ciphertext.

This resolves ``tool_connection`` credential_refs and ``permission`` secrets.
Vault blob and master key are stored separately and neither is useful alone
(Operational Commitment 6).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet

from .paths import runtime_root

_ENV_KEY = "AGENT_RUNTIME_MASTER_KEY"


class VaultError(RuntimeError):
    """Raised when the vault cannot be opened (e.g. no master key)."""


def generate_master_key() -> str:
    """Return a fresh base64 Fernet key (for first-time setup)."""
    return Fernet.generate_key().decode()


class Vault:
    """In-process encrypted secret store backed by a single Fernet blob file."""

    def __init__(
        self,
        vault_path: str | Path | None = None,
        master_key: str | bytes | None = None,
        key_file: str | Path | None = None,
    ) -> None:
        """Open the vault, resolving the master key by priority:
        explicit ``master_key`` arg -> env var -> key file. Raises VaultError if
        none is available."""
        base = runtime_root() / "secrets"
        self._path = Path(vault_path) if vault_path is not None else base / "vault.bin"
        self._fernet = Fernet(self._resolve_key(master_key, key_file))
        # In-memory plaintext mapping; loaded once, re-encrypted on every write.
        self._data: dict[str, str] = self._load()

    def _resolve_key(self, master_key, key_file) -> bytes:
        if master_key is not None:
            return master_key.encode() if isinstance(master_key, str) else master_key
        env = os.environ.get(_ENV_KEY)
        if env:
            return env.encode()
        path = Path(key_file) if key_file is not None else runtime_root() / "secrets" / "master.key"
        if path.exists():
            return path.read_text().strip().encode()
        raise VaultError(
            f"no master key: set {_ENV_KEY}, pass master_key, or create {path}"
        )

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._fernet.decrypt(self._path.read_bytes()).decode())

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(self._data).encode())
        self._path.write_bytes(token)
        # Restrict the blob to the owner (defense in depth; key is the real guard).
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    def get(self, ref: str) -> str:
        """Return the secret for ``ref``. Raises KeyError if absent."""
        return self._data[ref]

    def set(self, ref: str, value: str) -> None:
        """Store/overwrite a secret and re-encrypt the blob."""
        self._data[ref] = value
        self._flush()

    def exists(self, ref: str) -> bool:
        return ref in self._data

    def delete(self, ref: str) -> None:
        """Remove a secret if present and re-encrypt."""
        if ref in self._data:
            del self._data[ref]
            self._flush()

    def rotate_key(self, new_key: str | bytes) -> None:
        """Re-encrypt all entries under a new master key."""
        self._fernet = Fernet(new_key.encode() if isinstance(new_key, str) else new_key)
        self._flush()
