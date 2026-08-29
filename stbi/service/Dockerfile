# Layanan pencarian obat STBI. Tanpa dependensi pihak ketiga - seluruh BM25
# ditulis di atas pustaka standar Python, sehingga image tetap kecil dan
# tidak ada yang perlu diunduh saat build.
FROM python:3.12-slim

WORKDIR /app
COPY app.py /app/app.py

ENV STBI_INDEX=/data/index.json \
    STBI_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/stbi/health',timeout=4).status==200 else 1)"

CMD ["python", "/app/app.py"]
