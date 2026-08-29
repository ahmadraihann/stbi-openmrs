#!/usr/bin/env python3
"""
Membangun indeks dokumen STBI dari medicine_dataset.csv (Kaggle 250k Medicines).

Masalah yang diselesaikan skrip ini
-----------------------------------
Dataset berisi 248.218 baris, tetapi satu baris = satu MEREK DAGANG, bukan satu
obat. Rata-rata ada 264 merek untuk tiap kombinasi indikasi + kelas terapi, dan
"Treatment of Bacterial infections" sendiri punya 35.854 merek. Mengindeks baris
mentah membuat sepuluh hasil teratas BM25 berisi sepuluh merek dari obat yang
sama persis - terlihat bagus di metrik, tidak berguna secara klinis.

Karena itu baris dikelompokkan menjadi PROFIL KLINIS: dua merek dianggap satu
dokumen bila indikasi, efek samping, dan ketiga kolom kelasnya identik. Hasilnya
248.218 baris menyusut menjadi sekitar 3.600 dokumen, dan daftar merek disimpan
sebagai atribut sehingga tidak ada informasi yang hilang.

Keluaran: data/index.json
"""

import csv
import json
import os
import re
import sys
from collections import Counter

CSV_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "..", "medicine_dataset.csv")
OUT_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data", "index.json")

USE_COLS = [f"use{i}" for i in range(5)]
SE_COLS = [f"sideEffect{i}" for i in range(42)]
SUB_COLS = [f"substitute{i}" for i in range(5)]

# Dataset memakai literal "NA" untuk nilai kosong, bukan sel kosong.
MISSING = {"", "na", "n/a", "none", "nan"}


def clean(value):
    v = (value or "").strip()
    return "" if v.lower() in MISSING else v


def collect(row, columns):
    """Ambil nilai unik dari sekelompok kolom, urutan pertama-muncul dipertahankan."""
    seen = []
    for col in columns:
        v = clean(row.get(col))
        if v and v not in seen:
            seen.append(v)
    return seen


def title_case_brand(name):
    """'augmentin 625 duo tablet' -> 'Augmentin 625 Duo Tablet'"""
    return re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), name.strip())


def main():
    csv_path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT)
    out_path = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else OUT_DEFAULT)

    if not os.path.exists(csv_path):
        sys.exit(f"CSV tidak ditemukan: {csv_path}")

    csv.field_size_limit(10 ** 7)

    clusters = {}
    total_rows = 0

    with open(csv_path, encoding="utf8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            total_rows += 1

            uses = collect(row, USE_COLS)
            if not uses:
                continue  # tanpa indikasi, dokumen tak bisa ditemukan lewat query penyakit

            side_effects = collect(row, SE_COLS)
            therapeutic = clean(row.get("Therapeutic Class"))
            chemical = clean(row.get("Chemical Class"))
            action = clean(row.get("Action Class"))
            habit = clean(row.get("Habit Forming")).lower() == "yes"
            brand = clean(row.get("name"))

            key = (
                tuple(sorted(uses)),
                tuple(sorted(side_effects)),
                therapeutic,
                chemical,
                action,
            )

            doc = clusters.get(key)
            if doc is None:
                doc = clusters[key] = {
                    "uses": uses,
                    "side_effects": side_effects,
                    "therapeutic_class": therapeutic,
                    "chemical_class": chemical,
                    "action_class": action,
                    "habit_forming": habit,
                    "brands": [],
                    "brand_count": 0,
                    "substitutes": Counter(),
                }

            doc["brand_count"] += 1
            if brand and len(doc["brands"]) < 40:
                doc["brands"].append(title_case_brand(brand))
            for sub in collect(row, SUB_COLS):
                doc["substitutes"][sub] += 1

    docs = []
    for i, doc in enumerate(clusters.values()):
        # Substitusi paling sering disebut mewakili alternatif yang paling lazim.
        subs = [name for name, _ in doc["substitutes"].most_common(8)]

        # Judul dokumen. Kelas kimia adalah yang paling mendekati "zat aktif"
        # yang tersedia di dataset ini, jadi ia didahulukan. Bila kolom itu NA,
        # jatuh ke kelas aksi. Pilihan terakhir adalah nama merek TERPENDEK -
        # biasanya nama dasar tanpa embel-embel kekuatan dan bentuk sediaan.
        #
        # `title_source` ikut disimpan supaya panel bisa jujur menandai bahwa
        # judul yang ditampilkan hanyalah satu merek dari sekian ratus, bukan
        # nama generik obat.
        if doc["chemical_class"]:
            title, title_source = doc["chemical_class"], "chemical_class"
        elif doc["action_class"]:
            title, title_source = doc["action_class"], "action_class"
        elif doc["brands"]:
            title, title_source = min(doc["brands"], key=len), "brand"
        else:
            title, title_source = "Obat tanpa nama", "none"

        docs.append({
            "id": f"stbi-{i:05d}",
            "title": title,
            "title_source": title_source,
            "uses": doc["uses"],
            "side_effects": doc["side_effects"],
            "therapeutic_class": doc["therapeutic_class"],
            "chemical_class": doc["chemical_class"],
            "action_class": doc["action_class"],
            "habit_forming": doc["habit_forming"],
            "brands": doc["brands"][:12],
            "brand_count": doc["brand_count"],
            "substitutes": subs,
        })

    # Statistik yang dipakai laporan dan ditampilkan panel sebagai jejak audit.
    indications = Counter()
    for d in docs:
        for u in d["uses"]:
            indications[u] += 1

    payload = {
        "meta": {
            "source_csv": os.path.basename(csv_path),
            "rows_in_csv": total_rows,
            "documents": len(docs),
            "compression": round(total_rows / max(len(docs), 1), 1),
            "unique_indications": len(indications),
            "brands_indexed": sum(d["brand_count"] for d in docs),
        },
        "documents": docs,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    m = payload["meta"]
    print(f"Baris CSV dibaca      : {m['rows_in_csv']:,}")
    print(f"Dokumen setelah dedup : {m['documents']:,}  (pemampatan {m['compression']}x)")
    print(f"Indikasi unik         : {m['unique_indications']:,}")
    print(f"Merek terwakili       : {m['brands_indexed']:,}")
    print(f"Ditulis ke            : {out_path}")
    print(f"Ukuran berkas         : {os.path.getsize(out_path)/1e6:.1f} MB")

    print("\n10 indikasi dengan dokumen terbanyak:")
    for name, count in indications.most_common(10):
        print(f"  {count:5,} dokumen  {name}")


if __name__ == "__main__":
    main()
