# Local Kokoro-82M narration (Apache-2.0) on CPU, for `TTS_PROVIDER=kokoro_http`.
# Built from ./scripts by the optional `kokoro` Compose profile:
#   docker compose --profile kokoro up -d kokoro
FROM python:3.11-slim-bookworm

# espeak-ng reads words that are missing from Kokoro's English lexicon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir kokoro==0.7.16 transformers==4.46.3 soundfile==0.12.1 \
       fastapi==0.115.4 \
       "uvicorn==0.32.0" \
    && python -m spacy download en_core_web_sm

WORKDIR /app
COPY kokoro_server.py ./
EXPOSE 8811
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8811/health')"
CMD ["uvicorn", "kokoro_server:app", "--host", "0.0.0.0", "--port", "8811"]
