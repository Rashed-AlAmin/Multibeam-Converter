# ---------------------------------------------------------------------------
# Backend image: MB-System (built from source) + FastAPI conversion service.
#
# Layer order is deliberate and load-bearing. MB-System takes ~40 minutes to
# compile, so it sits near the top where Docker's cache can keep it. Anything
# that changes often (Python deps, then app code) sits below it, so editing
# application code never triggers a recompile of the sonar toolchain.
# ---------------------------------------------------------------------------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    MBSYSTEM_VERSION=MB-System-5.8.2 \
    PYTHONUNBUFFERED=1

# --- 1. Dependencies -------------------------------------------------------
# GMT, GDAL, PROJ and netCDF are MB-System's core dependencies.
# libx11-dev / libmotif-dev are deliberately absent: they are only needed for
# MB-System's graphical tools, which we switch off below with -DbuildGUIs=OFF.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        ca-certificates \
        gmt libgmt-dev \
        libgdal-dev gdal-bin \
        libproj-dev proj-bin proj-data \
        libnetcdf-dev \
        libfftw3-dev \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- 2. MB-System ----------------------------------------------------------
# Pinned to the latest stable tag (not a beta) so the image is reproducible.
# GUIs / OpenCV / TRN / tests are all off: none are used by the conversion
# pipeline, and each one drags in heavy dependencies we would otherwise ship.
RUN git clone --depth 1 --branch "${MBSYSTEM_VERSION}" \
        https://github.com/dwcaress/MB-System.git /tmp/mbsystem \
 && cmake -S /tmp/mbsystem -B /tmp/mbsystem/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DbuildGUIs=OFF \
        -DbuildOpenCV=OFF \
        -DbuildTRN=OFF \
        -DbuildTests=OFF \
 && cmake --build /tmp/mbsystem/build -j2 \
 && cmake --install /tmp/mbsystem/build \
 && ldconfig \
 && rm -rf /tmp/mbsystem \
 && mbinfo -V 2>&1 | head -2

# --- 3. Python dependencies ------------------------------------------------
COPY backend/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# --- 4. Application code (last: cheapest layer to rebuild) -----------------
COPY backend/app /app/app
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
