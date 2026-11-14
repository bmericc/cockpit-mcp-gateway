# Docker ile kurulum

[English](docker-install.md) · **Türkçe**

[← README'ye dön](../README.tr.md)

Gateway'i çalıştırmanın iki yolu var:

- [A. Hazır imaj](#a-hazır-imaj) (önerilen): yayınlanan imajı çekin, kaynak kod gerekmez.
- [B. Kaynak koddan](#b-kaynak-koddan): imajı deponun bir kopyasından kendiniz derleyin.

## Gereksinimler

- Docker Engine ve Docker Compose eklentisi kurulu bir Linux makine (`docker compose version` çalışmalı).
- İmajlar `linux/amd64`, `linux/arm64` ve `linux/arm/v7` (Raspberry Pi vb.) için var.
- Bir alan adı ve HTTPS'i sonlandıran bir reverse proxy (nginx, Caddy, Traefik vb.). claude.ai gibi MCP istemcileri yalnızca HTTPS adreslere bağlanır.
- Yönetmek istediğiniz her sunucuda Cockpit ve yönetim işlemleri için `sudo` yetkisi olan bir Cockpit kullanıcısı. Bkz. [Sunucuları hazırlama](#sunucuları-hazırlama).
- Gateway'i çalıştıran makinede de (ya da erişebildiği herhangi bir makinede) Cockpit: gateway'e girişler onunla doğrulanır (`COCKPIT_AUTH_URL`).

## A. Hazır imaj

İmajlar iki registry'ye yayınlanır; ikisinde de aynı imaj vardır:

| Registry | İmaj |
| --- | --- |
| GitHub Container Registry | `ghcr.io/bmericc/cockpit-mcp-gateway` |
| Docker Hub | `bmericc/cockpit-mcp-gateway` |

Etiketler: `latest` (en yeni sürüm), `1.0.0`, `1.0`, `1` (sürüme göre) ve `sha-<commit>` (`main`'in her derlemesi).

1. Bir klasör oluşturun:

   ```bash
   mkdir -p /opt/cockpit-mcp-gateway && cd /opt/cockpit-mcp-gateway
   ```

2. Compose dosyasını ve örnek ayarları indirin:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/bmericc/cockpit-mcp-gateway/main/docker-compose.yml
   ```

   ```bash
   curl -fsSL -o .env https://raw.githubusercontent.com/bmericc/cockpit-mcp-gateway/main/sample.env
   ```

3. `.env`'yi düzenleyin (bkz. [Ayarlar](#ayarlar)). En azından `PUBLIC_URL` ve `ALLOWED_USERS` girilmeli:

   ```bash
   nano .env
   ```

4. İmajı çekip başlatın:

   ```bash
   docker compose pull
   ```

   ```bash
   docker compose up -d --no-build
   ```

5. Çalıştığını kontrol edin:

   ```bash
   docker compose ps
   ```

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7435/login
   ```

   `200` görürseniz gateway ayaktadır. Ardından [reverse proxy](#reverse-proxy)'yi kurun.

`latest` yerine belirli bir sürüm kullanmak için `.env`'ye `IMAGE_TAG=1.0.0` ekleyin. GHCR yerine Docker Hub kullanmak için `docker-compose.yml`'deki `image:` satırını `bmericc/cockpit-mcp-gateway:${IMAGE_TAG:-latest}` yapın.

## B. Kaynak koddan

1. Depoyu klonlayın:

   ```bash
   git clone https://github.com/bmericc/cockpit-mcp-gateway.git /opt/cockpit-mcp-gateway && cd /opt/cockpit-mcp-gateway
   ```

2. Ayarları oluşturup düzenleyin:

   ```bash
   cp sample.env .env
   ```

   ```bash
   nano .env
   ```

3. Derleyip başlatın:

   ```bash
   docker compose up -d --build
   ```

İmaj `public-gateway/Dockerfile`'dan derlenir. Docker olmadan çalıştırmak (geliştirme) için bkz. [Geliştirme ve testler](../README.tr.md#geliştirme-ve-testler).

## Ayarlar

Tüm ayarlar `.env`'dedir. Gateway **hiçbir üçüncü taraf API anahtarı gerektirmez**: girişler Cockpit ile doğrulanır, sunuculara Cockpit ya da SSH üzerinden erişilir.

| Değişken | Zorunlu | Ne işe yarar |
| --- | --- | --- |
| `ALLOWED_USERS` | **evet** | Gateway'e girebilecek Cockpit kullanıcıları, virgülle ayrılmış. Boşsa gateway başlamaz. |
| `PUBLIC_URL` | **evet** | Gateway'in erişildiği HTTPS adres, örn. `https://mcp.example.com`. OAuth yönlendirmeleri ve panelde gösterilen adresler bunu kullanır. |
| `COCKPIT_AUTH_URL` | hayır | Girişleri doğrulayan Cockpit. Varsayılan `https://host.docker.internal:9090`: gateway'i çalıştıran makinenin Cockpit'i. |
| `COCKPIT_AUTH_VERIFY_TLS` | hayır | Bu Cockpit'in sertifikasını doğrulamak için `true`. Varsayılan `false` (Cockpit kendinden imzalı sertifika kullanır). |
| `SECRET_KEY` | hayır | Oturumları imzalar, kayıtlı Cockpit parolalarını ve SSH anahtarlarını şifreler. Boşsa bir kez üretilip `data/.secret_key`'de saklanır. **Sonradan asla değiştirmeyin**: kayıtlı parolalar çözülemez hale gelir. |
| `MCP_API_KEY` | hayır | Gateway parolası (ikinci faktör, Cockpit parolasıyla birlikte sorulur). Boşsa bir kez üretilip `data/.mcp_api_key`'de saklanır. Değiştirmek herkesin oturumunu kapatır. |
| `PORT` | hayır | Container içindeki port. Varsayılan `7435`. |
| `TRUSTED_PROXIES` | hayır | `X-Forwarded-*` gönderebilecek adresler (virgülle, CIDR olabilir). Varsayılan `127.0.0.1`. Reverse proxy host'taysa Docker bridge aralığını yazın, örn. `172.16.0.0/12`. |
| `MCP_READ_ONLY` | hayır | `true`: MCP istemcileri yalnızca okuma araçlarını alır. Varsayılan `false`. |
| `SSH_LOGINS` | hayır | SSH için denenecek kullanıcılar ve anahtar klasörleri, örn. `root:/root/.ssh`. |
| `OUTBOUND_PROXY` | hayır | Sunuculara giden bağlantılar için proxy: `socks5://`, `socks5h://` ya da `http://`. |
| `AUDIT_LOG_MAX_MB` | hayır | `data/audit.log`'un döndürüleceği boyut. Varsayılan `10`. |
| `BIND_ADDRESS` | hayır | Gateway değil `docker-compose.yml` okur: portun açıldığı host adresi. Varsayılan `127.0.0.1`. |
| `IMAGE_TAG` | hayır | `docker-compose.yml` okur: imaj etiketi. Varsayılan `latest`. |

Üretilen gateway parolasını görmek için:

```bash
cat data/.mcp_api_key
```

## Reverse proxy

`docker-compose.yml` portu yalnızca `127.0.0.1:7435`'te açar; aynı makinede bir reverse proxy beklenir. MCP uç noktaları uzun süreli akışlar açık tutar; tamponlamayı kapatın ve uzun zaman aşımı verin.

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:7435;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

Caddy:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:7435 {
        flush_interval -1
    }
}
```

Host'taki nginx/Caddy'den gelen istekler container'a Docker bridge ağından ulaşır; bu yüzden `TRUSTED_PROXIES=172.16.0.0/12` (ya da tam bridge alt ağı) yazın. Aksi halde işlem kayıtlarında istemcinin değil bridge'in adresi görünür.

Erişim token'ı MCP adresinde (`?token=`) olabilir. Proxy'niz bunu loglar; proxy log biçiminde `token=`'ı maskeleyin ya da token'ı giriş sayfasında girin.

## Sunucuları hazırlama

Yönetilecek her sunucuda:

```bash
sudo apt install -y cockpit
```

```bash
sudo systemctl enable --now cockpit.socket
```

Fedora/RHEL'de `sudo dnf install -y cockpit` kullanın. Minimal Debian'da `sudo` yoktur: `apt install sudo && usermod -aG sudo KULLANICI`.

Gateway sunucuya SSH ile ulaşabiliyorsa 9090 portunun açık olması gerekmez: Cockpit SSH tüneli üzerinden kullanılır. Aksi halde 9090'ı yalnızca gateway'in adresine açın; bu adres panelin üstünde yazar. Sunucular sonra panelden eklenir: bkz. [Özellik rehberi](features.tr.md#sunucular).

## MCP istemcisini bağlama

- **claude.ai:** *Settings → Connectors → Add custom connector*, adres `https://mcp.example.com/mcp?token=<MCP_API_KEY>`. Açılan sayfada Cockpit hesabınızla giriş yapın.
- **Claude Code:**

  ```bash
  claude mcp add --transport sse cockpit-gateway "https://mcp.example.com/sse?token=<MCP_API_KEY>"
  ```

Ayrıntılar: [Giriş ve MCP istemcisine bağlanma](../README.tr.md#giriş-ve-mcp-istemcisine-bağlanma).

## Güncelleme

Hazır imaj:

```bash
docker compose pull
```

```bash
docker compose up -d --no-build
```

Kaynak koddan:

```bash
git pull
```

```bash
docker compose up -d --build
```

Ayarlar ve veriler `.env` ile `data/`'da kalır. Panel oturumları yeniden başlatmada kapanır; OAuth istemcileri token'larını korur.

## Yedekleme

Gateway'in tuttuğu her şey `data/`'dadır:

| Dosya | İçerik |
| --- | --- |
| `.secret_key` | Şifreleme anahtarı (yalnızca `.env`'de `SECRET_KEY` boşsa) |
| `.mcp_api_key` | Gateway parolası (yalnızca `MCP_API_KEY` boşsa) |
| `servers.json` | Sunucular, şifreli Cockpit parolaları, sabitlenmiş SSH sunucu anahtarları |
| `ssh_keys.json` | Ortak SSH anahtarları, şifreli |
| `oauth.json` | OAuth istemcileri ve hash'lenmiş token'lar |
| `audit.log`, `audit.log.1` | İşlem kayıtları |

`data/` ile `.env`'yi birlikte yedekleyin. Eşleşen `SECRET_KEY` olmadan yedekteki parolalar ve anahtarlar çözülemez. Yedeği gizli tutun.

```bash
tar czf cockpit-mcp-gateway-$(date +%F).tar.gz .env data
```

## Sık karşılaşılan sorunlar

| Belirti | Neden ve çözüm |
| --- | --- |
| Container sürekli yeniden başlıyor, logda `ALLOWED_USERS is empty` | `.env`'de `ALLOWED_USERS`'ı doldurun, sonra `docker compose up -d`. |
| Parola doğru olduğu halde giriş başarısız | Kullanıcı `ALLOWED_USERS`'ta değil, gateway parolası yanlış ya da `COCKPIT_AUTH_URL` container'dan erişilemiyor. `docker compose logs gateway` ile bakın. 10 hatalı denemeden sonra IP adresi 5 dakika engellenir. |
| `host.docker.internal` çözülmüyor | `docker-compose.yml`'deki `extra_hosts` satırını koruyun ya da `COCKPIT_AUTH_URL`'e host'un adresini yazın. |
| claude.ai bağlanamıyor | `PUBLIC_URL` tam HTTPS adres olmalı; proxy `/mcp`, `/sse`, `/messages/`, `/.well-known/`, `/authorize`, `/token` ve `/register`'ı iletmeli. |
| İşlem kayıtlarında her istemci `172.x.x.x` | Docker bridge aralığını `TRUSTED_PROXIES`'e ekleyin. |
| Taşıma ya da geri yüklemeden sonra sunuculara bağlanılamıyor | `SECRET_KEY` (ya da `data/.secret_key`) değişmiş, kayıtlı Cockpit parolaları çözülemiyor. Eskisini geri koyun. |
| SSH yedeği anahtarları okuyamıyor | Anahtarlar container'a bağlanan bir klasörde (varsayılan `/root/.ssh`) olmalı ve `SSH_LOGINS`'te yazmalı. |

Loglar:

```bash
docker compose logs -f gateway
```
