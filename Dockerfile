# Ekalavya application image: API, Temporal worker, and Streamlit UI share it.
FROM python:3.11-slim-bookworm

# Set WITH_LATEX=0 for a ~1 GB smaller image; equations then render as Unicode text.
ARG WITH_LATEX=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/huggingface

# FFmpeg for assembly, Cairo/Pango for Manim, Noto fonts for Indic and CJK labels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libcairo2-dev libpango1.0-dev ffmpeg curl \
        fonts-noto-core fonts-noto-cjk \
    && if [ "$WITH_LATEX" = "1" ]; then apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra \
        texlive-fonts-recommended texlive-science dvisvgm; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch (for local embeddings and forced alignment) keeps the image small.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[llm,embeddings,alignment,manim]" \
    && python -m spacy download en_core_web_sm

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts/retitle_chats.py ./scripts/
COPY frontend ./frontend
COPY .streamlit ./.streamlit

EXPOSE 8000 8501
CMD ["uvicorn", "scholarmotion.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
