"""Ortam değişkenlerinden okunan ayarlar ve dosya yolları."""
import hashlib
import os

import security

# Boş ya da örnek değerse data/ altında rastgele üretilip kalıcı olarak saklanır
SECRET_KEY = security.load_or_create_secret("SECRET_KEY", "data/.secret_key")
# Gateway'in dışarıdan erişilen adresi (OAuth yönlendirmeleri ve metadata için)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:7435").rstrip("/")
# Giriş doğrulaması yapılacak Cockpit (varsayılan: gateway'in çalıştığı host makine)
COCKPIT_AUTH_URL = os.getenv("COCKPIT_AUTH_URL", "https://host.docker.internal:9090").rstrip("/")
COCKPIT_AUTH_VERIFY_TLS = os.getenv("COCKPIT_AUTH_VERIFY_TLS", "").lower() in ("1", "true", "yes")
# Gateway'e girebilecek Cockpit kullanıcıları (virgülle ayrılmış). Boşsa gateway başlamaz.
ALLOWED_USERS = {u.strip() for u in os.getenv("ALLOWED_USERS", "").split(",") if u.strip()}

# Cockpit girişine ek olarak istenen erişim token'ı (ikinci faktör). Boşsa rastgele üretilir.
# MCP istemcisinin bağlandığı URL'de ?token=... olarak verilirse giriş sayfasında ayrıca sorulmaz.
MCP_API_KEY = security.load_or_create_secret("MCP_API_KEY", "data/.mcp_api_key")
# Reverse proxy adresleri (virgülle, CIDR olabilir). Yalnızca bunların X-Forwarded-* başlığına güvenilir.
TRUSTED_PROXIES = os.getenv("TRUSTED_PROXIES", "127.0.0.1").strip()
# true ise değişiklik yapan araçlar MCP istemcilerine kapalıdır; confirm alanını model doldurduğundan
# insan onayı yerine geçmez, yazma yetkisi burada kesilir
READ_ONLY = os.getenv("MCP_READ_ONLY", "").lower() in ("1", "true", "yes")

# --- Panel oturumu ---
SESSION_TTL = 8 * 3600
# Anahtar MCP_API_KEY'e de bağlı: token değişince tüm panel çerezleri geçersiz olur
SESSION_KEY = hashlib.sha256(f"session\0{SECRET_KEY}\0{MCP_API_KEY}".encode()).hexdigest()

SERVERS_FILE = "data/servers.json"
OAUTH_STORE_FILE = "data/oauth.json"

# --- Giden bağlantılar için proxy ---
# Sunuculara (Cockpit ve SSH) bu proxy üzerinden bağlanılır; sunucular gateway yerine proxy'nin
# IP adresini görür. Sunucu bazında farklı bir proxy ya da "direct" (proxysiz) seçilebilir.
OUTBOUND_PROXY = os.getenv("OUTBOUND_PROXY", "").strip()

# --- SSH Giriş Ayarları ---
# Denenecek kullanıcılar ve key klasörleri, sırasıyla: "kullanici:/ssh/klasoru,..."
SSH_LOGINS = os.getenv("SSH_LOGINS", "root:/root/.ssh")

# --- Ortak SSH anahtarları (web panelinden eklenir, tüm sunucularda denenir) ---
SSH_KEYS_FILE = "data/ssh_keys.json"
