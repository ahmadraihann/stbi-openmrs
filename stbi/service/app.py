#!/usr/bin/env python3
"""
Layanan pencarian obat STBI - BM25 atas indeks profil klinis.

Kenapa BM25 ditulis sendiri di sini
-----------------------------------
Arsitektur akhir proyek memakai BM25 bawaan Elasticsearch di dalam OMOD Java.
Layanan ini adalah tahap pertama: kontrak REST-nya dibuat identik dengan yang
akan dilayani OMOD nanti, sehingga panel frontend tidak perlu diubah sedikit pun
saat backend ditukar.

Skoring memakai BM25 per-field lalu dijumlahkan dengan bobot. Bentuk itu dipilih
karena memetakan satu-lawan-satu ke query multi_match/most_fields di Elasticsearch:

    {"multi_match": {"query": q, "type": "most_fields",
                     "fields": ["uses^3", "classes^2", "brands^1.5",
                                "side_effects^0.6"]}}

Parameter k1 dan b memakai nilai bawaan Elasticsearch (1,2 dan 0,75) supaya
angka evaluasi tetap sebanding setelah migrasi.

Tanpa dependensi eksternal - hanya pustaka standar Python.
"""

import json
import math
import os
import re
import sys
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

K1 = 1.2
B = 0.75

# Bobot field. "uses" paling berat karena di situlah nama penyakit berada, dan
# query yang diharapkan berbentuk indikasi ("hypertension"), bukan nama obat.
FIELD_WEIGHTS = {
    "uses": 3.0,
    "classes": 2.0,
    "brands": 1.5,
    "side_effects": 0.6,
}

# Normalisasi panjang dokumen per field. Nilai bawaan BM25 (b=0,75) menghukum
# dokumen panjang, dan pada field `uses` itu justru merusak hasil: query
# "hypertension" mengembalikan obat tetes mata karena "Ocular hypertension"
# lebih pendek daripada "Hypertension (high blood pressure)". Kolom `uses`
# adalah kosakata terkendali dengan 819 nilai pendek, jadi panjangnya nyaris
# tidak membawa informasi - b diturunkan untuk field itu saja.
FIELD_B = {
    "uses": 0.35,
    "classes": 0.75,
    "brands": 0.75,
    "side_effects": 0.75,
}

# Bonus untuk dokumen yang salah satu indikasinya cocok persis dengan query,
# atau diawali query. Ini yang memisahkan "Hypertension (high blood pressure)"
# dari "Ocular hypertension" - keduanya memuat kata yang sama, tetapi hanya yang
# pertama benar-benar berjudul penyakit yang dicari.
#
# Padanan di Elasticsearch: subfield keyword untuk kecocokan persis, dan
# match_phrase_prefix untuk kecocokan awalan, keduanya diberi boost.
PHRASE_BONUS_EXACT = 4.0
PHRASE_BONUS_PREFIX = 2.0

