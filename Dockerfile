# sheet2xml server: upload page + API, vector PDF reader, three recognisers for scans (Audiveris, homr, copisteria)
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

# Two neural recognisers next to Audiveris, each in its own environment so that their
# dependencies never meet ours (see src/sheet2xml/omr/engines.py). Versions and weights are
# pinned: copisteria loads its weights as Python pickles, so their checksums are checked.
ARG HOMR_VERSION=0.7.1
ARG COPISTERIA_REF=c685f280914d91a694494588d9f6a376d7917eba
ARG COPISTERIA_WEIGHTS=https://huggingface.co/kobimusic/copista-28m/resolve/main
ENV HOMR_BIN=/opt/homr/bin/homr \
    COPISTERIA_HOME=/opt/copisteria \
    COPISTERIA_PYTHON=/opt/copisteria/.venv/bin/python

RUN python3 -m venv /opt/homr \
    && /opt/homr/bin/pip install --no-cache-dir "homr[cpu]==${HOMR_VERSION}" \
    # fetches homr's models now, so the container needs no network later
    && /opt/homr/bin/homr --init

RUN mkdir -p /opt/copisteria/models/small \
    && curl -fsSL "https://github.com/kobimusic/copisteria/archive/${COPISTERIA_REF}.tar.gz" \
        | tar xz --strip-components=1 -C /opt/copisteria \
    && python3 -m venv /opt/copisteria/.venv \
    && /opt/copisteria/.venv/bin/pip install --no-cache-dir torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu \
    && /opt/copisteria/.venv/bin/pip install --no-cache-dir -e /opt/copisteria \
    && curl -fsSL -o /opt/copisteria/models/evidence-2m.pt "${COPISTERIA_WEIGHTS}/evidence-2m.pt" \
    && curl -fsSL -o /opt/copisteria/models/small/v7_obj-recall-30m_best.pt \
        "${COPISTERIA_WEIGHTS}/small/v7_obj-recall-30m_best.pt" \
    && echo "7a3eb0362b44016e941680c5df1e133239576592ab134057c3dbac8e9f3b8838  /opt/copisteria/models/evidence-2m.pt" \
        > /tmp/weights.sha256 \
    && echo "9f1db282271fb1064461387da4a2e15de14b099c49b619c21429cdc45be85cfe  /opt/copisteria/models/small/v7_obj-recall-30m_best.pt" \
        >> /tmp/weights.sha256 \
    && sha256sum -c /tmp/weights.sha256 && rm /tmp/weights.sha256

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
