"""
The conversion pipeline: Kongsberg .all -> LAS.

Five stages, mirroring the MB-System workflow:

    1. mbinfo                  inspect the file, confirm it is readable sonar data
    2. mbkongsbergpreprocess   raw .all -> MB-System working format (.mb59)
    3. mblist                  .mb59 -> longitude/latitude/depth text
    4. pyproj                  WGS84 geographic -> WGS84 / UTM (zone auto-detected)
    5. laspy                   UTM points -> LAS, with the CRS in the metadata

Every stage runs in a caller-supplied working directory so the whole job can be
deleted with one rmtree when it finishes.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator

import laspy
import numpy as np
from pyproj import CRS, Transformer

log = logging.getLogger(__name__)

# Generous per-command ceilings. A stuck MB-System process must not hold a
# worker thread forever, but a large survey legitimately takes minutes.
TIMEOUT_INSPECT = 10 * 60
TIMEOUT_PREPROCESS = 60 * 60
TIMEOUT_EXPORT = 60 * 60

# Rows are streamed in blocks so a multi-million-point survey never needs the
# whole text file resident as Python objects.
XYZ_CHUNK_LINES = 200_000


class ConversionError(RuntimeError):
    """Raised with a message that is safe to show to an end user."""


@dataclass
class Summary:
    """Everything the UI shows about a finished conversion."""

    point_count: int = 0
    depth_min_m: float = 0.0          # positive down, as recorded by the sonar
    depth_max_m: float = 0.0
    lon_min: float = 0.0
    lon_max: float = 0.0
    lat_min: float = 0.0
    lat_max: float = 0.0
    utm_zone: int = 0
    utm_hemisphere: str = ""
    epsg: int = 0
    crs_name: str = ""
    easting_min_m: float = 0.0
    easting_max_m: float = 0.0
    northing_min_m: float = 0.0
    northing_max_m: float = 0.0
    mbinfo_format: str = ""
    las_bytes: int = 0
    z_convention: str = (
        "LAS Z is ELEVATION in metres, positive up: a sounding 11.3 m below the "
        "surface is written as -11.3. mblist -OXYZ already emits topography "
        "positive up, so no sign flip is applied. The depth figures above are "
        "the same values expressed positive-down."
    )

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


ProgressFn = Callable[[float, str], None]


def _noop(_pct: float, _msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# subprocess plumbing
# --------------------------------------------------------------------------
def _run(cmd: list[str], *, cwd: Path, timeout: int, stdout_path: Path | None = None) -> str:
    """Run one MB-System command, or fail with a message fit for the UI."""
    log.info("run: %s", " ".join(cmd))
    sink = stdout_path.open("wb") if stdout_path else subprocess.PIPE
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=sink,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"{cmd[0]} is not installed in this image.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(
            f"{cmd[0]} exceeded its {timeout // 60} minute time limit and was stopped."
        ) from exc
    finally:
        if stdout_path:
            sink.close()

    stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        detail = stderr.splitlines()[-1] if stderr else f"exit code {proc.returncode}"
        raise ConversionError(f"{cmd[0]} failed: {detail}")

    if stdout_path:
        return ""
    return (proc.stdout or b"").decode("utf-8", "replace")


# --------------------------------------------------------------------------
# stage 1 - inspect
# --------------------------------------------------------------------------
_FORMAT_RE = re.compile(r"MBIO Data Format ID:\s*(\d+)")


def inspect(all_file: Path, work: Path) -> str:
    """Run mbinfo and return the detected format ID.

    A file that mbinfo cannot parse is rejected here, before any expensive
    work happens, so the user gets a fast, clear failure.
    """
    text = _run(["mbinfo", "-I", all_file.name], cwd=work, timeout=TIMEOUT_INSPECT)
    match = _FORMAT_RE.search(text)
    fmt = match.group(1) if match else ""
    if "Number of Records" not in text and not fmt:
        raise ConversionError(
            "mbinfo could not read this file as Kongsberg sonar data. "
            "It may be corrupt, truncated, or not a .all file."
        )
    return fmt


# --------------------------------------------------------------------------
# stage 2 - preprocess
# --------------------------------------------------------------------------
# MB-System 5.8 replaced the format-specific mbkongsbergpreprocess with the
# generic mbpreprocess, which takes long options instead of -I. Both are
# accepted so the image still works if it is ever pinned to an older release.
PREPROCESS_TOOLS = ("mbpreprocess", "mbkongsbergpreprocess")


def preprocess(all_file: Path, work: Path, mb_format: str = "") -> Path:
    """Convert the raw .all into MB-System's .mb59 working format.

    The tool derives its own output filename and also drops sidecar files
    (.fbt, .fnv, .inf) beside it, so the result is located by globbing for
    *.mb59 rather than by assuming a naming convention.
    """
    tool = next((t for t in PREPROCESS_TOOLS if shutil.which(t)), None)
    if tool is None:
        raise ConversionError(
            "No MB-System preprocessing tool is installed in this image."
        )

    if tool == "mbpreprocess":
        cmd = [tool, f"--input={all_file.name}"]
        if mb_format:
            cmd.append(f"--format={mb_format}")
    else:
        cmd = [tool, "-I", all_file.name]

    before = {p.name for p in work.glob("*.mb59")}
    _run(cmd, cwd=work, timeout=TIMEOUT_PREPROCESS)
    produced = sorted(p for p in work.glob("*.mb59") if p.name not in before)
    if not produced:
        # Some builds overwrite in place rather than creating a new file.
        produced = sorted(work.glob("*.mb59"))
    if not produced:
        raise ConversionError(
            f"{tool} produced no .mb59 output for this file."
        )
    return produced[0]


# --------------------------------------------------------------------------
# stage 3 - export soundings
# --------------------------------------------------------------------------
def export_soundings(mb59: Path, work: Path) -> Path:
    """Write every beam as longitude / latitude / topography text.

    -MA    every beam in the swath, not just the centre beam
    -OXYZ  longitude, latitude, topography

    On the sign of Z, the mblist(1) man page is explicit and was confirmed
    against the sample file before relying on it:

        Z  for topography (positive upwards) (m)
        z  for depth (positive downwards) (m)

    So capital Z is ALREADY elevation, positive up - the convention LAS
    wants. No sign flip is applied anywhere in this pipeline.
    """
    xyz = work / "soundings.xyz"
    _run(
        ["mblist", "-I", mb59.name, "-MA", "-OXYZ"],
        cwd=work,
        timeout=TIMEOUT_EXPORT,
        stdout_path=xyz,
    )
    if not xyz.exists() or xyz.stat().st_size == 0:
        raise ConversionError(
            "mblist produced no soundings. The file may contain navigation "
            "records but no usable beam data."
        )
    return xyz


def _iter_xyz_chunks(xyz: Path) -> Iterator[np.ndarray]:
    """Yield (n, 3) float arrays, reading the text file a block at a time."""
    with xyz.open("r", errors="replace") as handle:
        while True:
            block = list(islice(handle, XYZ_CHUNK_LINES))
            if not block:
                return
            # split() + np.array is markedly faster than np.loadtxt here and,
            # unlike np.fromstring, is not deprecated. The temporary string
            # list is bounded by the chunk size rather than the file size.
            flat = np.array("".join(block).split(), dtype=np.float64)
            usable = (flat.size // 3) * 3
            if usable:
                yield flat[:usable].reshape(-1, 3)


def load_soundings(xyz: Path) -> np.ndarray:
    """Read the whole sounding set, dropping rows that are not real fixes."""
    chunks = []
    for chunk in _iter_xyz_chunks(xyz):
        lon, lat = chunk[:, 0], chunk[:, 1]
        keep = (
            np.isfinite(chunk).all(axis=1)
            & (lon >= -180.0) & (lon <= 180.0)
            & (lat >= -90.0) & (lat <= 90.0)
            & ~((lon == 0.0) & (lat == 0.0))   # null island = missing navigation
        )
        if keep.any():
            chunks.append(chunk[keep])
    if not chunks:
        raise ConversionError("No valid soundings were found in this file.")
    return np.vstack(chunks)


# --------------------------------------------------------------------------
# stage 4 - reproject
# --------------------------------------------------------------------------
def utm_epsg(lon: float, lat: float) -> tuple[int, int, str]:
    """Pick the WGS84 UTM zone that contains a point.

    The globe is sliced into 60 longitude bands of 6 degrees starting at -180.
    EPSG numbers them 326zz in the north and 327zz in the south.
    """
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(60, max(1, zone))
    northern = lat >= 0.0
    epsg = (32600 if northern else 32700) + zone
    return zone, epsg, "N" if northern else "S"


def reproject(points: np.ndarray) -> tuple[np.ndarray, CRS, int, str]:
    """Convert lon/lat degrees to UTM metres, zone chosen from the survey itself.

    The zone is taken from the MEDIAN position rather than the mean so that a
    handful of bad fixes cannot drag the survey into the wrong zone.
    """
    lon_c = float(np.median(points[:, 0]))
    lat_c = float(np.median(points[:, 1]))
    zone, epsg, hemi = utm_epsg(lon_c, lat_c)

    crs = CRS.from_epsg(epsg)
    transformer = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    easting, northing = transformer.transform(points[:, 0], points[:, 1])

    # Capital-Z from mblist is topography, positive up (see the man-page
    # quotation in export_soundings), which is already what LAS consumers
    # expect. Passing it through unchanged is deliberate: no sign flip.
    elevation = points[:, 2]

    out = np.column_stack([easting, northing, elevation])
    finite = np.isfinite(out).all(axis=1)
    return out[finite], crs, zone, hemi


# --------------------------------------------------------------------------
# stage 5 - write LAS
# --------------------------------------------------------------------------
def write_las(utm_points: np.ndarray, crs: CRS, out_path: Path) -> None:
    """Write LAS 1.4 with the projected CRS recorded in the header."""
    header = laspy.LasHeader(point_format=1, version="1.4")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.floor(utm_points.min(axis=0))
    # Writes both a WKT VLR and, where possible, the legacy GeoTIFF keys, so
    # older point-cloud tools still see the projection.
    header.add_crs(crs, keep_compatibility=True)

    with laspy.open(out_path, mode="w", header=header) as writer:
        record = laspy.ScaleAwarePointRecord.zeros(
            len(utm_points), header=header
        )
        record.x = utm_points[:, 0]
        record.y = utm_points[:, 1]
        record.z = utm_points[:, 2]
        writer.write_points(record)


# --------------------------------------------------------------------------
# bonus - plan-view preview
# --------------------------------------------------------------------------
PREVIEW_MAX_POINTS = 20_000


def write_preview(utm_points: np.ndarray, out_path: Path) -> None:
    """Write a small, evenly-thinned sample of the cloud for the browser.

    Sending millions of points to a web page is pointless: the screen has far
    fewer pixels than that. A deterministic stride keeps the swath shape and
    the depth gradient intact while capping the payload at a few hundred KB.
    """
    import json

    count = len(utm_points)
    stride = max(1, count // PREVIEW_MAX_POINTS)
    sample = utm_points[::stride]

    origin = utm_points.min(axis=0)
    rel = sample - origin
    out_path.write_text(
        json.dumps(
            {
                "count": int(len(sample)),
                "total": int(count),
                "origin": [float(v) for v in origin],
                "x": [round(float(v), 2) for v in rel[:, 0]],
                "y": [round(float(v), 2) for v in rel[:, 1]],
                "z": [round(float(v), 2) for v in sample[:, 2]],
            }
        )
    )


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def convert(
    all_file: Path,
    work: Path,
    out_las: Path,
    progress: ProgressFn = _noop,
    preview_path: Path | None = None,
) -> Summary:
    """Run the full pipeline and return the summary shown in the UI."""
    summary = Summary()

    progress(0.05, "Inspecting file with mbinfo")
    summary.mbinfo_format = inspect(all_file, work)

    progress(0.20, "Preprocessing to MB-System format")
    mb59 = preprocess(all_file, work, summary.mbinfo_format)

    progress(0.45, "Exporting soundings")
    xyz = export_soundings(mb59, work)

    progress(0.65, "Reading soundings")
    geographic = load_soundings(xyz)
    summary.point_count = int(geographic.shape[0])
    summary.lon_min, summary.lon_max = map(float, (geographic[:, 0].min(), geographic[:, 0].max()))
    summary.lat_min, summary.lat_max = map(float, (geographic[:, 1].min(), geographic[:, 1].max()))
    # Column 2 is topography (positive up); depth is its negation, and is
    # reported positive-down because that is how surveyors talk about it.
    summary.depth_min_m = float(-geographic[:, 2].max())
    summary.depth_max_m = float(-geographic[:, 2].min())

    progress(0.78, "Reprojecting to UTM")
    utm_points, crs, zone, hemi = reproject(geographic)
    summary.utm_zone = zone
    summary.utm_hemisphere = hemi
    summary.epsg = int(crs.to_epsg())
    summary.crs_name = crs.name
    summary.easting_min_m, summary.easting_max_m = map(float, (utm_points[:, 0].min(), utm_points[:, 0].max()))
    summary.northing_min_m, summary.northing_max_m = map(float, (utm_points[:, 1].min(), utm_points[:, 1].max()))

    progress(0.90, "Writing LAS")
    write_las(utm_points, crs, out_las)
    summary.las_bytes = out_las.stat().st_size

    if preview_path is not None:
        progress(0.97, "Building preview")
        write_preview(utm_points, preview_path)

    progress(1.0, "Complete")
    return summary
