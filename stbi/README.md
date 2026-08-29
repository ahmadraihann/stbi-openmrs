# Modul STBI — Panel pencarian obat BM25 di keranjang order OpenMRS

Implementasi rekomendasi **R1**: panel temu balik informasi obat yang muncul di
dalam keranjang order OpenMRS 3.x, tepat di bawah kotak pencarian obat bawaan.

Pencarian bawaan OpenMRS mencocokkan potongan **nama** obat pada kamus 323 entri.
Query berupa penyakit mengembalikan nol hasil (`hypertension`, `diabetes`,
`headache` — semuanya 0). Panel ini mengisi kekosongan itu dengan BM25 atas
dataset Kaggle 250k Medicines.

---

## Isi

```
stbi/
├── scripts/build_index.py     CSV 248.218 baris  ->  3.602 profil klinis
├── data/index.json            indeks siap pakai (2,6 MB, hasil generate)
├── service/                   layanan pencarian BM25 (Python, tanpa dependensi)
│   ├── app.py
│   └── Dockerfile
├── frontend/                  microfrontend @openmrs/esm-stbi-app
│   ├── src/
│   │   ├── routes.json        pendaftaran ekstensi ke drug-search-slot
│   │   ├── index.ts
│   │   ├── stbi-drug-search-panel.component.tsx
│   │   ├── stbi-drug-search-panel.scss
│   │   └── stbi.resource.ts
│   ├── dist/                  hasil build (di-mount ke container frontend)
│   └── generated/             importmap.json + routes.registry.json yang dipatch
└── gateway/
    └── default.conf.template  nginx: rute /openmrs/ws/rest/v1/stbi/
```

## Menjalankan

Seluruh modul sudah tersambung ke `docker-compose.stbi.yml` pada distro, dan
`.env` sudah menunjuk ke sana. Jadi cukup:

```bash
cd ../openmrs-distro-referenceapplication
docker compose up -d
```

Buka <http://localhost/openmrs/spa>, masuk sebagai `admin` / `Admin123`, lalu:

1. buka rekam medis pasien yang punya kunjungan aktif
2. tab **Medications** → **Record active medications**
3. panel **Drug reference search** muncul di bawah kotak pencarian bawaan
4. ketik nama penyakit, misalnya `hypertension`

## Membangun ulang

```bash
# indeks, setelah dataset berubah
python3 scripts/build_index.py
docker compose restart stbi

# microfrontend, setelah kode berubah
cd frontend && npm run build
# tidak perlu restart container - dist/ di-mount langsung
```

---

## Keputusan desain

### Deduplikasi: 248.218 baris menjadi 3.602 dokumen

Satu baris dataset adalah satu **merek dagang**, bukan satu obat. Rata-rata ada
264 merek per kombinasi indikasi + kelas terapi; "Treatment of Bacterial
infections" sendiri punya 35.854 merek. Mengindeks baris mentah membuat sepuluh
hasil teratas berisi sepuluh merek dari obat yang sama — bagus di metrik, tidak
berguna secara klinis.

Baris dikelompokkan menjadi **profil klinis**: indikasi, efek samping, dan
ketiga kolom kelas yang identik dianggap satu dokumen. Daftar merek disimpan
sebagai atribut, jadi tidak ada informasi yang hilang.

### Skoring: BM25 per-field, dijumlahkan berbobot

| Field | Bobot | b | Alasan |
|---|---|---|---|
| `uses` | 3,0 | 0,35 | tempat nama penyakit berada; query berbentuk indikasi |
| `classes` | 2,0 | 0,75 | kelas terapi, kimia, aksi |
| `brands` | 1,5 | 0,75 | untuk pencarian berdasarkan nama |
| `side_effects` | 0,6 | 0,75 | pembeda halus antar obat sekelas |

`k1 = 1,2` dan `b = 0,75` mengikuti nilai bawaan Elasticsearch supaya angka
evaluasi tetap sebanding setelah migrasi ke OMOD.

