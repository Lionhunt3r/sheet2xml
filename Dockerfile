# sheet2xml server: upload page + API, vector PDF reader, Audiveris OMR
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG AUDIVERIS_VERSION=5.11.0
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    AUDIVERIS_BIN=/opt/audiveris/bin/Audiveris \
    TESSDATA_PREFIX=/opt/tessdata

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates curl \
        libasound2t64 libbsd0 libx11-6 libxext6 libxi6 libxrender1 libxtst6 xdg-utils \
        libgl1 libglib2.0-0 libgtk-3-0t64 fontconfig fonts-dejavu-core \
    # the Audiveris package installs a desktop menu entry; create the folders it expects
    && mkdir -p /usr/share/applications /usr/share/desktop-directories /usr/share/mime/packages \
    && curl -fsSL -o /tmp/audiveris.deb \
        "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/Audiveris-${AUDIVERIS_VERSION}-ubuntu24.04-x86_64.deb" \
    && (dpkg -i /tmp/audiveris.deb || apt-get install -y -f --no-install-recommends) \
    && rm /tmp/audiveris.deb \
    # OCR models for lyrics, chord symbols and titles (full models, Audiveris needs the legacy engine)
    && mkdir -p /opt/tessdata \
    && for l in deu eng; do \
        curl -fsSL -o /opt/tessdata/$l.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/$l.traineddata; \
       done \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir ".[server]"

ENV PATH=/venv/bin:$PATH \
    HOME=/tmp \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
USER 1000:1000
CMD ["sheet2xml-server"]