# Kata yang muncul di hampir setiap nilai kolom `use` sehingga tidak membedakan
# apa pun. IDF sebenarnya sudah menekan kata-kata ini, tetapi membuangnya lebih
# awal membuat penjelasan skor lebih bersih dan mempercepat penelusuran.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "due", "for", "from", "in",
    "is", "it", "of", "on", "or", "the", "to", "with", "treatment", "treat",
    "used", "use", "management", "relief", "other",
}

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Huruf kecil, buang tanda baca, buang stopword, lalu normalisasi jamak.

    Pemotongan akhiran -s disengaja sederhana: ia menyatukan "infection" dengan
    "infections" dan "allergy" tetap utuh. Karena diterapkan pada dokumen dan
    query sekaligus, kesalahan potong tidak merusak pencocokan. Stemming penuh
    (Porter/Snowball) sengaja tidak dipakai supaya perilaku tetap mudah dijelaskan.
    """
    out = []
    for tok in TOKEN_RE.findall(text.lower()):
        if tok in STOPWORDS:
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


class Bm25Index:
    def __init__(self, payload):
        self.meta = payload["meta"]
        self.documents = payload["documents"]
        self.n_docs = len(self.documents)

        # postings[field][term] -> list of (doc_index, term_frequency)
        self.postings = {f: defaultdict(list) for f in FIELD_WEIGHTS}
        self.doc_len = {f: [0] * self.n_docs for f in FIELD_WEIGHTS}
        self.avg_len = {}
        # Pencarian cepat berdasarkan nama merek, dipakai endpoint drug-info.
        self.by_brand = {}
        # Bentuk ternormalisasi tiap nilai indikasi, untuk bonus frasa.
        self.use_forms = []

        for idx, doc in enumerate(self.documents):
            self.use_forms.append([" ".join(tokenize(u)) for u in doc["uses"]])
            fields = self._field_texts(doc)
            for field, text in fields.items():
                tokens = tokenize(text)
                self.doc_len[field][idx] = len(tokens)
                counts = defaultdict(int)
                for t in tokens:
                    counts[t] += 1
                for term, tf in counts.items():
                    self.postings[field][term].append((idx, tf))
            for brand in doc["brands"]:
                self.by_brand.setdefault(self._brand_key(brand), idx)

        for field in FIELD_WEIGHTS:
            total = sum(self.doc_len[field])
            self.avg_len[field] = (total / self.n_docs) if self.n_docs else 0.0

        self.idf = {}
        for field in FIELD_WEIGHTS:
            self.idf[field] = {
                term: math.log(1 + (self.n_docs - len(plist) + 0.5) / (len(plist) + 0.5))
                for term, plist in self.postings[field].items()
            }

    @staticmethod
    def _brand_key(name):
        return " ".join(tokenize(name))

    @staticmethod
    def _field_texts(doc):
        return {
            "uses": " ".join(doc["uses"]),
            "classes": " ".join(
                x for x in (doc["therapeutic_class"], doc["chemical_class"], doc["action_class"]) if x
            ),
            "brands": " ".join(doc["brands"]),
            "side_effects": " ".join(doc["side_effects"]),
        }

    def search(self, query, limit=10):
        terms = tokenize(query)
        if not terms:
            return [], {"terms": [], "matched": 0}

        scores = defaultdict(float)
        # Rincian kontribusi per term, agar skor bisa ditelusuri di UI.
        detail = defaultdict(lambda: defaultdict(float))

        for field, weight in FIELD_WEIGHTS.items():
            avg = self.avg_len[field] or 1.0
            b = FIELD_B[field]
            postings = self.postings[field]
            idfs = self.idf[field]
            for term in terms:
                plist = postings.get(term)
                if not plist:
                    continue
                idf = idfs[term]
                for idx, tf in plist:
                    dl = self.doc_len[field][idx]
                    denom = tf + K1 * (1 - b + b * dl / avg)
                    contribution = weight * idf * (tf * (K1 + 1)) / denom
                    scores[idx] += contribution
                    detail[idx][term] += contribution

        # Bonus frasa: hanya dihitung untuk dokumen yang sudah punya skor, jadi
        # tidak menambah biaya penelusuran yang berarti.
        phrase = " ".join(terms)
        query_idf = sum(self.idf["uses"].get(t, 0.0) for t in terms)
        if query_idf:
            for idx in list(scores):
                best = 0.0
                for form in self.use_forms[idx]:
                    if form == phrase:
                        best = max(best, PHRASE_BONUS_EXACT)
                        break
                    if form.startswith(phrase + " "):
                        best = max(best, PHRASE_BONUS_PREFIX)
                if best:
                    bonus = best * query_idf
                    scores[idx] += bonus
                    detail[idx]["(indikasi cocok persis)" if best == PHRASE_BONUS_EXACT
                                else "(indikasi diawali query)"] += bonus

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        results = []
        for idx, score in ranked:
            doc = self.documents[idx]
            contributions = sorted(detail[idx].items(), key=lambda kv: kv[1], reverse=True)
            results.append({
                **doc,
                "score": round(score, 4),
                "explain": [{"term": t, "contribution": round(c, 4)} for t, c in contributions],
            })
        return results, {"terms": terms, "matched": len(scores)}

    def drug_info(self, name):
        key = self._brand_key(name)
        idx = self.by_brand.get(key)
        if idx is None:
            # Pencocokan longgar: cukup satu token nama merek yang cocok penuh.
            # Berguna karena kamus OpenMRS menulis "Aspirin 81mg" sedangkan
            # dataset menulis "Aspirin 81 Mg Tablet".
            head = key.split()
            if head:
                for brand_key, candidate in self.by_brand.items():
                    if brand_key.split()[:1] == head[:1]:
                        idx = candidate
                        break
        if idx is None:
            return None
        return self.documents[idx]


class Handler(BaseHTTPRequestHandler):
    index = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[stbi] %s\n" % (fmt % args))

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        # Terima path lengkap maupun path pendek, supaya layanan ini bisa
        # dipasang di belakang proxy dengan atau tanpa pemotongan awalan.
        for prefix in ("/ws/rest/v1/stbi", "/openmrs/ws/rest/v1/stbi", "/stbi"):
            if path.startswith(prefix):
                path = path[len(prefix):] or "/"
                break
        params = parse_qs(parsed.query)

        if path in ("/health", "/"):
            return self._send({
                "status": "ok",
                "documents": self.index.n_docs,
                "algorithm": f"BM25 k1={K1} b={B}",
            })

        if path == "/stats":
            return self._send({
                **self.index.meta,
                "field_weights": FIELD_WEIGHTS,
                "k1": K1,
                "b": B,
            })

        if path == "/search":
            q = (params.get("q") or [""])[0]
            try:
                limit = max(1, min(50, int((params.get("limit") or ["10"])[0])))
            except ValueError:
                limit = 10
            results, info = self.index.search(q, limit)
            return self._send({
                "query": q,
                "terms": info["terms"],
                "totalMatched": info["matched"],
                "returned": len(results),
                "results": results,
            })

        if path == "/drug-info":
            name = (params.get("name") or [""])[0]
            doc = self.index.drug_info(name)
            if doc is None:
                return self._send({"query": name, "found": False, "document": None}, 200)
            return self._send({"query": name, "found": True, "document": doc})

        return self._send({"error": "not found", "path": parsed.path}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    index_path = os.environ.get("STBI_INDEX", "/data/index.json")
    port = int(os.environ.get("STBI_PORT", "8080"))

    sys.stderr.write(f"[stbi] memuat indeks dari {index_path}\n")
    with open(index_path, encoding="utf8") as fh:
        payload = json.load(fh)

    Handler.index = Bm25Index(payload)
    m = Handler.index.meta
    sys.stderr.write(
        f"[stbi] siap - {m['documents']:,} dokumen dari {m['rows_in_csv']:,} baris CSV, "
        f"BM25 k1={K1} b={B}, port {port}\n"
    )

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
