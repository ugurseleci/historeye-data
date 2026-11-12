# historeye-data

HistorEye'ın açık veri hattı ve veri seti. HistorEye, kamerayı tarihi bir yapıya doğrultunca adını ve hikâyesini yapının üstünde gösteren konum tabanlı bir AR rehberidir.

Bu repo dünyanın her yerindeki tarihi yapılar için OpenStreetMap ve Wikidata'dan küçük, statik **karo dosyaları** üretir. Uygulama bu karoları indirir ve "yakınımda ne var" hesabını cihazda yapar; kullanıcının konumu hiçbir sunucuya gönderilmez.

> Durum: kurulum aşaması. Tasarım belgesi uygulama reposundadır; hat kodu P0 prototipiyle buraya gelecek.

## Kaynaklar ve lisanslar

| Kaynak | Kullanım | Lisans |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Yapı poligonları ve noktaları, `historic` / `heritage` etiketleri | ODbL 1.0 |
| [Wikidata](https://www.wikidata.org/) | Ad, dönem, mimar, üslup, koruma statüsü | CC0 |
| [Wikimedia Commons](https://commons.wikimedia.org/) | Görsel bağlantısı ve atıf metni (görseller depolanmaz) | Dosya bazında |
| [Copernicus DEM GLO-30](https://spacedata.copernicus.eu/) | Zemin kotu | Copernicus lisansı |

**Veri seti lisansı:** üretilen karolar OpenStreetMap'ten türetilmiş bir veritabanıdır ve [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/) altında yayımlanır. © OpenStreetMap katkıcıları.

Her veri sürümünün tam karo arşivi bu reponun [Releases](../../releases) sayfasından indirilebilir olacaktır.

**Kod lisansı:** henüz belirlenmedi.

## Nasıl çalışır (özet)

1. Geofabrik bölge dosyalarından tarihi yapı adayları seçilir (GitHub Actions, bölge başına paralel iş).
2. OSM'deki `wikidata=Q…` etiketleriyle Wikidata nitelikleri eklenir; ad ve konum tutarlılığı denetlenir.
3. Kalite kapısı: şüpheli kayıtlar AR etiketi olarak değil, yalnızca liste olarak işaretlenir.
4. Dünya ≈ 5 km'lik karolara (geohash-5) bölünür; karolar içerik adresli dosyalar olarak yayımlanır.
