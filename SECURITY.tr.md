# Güvenlik politikası

[English](SECURITY.md) · **Türkçe**

## Desteklenen sürümler

Güvenlik düzeltmeleri yalnızca en son sürüme girer. Bildirmeden önce en yeni imaja (`latest` ya da en yüksek sürüm etiketi) güncelleyin.

| Sürüm | Destekleniyor |
| --- | --- |
| 1.x (en son) | ✓ |
| daha eski | ✗ |

## Açık bildirme

**Herkese açık issue açmayın.** GitHub üzerinden gizli olarak bildirin:

<https://github.com/bmericc/cockpit-mcp-gateway/security/advisories/new>

Lütfen sürümü ya da imaj etiketini, kurulumu (reverse proxy, `MCP_READ_ONLY`, taşıma), yeniden üretme adımlarını ve etkisini yazın. Birkaç gün içinde yanıt alırsınız. Düzeltme yayınlanınca bildirim, aksini istemediğiniz sürece adınızla yayımlanır.

## Kapsam

Kapsamda: `public-gateway/`'deki gateway ve yayınlanan Docker imajları, örneğin:

- girişi atlatmak (Cockpit parolası + gateway parolası, OAuth, HTTP Basic)
- `confirm: true` olmadan ya da `MCP_READ_ONLY=true` iken komut çalıştırmak veya sunucuyu değiştirmek
- araç girdileri üzerinden komut enjeksiyonu
- `data/`'daki kayıtlı parolaları, SSH anahtarlarını ya da token'ları okumak
- gizli bilgilerin loglara, panele ya da araç çıktısına sızması
- web panelinde CSRF, XSS ya da oturum sorunları

Kapsam dışı: Cockpit, OpenSSH ya da yönetilen sunucuların kendi açıkları; belgelenen gereksinimleri yok sayan kurulumlar (HTTPS yok, `TRUSTED_PROXIES=*`, port reverse proxy olmadan internete açık); izinli bir kullanıcının zaten yapabilmesi gereken işlemler.

## Güvenli çalıştırma

- HTTPS arkasına koyun ve portu `127.0.0.1`'de tutun (`docker-compose.yml` varsayılanı).
- `ALLOWED_USERS`'ı kısa tutun. Değişiklik gerekmeyen yerde `MCP_READ_ONLY=true` kullanın.
- `.env` ve `data/`'yı gizli tutun, birlikte yedekleyin. İlk açılıştan sonra `SECRET_KEY`'i asla değiştirmeyin.
- `TRUSTED_PROXIES`'e yalnızca proxy'nizin adresini yazın.
- Reverse proxy erişim logunda `token=`'ı maskeleyin ya da token'ı URL yerine giriş sayfasında girin.
- Yeni sürümlere güncelleyin; bu depoda GitHub Dependabot uyarıları açık.

Daha fazlası: [Güvenlik notları](README.tr.md#güvenlik-notları).
