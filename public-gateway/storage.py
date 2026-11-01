"""Kalıcı kayıtlar: sunucular, ortak SSH anahtarları ve gizli alanların şifrelenmesi."""
import base64
import hashlib
import json
import os
import time
from typing import Dict, Any

import asyncssh
from cryptography.fernet import Fernet, InvalidToken

import security
import settings
from i18n import t

class StorageError(Exception):
    """Kayıt dosyası okunamıyor. Boş kabul edilirse sonraki kayıt tüm içeriği silerdi."""

def _load_json(path: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise StorageError(f"could not read {path}: {e}") from e
    if not isinstance(data, dict):
        raise StorageError(f"could not read {path}: unexpected content")
    return data

def load_servers() -> Dict[str, Dict[str, Any]]:
    return _load_json(settings.SERVERS_FILE)

def save_servers(servers: Dict[str, Dict[str, Any]]):
    security.write_private_json(settings.SERVERS_FILE, servers, indent=4)

# --- Gizli bilgiler ---
# Cockpit şifreleri servers.json'da SECRET_KEY'den türetilen anahtarla şifreli tutulur
SECRET_FIELDS = ("cockpit_password", "proxy")

# Eski sürümlerin SECRET_KEY boşken ve örnek dosyayla kullandığı değerler; yalnızca taşıma için
LEGACY_SECRET_KEYS = ("gizli-anahtar", "a_very_secure_and_random_string")

def _fernet(key: str | None = None) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256((key or settings.SECRET_KEY).encode()).digest()))

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_secret(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None

def _reencrypt_legacy(value: str) -> str:
    """Eski bilinen anahtarla şifrelenmiş değeri geçerli anahtarla yeniden şifreler."""
    if decrypt_secret(value) is not None:
        return value
    for key in LEGACY_SECRET_KEYS:
        try:
            return encrypt_secret(_fernet(key).decrypt(value.encode()).decode())
        except (InvalidToken, ValueError):
            continue
    return value

def migrate_legacy_encryption() -> None:
    """SECRET_KEY varsayılan/örnek değerden rastgele değere geçince kayıtlı şifreler kaybolmasın."""
    servers = load_servers()
    changed = False
    for cfg in servers.values():
        for field in SECRET_FIELDS:
            if cfg.get(field) and (new := _reencrypt_legacy(cfg[field])) != cfg[field]:
                cfg[field], changed = new, True
    if changed:
        save_servers(servers)
    keys = load_shared_keys()
    changed = False
    for entry in keys.values():
        if entry.get("private") and (new := _reencrypt_legacy(entry["private"])) != entry["private"]:
            entry["private"], changed = new, True
    if changed:
        save_shared_keys(keys)
MAX_KEY_SIZE = 16 * 1024

def load_shared_keys() -> Dict[str, Dict[str, Any]]:
    return _load_json(settings.SSH_KEYS_FILE)

def save_shared_keys(keys: Dict[str, Dict[str, Any]]):
    security.write_private_json(settings.SSH_KEYS_FILE, keys, indent=4)

def shared_key_entry(name: str, key: "asyncssh.SSHKey") -> Dict[str, Any]:
    """Özel anahtar şifrelenmiş (ve parolasız hâliyle) saklanır; açık anahtar gösterim içindir."""
    return {
        "name": name,
        "type": key.get_algorithm(),
        "fingerprint": key.get_fingerprint(),
        "public": key.export_public_key().decode().strip(),
        "private": encrypt_secret(key.export_private_key().decode()),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def import_shared_key(name: str, private_text: str, passphrase: str = "") -> Dict[str, Any]:
    if len(private_text) > MAX_KEY_SIZE:
        raise ValueError(t("The key is too large."))
    try:
        key = asyncssh.import_private_key(private_text.strip() + "\n", passphrase or None)
    except asyncssh.KeyEncryptionError:
        raise ValueError(t("Wrong key passphrase."))
    except asyncssh.KeyImportError as e:
        if "Passphrase" in str(e):
            raise ValueError(t("This key is passphrase-protected; enter the key passphrase too."))
        raise ValueError(t("Not a valid SSH private key (paste the private key, not the public key)."))
    return shared_key_entry(name, key)
