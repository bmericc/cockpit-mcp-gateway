import os
import stat

import pytest

import settings
import storage
import security


def test_load_servers_missing_file_returns_empty(servers_file):
    assert storage.load_servers() == {}


def test_save_and_load_roundtrip(servers_file, sample_server):
    storage.save_servers(sample_server)
    assert storage.load_servers() == sample_server


def test_corrupt_servers_file_is_an_error_not_empty(servers_file):
    # Boş sayılsaydı sonraki kayıt tüm sunucuları silerdi
    servers_file({})
    with open(settings.SERVERS_FILE, "w") as f:
        f.write("{bozuk json")
    with pytest.raises(storage.StorageError):
        storage.load_servers()
    with open(settings.SERVERS_FILE) as f:
        assert f.read() == "{bozuk json"


def test_servers_file_is_private_and_write_leaves_no_temp_file(servers_file, sample_server):
    storage.save_servers(sample_server)
    assert stat.S_IMODE(os.stat(settings.SERVERS_FILE).st_mode) == 0o600
    assert os.listdir(os.path.dirname(settings.SERVERS_FILE)) == ["servers.json"]


def test_failed_write_keeps_previous_content(servers_file, sample_server, monkeypatch):
    storage.save_servers(sample_server)
    monkeypatch.setattr(security.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        storage.save_servers({})
    assert storage.load_servers() == sample_server
