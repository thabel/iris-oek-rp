FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 harvester
WORKDIR /app

# Fully pinned dependency set first, for layer caching. requirements-dev.txt
# is requirements.txt plus pytest, so the same image can run the test suite
# (docker compose run --rm tests).
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

COPY config.example.toml config.live-control.toml ./
COPY sql ./sql
COPY scripts ./scripts
COPY tests ./tests
RUN mkdir -p /app/output && chown harvester:harvester /app/output

USER harvester

# BETA2007 is the official AdV NTv2 grid for DHDN -> ETRS89 (open licence,
# served by cdn.proj.org). Without it PROJ falls back to a ~3 m Helmert
# transformation; the run report states which one was used.
# Build with --build-arg FETCH_DATUM_GRID=0 for an offline image.
ARG FETCH_DATUM_GRID=1
RUN if [ "$FETCH_DATUM_GRID" = "1" ]; then pyproj sync --file de_adv_BETA2007 -v; fi

ENTRYPOINT ["oek-harvester"]
CMD ["harvest", "--config", "tests/fixtures/config.fixture.toml", "--fixtures", "tests/fixtures/wfs"]
