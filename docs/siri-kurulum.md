# "Hey Siri, Ghost" — iPhone Kestirme Kurulumu (~3 dk)

Kilit ekranından sesle Ghost'a emir vermek için. Kestirme, söylediğini
`hooks.example.com`'ye yollar; Ghost işler, cevap Telegram'a düşer.

## Adımlar (iPhone → Kestirmeler uygulaması)

1. **+** → yeni kestirme, adı: **Ghost**
2. Eylem ekle → **Metni Dikte Et** (Dictate Text) — dil: Türkçe
3. Eylem ekle → **URL İçeriğini Al** (Get Contents of URL):
   - URL: `https://hooks.example.com/hook/siri`
   - Yöntem: **POST**
   - Üstbilgiler (Headers):
     - `X-Ghost-Token` : *(değer: Mac'te `grep WEBHOOK_SECRET ~/jarvis/.env` çıktısındaki anahtar)*
   - İstek Gövdesi: **JSON** → alan ekle:
     - `text` (Metin) = **Dikte Edilen Metin** (değişken)
4. Kaydet. Artık: **"Hey Siri, Ghost"** → konuş → bitti.

Cevap ~10-60 sn içinde Telegram'a düşer ("🎙 Kestirme komutu alındı" + sonuç).

## İpuçları
- Kestirmeyi kilit ekranı widget'ına veya Eylem Düğmesine (Action Button)
  bağlarsan tek dokunuşla açılır.
- Aynı webhook'a herhangi bir uygulamadan da POST atabilirsin:
  `{"text": "..."}` → sohbet işi · `{"job": "..."}` → arka plan claude işi.
