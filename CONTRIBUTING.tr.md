# Katkıda bulunma

[English](CONTRIBUTING.md) · **Türkçe**

Yardımınız için teşekkürler. Hata bildirimleri, düzeltmeler ve dokümantasyon iyileştirmeleri memnuniyetle karşılanır.

## Hata bildirme

Issue açın: <https://github.com/bmericc/cockpit-mcp-gateway/issues>. Şunları ekleyin:

- sürüm ya da imaj etiketi (`public-gateway/VERSION` ya da çektiğiniz etiket)
- yönetilen sunucunun dağıtımı ve araç çıktısında görünen bağlantı yolu (`cockpit`, `cockpit-ssh` ya da `ssh`)
- ne yaptığınız, ne beklediğiniz ve ne olduğu, ilgili `docker compose logs gateway` satırlarıyla

Paylaşmak istemediğiniz parolaları, token'ları, host adlarını ve IP adreslerini çıkarın. **Güvenlik sorunları herkese açık issue ile değil, [SECURITY.tr.md](SECURITY.tr.md)'deki yolla bildirilir.**

## Geliştirme ortamı

Python 3.11 ya da üstü:

```bash
cd public-gateway
```

```bash
python -m venv .venv && . .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

Testler gerçek sunucu gerektirmez: SSH taklit edilir, `tests/fake_cockpit.py` cockpit-ws'i taklit eder. Gerçek bir Cockpit'e karşı denemek için bkz. [Geliştirme ve testler](README.tr.md#geliştirme-ve-testler).

## Kontroller

CI bunları her push ve pull request'te çalıştırır; PR açmadan önce siz de çalıştırın:

```bash
python -m pyflakes *.py
```

```bash
python -m pytest
```

```bash
pip-audit -r requirements.txt
```

Docker imajı `linux/amd64`, `linux/arm64` ve `linux/arm/v7` için derlenmeye devam etmeli.

## Kurallar

- **Commit mesajları ve pull request başlıkları İngilizce**, emir kipinde (`Add ...`, `Fix ...`).
- **Arayüz metinleri:** kaynak metinler İngilizcedir. Yenilerini `t("...")` ile sarın ve Türkçesini `public-gateway/i18n.py`'deki `TR` sözlüğüne ekleyin; eksik çeviri varsa bir test başarısız olur.
- **Dokümantasyon iki dillidir:** `README.md`'yi ya da `docs/`'taki bir dosyayı değiştirdiğinizde Türkçe karşılığını (`README.tr.md`, `docs/*.tr.md`, `docs/overview.md` için `docs/genel-bakis.md`) aynı pull request'te güncelleyin.
- Değişiklik yapan araçlar `confirm: true` arkasında kalmalı ve `MCP_READ_ONLY` ile kapanmalı. Komutları argv olarak verin; girdilerden kurulan bir shell dizesiyle asla çalıştırmayın.
- [CHANGELOG.md](CHANGELOG.md)'de `[Unreleased]` altına bir satır ekleyin.
- Sürüm çıkarma: `public-gateway/VERSION`'ı artırın, sonra aynı numarayla `vX.Y.Z` etiketini atın; CI ikisinin eşleştiğini kontrol eder.

## Lisans

Katkıda bulunarak katkınızın [GNU GPL v3](LICENSE) ile lisanslanmasını kabul etmiş olursunuz.
