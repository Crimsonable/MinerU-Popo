FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime libraries used by PyMuPDF/Pillow and font rendering.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-dejavu-core \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-service.txt ./requirements-service.txt
RUN python -m pip install --upgrade pip \
    && pip install -r requirements-service.txt

COPY post_processing ./post_processing
COPY data_engine ./data_engine
COPY service ./service

EXPOSE 9000

CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "9000"]