**Kenapa `b` pada `uses` diturunkan.** Dengan `b = 0,75`, query `hypertension`
mengembalikan obat tetes mata glaukoma, bukan antihipertensi — karena "Ocular
hypertension" lebih pendek daripada "Hypertension (high blood pressure)" dan
normalisasi panjang dokumen memenangkannya. Kolom `uses` adalah kosakata
terkendali berisi 819 frasa pendek, jadi panjangnya nyaris tidak membawa
informasi. Ini temuan yang layak masuk laporan.

**Bonus frasa.** Dokumen yang salah satu indikasinya cocok persis dengan query
mendapat bonus (4× jumlah IDF term), dan yang diawali query mendapat 2×. Inilah
yang memisahkan "Hypertension (high blood pressure)" dari "Ocular hypertension".

Setelah kedua penyesuaian itu, hasil teratas menjadi benar secara klinis:

| Query | Kelas terapi teratas |
|---|---|
| `hypertension` | CARDIAC |
| `type 2 diabetes` | ANTI DIABETIC |
| `headache` | PAIN ANALGESICS |
| `asthma` | RESPIRATORY |
| `depression` | NEURO CNS |

### Jembatan ke kamus OpenMRS

Dataset berisi merek dagang India; fasilitas ini hanya punya 323 obat yang bisa
diresepkan. Keduanya tidak akan pernah cocok sepenuhnya. Karena itu hasil BM25
diperlakukan sebagai **kartu informasi** dengan disclaimer di dalam panel, lalu
dicocokkan ke kamus OpenMRS sebagai lapisan kedua. Hanya obat yang berhasil
dicocokkan yang bisa diklik untuk membuka formulir order.

Hasil nol adalah keadaan wajar dan ditampilkan apa adanya — bagi dokter itu
justru informasi: obat ini tidak tersedia di sini.

---

## Kontrak REST

Dilayani di `/openmrs/ws/rest/v1/stbi/`, origin sama dengan OpenMRS, sehingga
`openmrsFetch` bisa dipakai apa adanya.

| Endpoint | Keterangan |
|---|---|
| `GET /search?q=&limit=` | hasil BM25 terurut, lengkap dengan rincian skor per term |
| `GET /drug-info?name=` | satu dokumen berdasarkan nama merek |
| `GET /stats` | metadata indeks, bobot field, k1, b |
| `GET /health` | pemeriksaan kesehatan |

Kontrak ini sengaja dibuat sama dengan yang akan dilayani OMOD Java nanti.
Ketika BM25 pindah ke Elasticsearch, **frontend tidak perlu diubah sama sekali**.

### Padanan di Elasticsearch

Skoring per-field berbobot memetakan langsung ke:

```json
{ "multi_match": {
    "query": "<q>", "type": "most_fields",
    "fields": ["uses^3", "classes^2", "brands^1.5", "side_effects^0.6"] } }
```

Bonus frasa menjadi `term` pada subfield `uses.keyword` (cocok persis) dan
`match_phrase_prefix` pada `uses` (cocok awalan), keduanya diberi boost.

---

## Status dan langkah berikutnya

Sudah selesai dan terverifikasi di browser sungguhan (Chrome headless, login
sampai panel merender hasil):

- indeks 3.602 dokumen dari 248.218 baris
- layanan BM25 berjalan sebagai container, sehat
- rute gateway aktif, REST OpenMRS lain tidak terganggu
- microfrontend terdaftar di `drug-search-slot`, tanpa error konsol
- pencocokan formularium bekerja (`Amlobet Tablet` → `✓ Amlodipine`)

Belum dikerjakan:

- **OMOD Java + Elasticsearch** — kontrak REST sudah disiapkan untuk penukaran
- **Evaluasi P@k, MAP, NDCG** — query set bisa dibangun dari 819 nilai `uses`
  yang unik; sebuah dokumen relevan terhadap query bila `uses`-nya memuat
  indikasi tersebut, sehingga penilaian relevansi terbentuk otomatis
- **Uji otomatis** untuk komponen frontend
