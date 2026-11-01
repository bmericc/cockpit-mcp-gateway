# Cockpit MCP Gateway

[![tests](https://github.com/bmericc/cockpit-mcp-gateway/actions/workflows/tests.yml/badge.svg)](https://github.com/bmericc/cockpit-mcp-gateway/actions/workflows/tests.yml)

[English](README.md) · **Türkçe**

Linux sunucularını, üzerlerindeki [Cockpit](https://cockpit-project.org) aracılığıyla [Model Context Protocol (MCP)](https://modelcontextprotocol.io) istemcilerine (Claude vb.) açan bir gateway. Debian, Ubuntu, Fedora ve RHEL gibi Cockpit çalıştıran her dağıtımla kullanılabilir. Yapay zekâ asistanı sunucuların durumunu okuyabilir, servis/paket/firewall yönetebilir ve komut çalıştırabilir.

![Cockpit MCP Gateway nasıl çalışıyor](docs/images/nasil-calisiyor.png)

Ayrıntılı mimari ve güvenlik açıklaması için: [Teknik Genel Bakış](docs/genel-bakis.md)

- Komutlar sunucudaki **Cockpit** üzerinden çalışır. Cockpit portuna dışarıdan ulaşılamazsa Cockpit'e **SSH tüneli** içinden bağlanılır; o da olmazsa komutlar doğrudan **SSH** ile çalışır.
- Gateway'e giriş (web paneli ve MCP istemcileri) **Cockpit hesabıyla** yapılır. Ayrı bir kullanıcı veritabanı yoktur.
- Web paneli, hata mesajları ve MCP araç açıklamaları varsayılan olarak **İngilizce**dir; tarayıcı Türkçe istiyorsa (`Accept-Language`) **Türkçe** gösterilir.

## Kurulum

```bash
cp sample.env .env      # değerleri düzenleyin
docker compose pull
docker compose up -d --no-build
```

`linux/amd64` ve `linux/arm64` imajları `ghcr.io/bmericc/cockpit-mcp-gateway` ve Docker Hub'da (`bmericc/cockpit-mcp-gateway`) yayınlanır.

Gateway `7435` portunda çalışır. Önüne HTTPS sonlandıran bir reverse proxy (nginx, Caddy vb.) koyun; `X-Forwarded-*` başlıkları yalnızca `TRUSTED_PROXIES` adreslerinden kabul edilir. `docker-compose.yml` portu yalnızca `127.0.0.1` üzerinde açar; değiştirmek için `BIND_ADDRESS` tanımlayın.

Hedef sunucularda Cockpit kurulu ve çalışır olmalı:

```bash
sudo apt install -y cockpit            # Fedora/RHEL: sudo dnf install -y cockpit
sudo systemctl enable --now cockpit.socket
```

Yönetici işlemleri `sudo` gerektirir; minimal Debian kurulumunda `sudo` yoktur. Kurup Cockpit kullanıcısına yetki verin: `apt install sudo && usermod -aG sudo KULLANICI`.

Gateway sunucuya SSH ile bağlanabiliyorsa Cockpit portunu (9090) dışarıya açmak gerekmez; Cockpit'e SSH tüneli içinden ulaşılır (bkz. *Bağlantı sırası*). Doğrudan erişim istenirse port yalnızca gateway'in IP adresine açılmalı:

```bash
sudo ufw allow from GATEWAY_IP to any port 9090 proto tcp
# firewalld: sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="GATEWAY_IP" port port="9090" protocol="tcp" accept' && sudo firewall-cmd --reload
```

### Ortam değişkenleri (`.env`)

| Değişken | Açıklama |
| --- | --- |
| `SECRET_KEY` | Oturum çerezlerini imzalar, Cockpit şifrelerini şifreler. Boş ya da örnek değerse bir kez rastgele üretilip `data/.secret_key` dosyasında saklanır. **Değiştirilirse kayıtlı Cockpit şifreleri çözülemez.** |
| `PORT` | Dinlenecek port (varsayılan `7435`) |
| `PUBLIC_URL` | Gateway'in dışarıdan erişilen adresi, örn. `https://mcp.ornek.com`. OAuth yönlendirmeleri bunu kullanır. |
| `COCKPIT_AUTH_URL` | Girişleri doğrulayacak Cockpit. Varsayılan `https://host.docker.internal:9090` (gateway'in çalıştığı host makine). |
| `COCKPIT_AUTH_VERIFY_TLS` | Bu Cockpit'in sertifikası doğrulansın mı (varsayılan `false`) |
| `ALLOWED_USERS` | Gateway'e girebilecek Cockpit kullanıcıları (virgülle). **Zorunludur: boşsa gateway başlamaz.** |
| `MCP_API_KEY` | Cockpit girişine ek olarak istenen **erişim token'ı** (ikinci faktör). Boş ya da örnek değerse bir kez rastgele üretilip `data/.mcp_api_key` dosyasında saklanır. Değiştirilirse tüm panel ve OAuth oturumları kapanır. |
| `OUTBOUND_PROXY` | Opsiyonel. Sunuculara giden bağlantılar için varsayılan proxy (`socks5://`, `socks5h://`, `http://`). Bkz. *Bağlantı proxy'si*. |
| `SSH_LOGINS` | SSH bağlantılarında (tünel ve yedek) denenecek kullanıcılar ve key klasörleri. Varsayılan: `root:/root/.ssh` |
| `TRUSTED_PROXIES` | `X-Forwarded-*` gönderebilecek reverse proxy adresleri (virgülle, CIDR olabilir). Varsayılan `127.0.0.1`. Proxy host üzerindeyse ve compose ağı kullanılıyorsa bridge aralığını verin, örn. `172.16.0.0/12`. `*` kullanmayın: istemciler audit kaydındaki adresi sahteleyebilir. |
| `MCP_READ_ONLY` | `true` ise değişiklik yapan tüm araçlar kapanır (varsayılan `false`). `confirm` alanını model doldurur, insan onayı değildir; bu ayar öyledir. |
| `AUDIT_LOG_MAX_MB` | İşlem kayıt dosyasının en fazla boyutu (varsayılan `10`). Bkz. *İşlem kayıtları*. |

`docker-compose.yml`, host makineye `host.docker.internal` adıyla erişim sağlar. Ayrıca `/root/.ssh` klasörünü salt okunur bağlar. Başka bir kullanıcının anahtarları için klasörü `docker-compose.override.yml` dosyasına ve `SSH_LOGINS`'e ekleyin. Dosya Linux yetkilerini de kısar ve kapsayıcı dosya sistemini salt okunur yapar.

## Giriş ve MCP istemcisine bağlanma

MCP uç noktası: `https://<PUBLIC_URL>/mcp` (Streamable HTTP). MCP spec'inde deprecated olan HTTP+SSE taşıması eski istemciler için `https://<PUBLIC_URL>/sse` adresinde durur.

Giriş iki parçalıdır: **Cockpit kullanıcı adı/şifresi** ve **erişim token'ı** (`MCP_API_KEY`). Token tek başına giriş sağlamaz. Böylece zayıf bir Cockpit şifresi tek başına yetmez.

| Yöntem | Kullanım |
| --- | --- |
| **OAuth 2.1** (önerilen) | İstemci bağlanınca tarayıcıda giriş sayfası açılır: kullanıcı adı, Cockpit parolası ve gateway parolası (`MCP_API_KEY`). Giriş yapılınca istemci token alır (1 saat; yenileme girişten 7 gün sonra biter, sonra istemci Cockpit ile yeniden giriş yapar). |
| **HTTP Basic** | `Authorization: Basic base64(kullanıcı:şifre)` + erişim token'ı (`X-MCP-Token: <token>` başlığı veya `?token=<token>`) |

Gateway parolası MCP adresine eklenirse (`https://<PUBLIC_URL>/mcp?token=<MCP_API_KEY>`) giriş sayfası onu ayrıca sormaz; yalnızca kullanıcı adı ve Cockpit parolası istenir. Adreste yoksa veya yanlışsa sayfada ikinci parola alanı çıkar. Web panelinde de aynısı geçerlidir (`/login?token=<token>`).

**claude.ai:** *Settings → Connectors → Add custom connector* bölümüne `https://<PUBLIC_URL>/mcp?token=<token>` adresini girin. Bağlanırken açılan sayfada Cockpit hesabınızla giriş yapın.

**Claude Code:**

```bash
claude mcp add --transport sse cockpit-gateway "https://<PUBLIC_URL>/sse?token=<token>"
```

OAuth akışı uç noktaları:
- `/.well-known/oauth-protected-resource/sse`
- `/.well-known/oauth-authorization-server`
- `/register` (dinamik istemci kaydı)
- `/authorize` (PKCE)
- `/token`
- `/revoke`

Token'lar `data/oauth.json` dosyasında hash'lenmiş olarak saklanır.

## Sunucu ekleme

Web panelinde (`https://<PUBLIC_URL>/`) Cockpit hesabınızla giriş yapın. Ardından *Sunucu Ekle / Güncelle* formunu doldurun:

| Alan | Açıklama |
| --- | --- |
| Sunucu adı, Host | Zorunlu |
| Cockpit kullanıcısı / şifresi | Komutların çalışacağı Cockpit hesabı. Yönetici işlemleri için bu kullanıcının sudo yetkisi olmalı. |
| Cockpit adresi | Boşsa `https://HOST:9090` |
| TLS sertifikasını doğrula | Kendinden imzalı sertifikada kapalı bırakın |
| SSH kullanıcısı / portu / key yolu | Cockpit tüneli ve SSH yedeği için |
| Bağlantı proxy'si | Opsiyonel, bkz. *Bağlantı proxy'si* |

Sunucular `data/servers.json` dosyasında tutulur ve bu dosya git'e alınmaz. Cockpit şifreleri dosyada şifrelidir; `list_servers` aracı şifreleri göstermez.

**Bağlantı kontrolü:** Kaydet'e basınca sunucuya gerçekten bağlanılır ve *Bağlantı sırası*ndaki yollar denenir. Cockpit doğrudan ya da SSH tüneliyle çalışıyorsa ✓, yalnızca düz SSH çalışıyorsa sunucu kaydedilir ama ⚠ ile uyarı (ve Cockpit'in neden çalışmadığı) gösterilir. Hiç bağlantı kurulamazsa kayıt yapılmaz ve hata gösterilir. Sunucu o an kapalıysa *Bağlantıyı test etmeden kaydet* seçeneği kullanılabilir. Sunucu listesindeki *Test et* düğmesi bağlantıyı istendiği zaman yeniden dener ve son durumu tabloda gösterir.

**Gateway'in dış IP adresi:** Panelin üstünde gateway'in internete çıktığı IP adresi gösterilir. Bu, uzaktaki sunucuların güvenlik duvarında Cockpit (9090/tcp) ve SSH (22/tcp) için izin verilmesi gereken adrestir (SSH açıksa Cockpit tünelden kullanılabildiği için 9090 zorunlu değildir). Adres 10 dakika önbelleklenir; *Yenile* düğmesiyle tekrar sorgulanabilir. Aynı yerel ağdaki sunucular ise gateway'i çalıştıran makinenin yerel IP adresini görür.

### Bağlantı proxy'si

Sunuculara giden Cockpit ve SSH bağlantıları bir proxy üzerinden yapılabilir. Böylece sunucular gateway yerine proxy'nin IP adresini görür; güvenlik duvarında tek bir sabit adrese izin vermek yeterli olur.

- **Varsayılan proxy:** `.env`'de `OUTBOUND_PROXY` ile tanımlanır ve tüm sunuculara uygulanır.
- **Sunucu bazında:** panelde *Bağlantı proxy'si* alanı. `socks5://host:1080` gibi bir adres, `direct` (proxysiz) ya da `default` (varsayılana dön) yazılabilir. Boş bırakılırsa mevcut ayar korunur.
- **Desteklenen türler:** `http://` (HTTP CONNECT), `socks5://` (hedef adı gateway'de çözülür), `socks5h://` (hedef adı proxy'de çözülür). Kullanıcı adı/parola adreste verilebilir; sunucu kayıtlarında şifreli saklanır, panelde ve `list_servers` çıktısında parola gizlenir.
- **Çıkış IP'si:** varsayılan proxy tanımlıysa panel, proxy üzerinden görünen çıkış IP adresini de gösterir.

### Ortak SSH anahtarları

Panelin *Ortak SSH Anahtarları* bölümünden bir özel anahtar yapıştırılabilir (parolalıysa anahtar parolasıyla birlikte) ya da yeni bir Ed25519 anahtarı üretilebilir. Bu anahtarlar SSH bağlantılarında (Cockpit tüneli ve SSH yedeği) tüm sunucularda, her kullanıcı için, kullanıcının kendi `.ssh` anahtarlarından sonra denenir. Tablodaki açık anahtar satırını sunuculardaki `~/.ssh/authorized_keys` dosyasına eklemeniz yeterlidir. Özel anahtarlar `data/ssh_keys.json` içinde `SECRET_KEY` ile şifreli saklanır ve panelde gösterilmez.

### Bağlantı sırası

1. **Cockpit (doğrudan):** sunucuda Cockpit kullanıcısı tanımlıysa önce Cockpit adresine bağlanılır. Yönetici işlemleri Cockpit'in superuser (sudo) mekanizmasıyla yapılır.
2. **Cockpit (SSH tüneli):** Cockpit'e ulaşılamazsa (örneğin 9090 güvenlik duvarında kapalıysa) sunucuya SSH ile bağlanılır ve bağlantının içinden sunucunun kendi Cockpit'ine (`localhost:9090`) tünel açılır. Komutlar yine Cockpit üzerinden çalışır; Cockpit portunu dışarıya açmak gerekmez. Sunucuda SSH port yönlendirmesi (`AllowTcpForwarding`, varsayılan açık) kapalı olmamalı.
3. **SSH:** tünel de kurulamazsa komutlar doğrudan SSH ile çalışır. Root olmayan kullanıcıda yönetici komutları `sudo -n` ile (parolasız sudo gerekir) çalışır.

SSH'ta önce sunucuya tanımlı kullanıcı, sonra `SSH_LOGINS` sırası denenir; her kullanıcı için kendi anahtarlarından sonra ortak anahtarlar denenir.

- Doğrudan Cockpit'e ulaşılamadığı 10 dakika hatırlanır; bu sürede her çağrıda zaman aşımı beklenmeden tünel kullanılır.
- Cockpit şifreyi reddederse tünel denenmez, doğrudan SSH'a geçilir.

Her araç çıktısında hangi yolla bağlanıldığı yazar: `"connection": {"via": "cockpit" | "cockpit-ssh" | "ssh"}`. SSH'a düşüldüyse `cockpit_error` alanında Cockpit'in neden kullanılamadığı yer alır.

## MCP araçları

**Okuma araçları** (onay gerekmez):

| Araç | Açıklama |
| --- | --- |
| `list_servers` | Kayıtlı sunucular |
| `fleet_health` | Tüm sunucuların yük, çökmüş servis ve kök disk özeti (paralel) |
| `server_info` | Hostname, OS sürümü, kernel, uptime |
| `resource_usage` | Bellek, swap, yük, CPU, disk doluluğu |
| `service_status` | Servis durumu ve son loglar |
| `failed_services` | Çökmüş systemd unit'leri |
| `read_logs` | `journalctl` (unit, zaman, öncelik, arama filtresi) |
| `top_processes` | En çok CPU/bellek kullanan süreçler |
| `network_info` | IP'ler, rotalar, dinlenen portlar |
| `firewall_status` | Güvenlik duvarı (`firewalld` veya `ufw`) durumu ve kurallar |
| `selinux_status` | SELinux modu ve son engellemeler |
| `apparmor_status` | AppArmor profil durumu ve son engellemeler (Debian/Ubuntu) |
| `available_updates` | Bekleyen (güvenlik) güncellemeleri |
| `package_info` | Paket kurulu mu, sürümü |

**Değiştiren araçlar** (`confirm: true` olmadan yalnızca ne yapılacağını gösterir):

| Araç | Açıklama |
| --- | --- |
| `service_action` | start / stop / restart / reload / enable / disable |
| `install_updates` | `dnf upgrade` / `apt-get upgrade` (opsiyonel yalnızca güvenlik) |
| `package_install` / `package_remove` | Paket kur / kaldır (`dnf` / `apt-get`) |
| `firewall_rule` | Kalıcı port/servis kuralı ekle/kaldır (`firewalld` veya `ufw`) |
| `reboot_server` | Yeniden başlat |
| `run_remote_command` | Serbest komut (`as_root` ile yönetici yetkisi) |

Paket araçları (`available_updates`, `install_updates`, `package_info`, `package_install`, `package_remove`) Debian/Ubuntu'da `apt`, Fedora/RHEL'de `dnf` kullanır; diğer dağıtımlarda hiçbir şey çalıştırmadan hata döner. Dağıtım, sunucu eklenirken ve her bağlantıda `/etc/os-release` üzerinden okunur; değiştiyse `data/servers.json` güncellenir. Dağıtım ve sürümü panelde ve `list_servers` çıktısında görünür. Bu okuma komutları her çağrının işlem kaydında yer alır. Eski sürümden geçerken bir şey yapmak gerekmez: gateway açılışta mevcut sunucular için bu bilgileri arka planda tamamlar, ulaşamadığı sunucular sonradan tamamlanır. Güvenlik duvarı araçları `firewalld` ve `ufw` hangisi kuruluysa onu kullanır; ikisi de kuruluysa `firewalld` aktifken o seçilir. `firewall_rule`, sunucuya erişimi kesebileceği için `ufw`'yi asla açmaz; kapalıysa bunu söyler. `selinux_status` ve `apparmor_status`, ilgili modül olmayan sunucularda hiçbir şey çalıştırmadan yanıt verir. Kurulu araçlar dağıtımla birlikte kontrol edilir; yeni kurulan araç bir sonraki bağlantıda görülür.

Girdiler (servis, paket, port vb.) doğrulanır. Komutlar argv olarak verildiği için komut enjeksiyonuna kapalıdır. Çıktılar JSON döner, uzun çıktılar kırpılır, komutlar zaman aşımına uğrar.

## İşlem kayıtları

Gateway üzerinden yapılan her işlem kaydedilir ve panelde *İşlem kayıtları* sayfasında (`/logs`) gösterilir:

- **MCP araç çağrıları:** kim (kullanıcı, IP), hangi araç, hangi sunucu, parametreler, sunucuda çalıştırılan komutlar (bağlantı yolu, kullanıcı, çıkış kodu), sonuç ve süre. `confirm: true` verilmediği için çalıştırılmayan çağrılar *onay bekliyor* olarak görünür.
- **MCP bağlantıları:** başarılı bağlantılar ve reddedilen kimlik bilgileri.
- **Panel işlemleri:** giriş (başarılı/başarısız), çıkış, sunucu ekleme/güncelleme/test/silme, ortak SSH anahtarı ekleme/üretme/silme.

Sayfada metin araması ile kullanıcı, sunucu, kaynak (MCP / panel) ve durum filtreleri vardır; en yeni kayıt üsttedir.

Kayıtlar `data/audit.log` dosyasında, her satırı bir JSON nesnesi olacak şekilde tutulur (dosya izni `600`). Dosya `AUDIT_LOG_MAX_MB` boyutuna ulaşınca `data/audit.log.1` olarak yedeklenir ve yenisi başlatılır; yalnızca bir yedek tutulur. Şifreler, erişim token'ları ve özel anahtarlar kaydedilmez. **Çalıştırılan komutlar ve çıktıları (alan başına 4000 karaktere kırpılarak) kaydedilir**; komuta ya da çıktıya yazılan gizli bilgiler kayıtta da yer alır. Mesajlar, işlemi yapan isteğin diliyle kaydedilir.

## Arayüz dili

Dil her istekte tarayıcının `Accept-Language` başlığından seçilir: tarayıcı Türkçeyi tercih ediyorsa Türkçe, aksi hâlde İngilizce. Bu; web panelini, giriş sayfalarını, hata mesajlarını ve MCP araç açıklamaları ile mesajlarını kapsar (MCP istemcileri genellikle `Accept-Language` göndermediği için İngilizce alır).

Kaynak metinler İngilizcedir; Türkçe çeviriler [`public-gateway/i18n.py`](public-gateway/i18n.py) içindedir. Yeni bir metin eklerken `t("...")` ile sarın ve çevirisini `TR` sözlüğüne ekleyin; çevirisi eksikse bir test hata verir.

## Geliştirme ve testler

```bash
cd public-gateway
pip install -r requirements-dev.txt
python -m pytest
```

- Testler gerçek sunuculara bağlanmaz. SSH taklit edilir; Cockpit için `tests/fake_cockpit.py` içinde gerçek cockpit-ws davranışına göre yazılmış bir sahte sunucu kullanılır.
- Gerçek bir Cockpit'e karşı test:

  ```bash
  COCKPIT_TEST_URL=https://host:9090 COCKPIT_TEST_USER=... COCKPIT_TEST_PASSWORD=... python -m pytest tests/test_cockpit_real.py
  ```
- GitHub Actions her push ve PR'da testleri Python 3.11 ile çalıştırır.

> `mcp` paketi `<2` sürümüne sabitlenmiştir; kod mcp 1.x API'sini kullanır.

## Güvenlik notları

- Gateway, kayıtlı sunucularda yönetici yetkisiyle işlem yapabilir. `ALLOWED_USERS` listesini kısa tutun, değişiklik gerekmeyen yerlerde `MCP_READ_ONLY=true` kullanın.
- Cockpit şifresi zayıf olsa bile `MCP_API_KEY` olmadan giriş yapılamaz. Token yanlışsa şifre Cockpit'e hiç gönderilmez. 10 başarısız denemeden sonra IP adresi 5 dakika engellenir.
- URL'deki token gateway erişim logunda gizlenir, ama reverse proxy de loglar. Giriş sayfasında yazmayı tercih edin ya da proxy log biçiminde `token=` değerini maskeleyin.
- Panel oturumları 8 saat sürer, çıkışta sunucuda iptal edilir ve gateway yeniden başlayınca biter. Açık SSE bağlantısı 30 saniyede bir yeniden doğrulanır; Streamable HTTP her isteği doğrular.
- Cockpit bağlantılarında TLS doğrulaması varsayılan olarak kapalıdır (kendinden imzalı sertifikalar için); SSH tüneli içindeki Cockpit bağlantısında hiç doğrulanmaz. SSH'ta sunucu anahtarı ilk bağlantıda kaydedilir, değişirse paneldeki *Sunucu anahtarını unut* kullanılana kadar bağlantı reddedilir; bu yüzden ilk bağlantının güvenilir olması gerekir.
- `SECRET_KEY`'i ve `data/` klasörünü gizli tutun, `SECRET_KEY`'i değiştirmeyin.
- `servers.json` okunamazsa boş sayılmaz: gateway üzerine yazmak yerine hata verir.

## Bileşenler

| Klasör | Açıklama |
| --- | --- |
| `public-gateway/` | MCP sunucusu, OAuth yetkilendirme, web paneli, Cockpit ve SSH istemcileri |

## Lisans

[GNU GPL v3](LICENSE)
