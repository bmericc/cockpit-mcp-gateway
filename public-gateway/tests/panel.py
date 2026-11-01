"""Panel testleri için oturum yardımcıları."""
import base64
import json
import re
import time

import itsdangerous

import settings
import web_panel


def login_as(client, username):
    """Sunucu tarafı oturumu ve imzalı çerezi üretir; client.post formlara CSRF token'ını ekler."""
    sid, csrf = f"sid-{username}", f"csrf-{username}"
    web_panel.panel_sessions[sid] = time.time() + settings.SESSION_TTL
    data = base64.b64encode(json.dumps({"user": {"username": username}, "sid": sid, "csrf": csrf}).encode())
    client.cookies.set("session", itsdangerous.TimestampSigner(settings.SESSION_KEY).sign(data).decode())
    post = client.post
    client.post = lambda url, data=None, **kw: post(url, data={"csrf": csrf, **(data or {})}, **kw)
    return client


def csrf_token(client):
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text)[1]
