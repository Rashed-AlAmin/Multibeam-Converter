# Multibeam Converter (`.all` → LAS)

Upload a Kongsberg multibeam `.all` file, convert it with real MB-System tools, reproject to WGS84/UTM, and download a LAS point cloud.

```
browser → Next.js (:3000) → FastAPI (:8000)
                              ├─ mbinfo
                              ├─ mbpreprocess
                              ├─ mblist
                              ├─ pyproj  (auto UTM)
                              └─ laspy   → output.las
```

---

## Run

Needs Docker Engine + Compose v2. Nothing else on the host.

```bash
docker compose up --build
```

Open http://localhost:3000, drop a `.all` file, wait, download the `.las`.

First build compiles MB-System from source — expect ~15–45 minutes depending on the machine. Later rebuilds are fast because that layer is cached.

```bash
# sanity checks
curl -s http://localhost:8000/api/health | python3 -m json.tool
docker compose exec backend mbinfo -V

# optional end-to-end test against the sample file
./scripts/smoke-test.sh /path/to/0014_20240913_042229_ShipName.all
```

Stop with `docker compose down` (add `-v` to wipe job data).

Against the supplied sample file this pipeline produces **526,038 points** (matches `mbinfo` “Number of Good Beams”), UTM zone **45N / EPSG:32645**, in about 5 seconds after the stack is up.

---

## MB-System install (Docker)

Built from source in `backend/Dockerfile` on `ubuntu:24.04`, pinned to tag **`MB-System-5.8.2`**.

- GUIs / OpenCV / TRN / tests are off (`-DbuildGUIs=OFF`, etc.), so X11/Motif are not installed. This is a headless converter.
- Compile uses `-j2` to avoid OOM on small machines.
- `ldconfig` runs after install; the layer finishes with `mbinfo -V` so a broken toolchain fails the image build, not the first request.
- Python packages use `pip --break-system-packages` because Ubuntu 24.04’s PEP 668 blocks a plain system pip (fine inside a container).

### Things that bit during setup

1. **`mbkongsbergpreprocess` is gone in 5.8.2.** The brief names it; current MB-System replaced it with `mbpreprocess` (long options). The code tries `mbpreprocess` first, then falls back to the old name.
2. **`mbinfo` exits 0 on empty junk.** A file with zero sonar records still “succeeds.” The gate now parses record/beam counts and rejects unusable input early with a clear message.
3. **Pin a stable tag, not `master` / betas**, so the image keeps building the same way later.

---

## Conversion pipeline

All of this lives in `backend/app/pipeline.py`.

| Step | Tool | What it does |
|------|------|----------------|
| 1 | `mbinfo` | Confirm readable Kongsberg data (usually format 58). Cheap fail-fast. |
| 2 | `mbpreprocess` | Raw `.all` → MB-System working format (`.mb59`) with better nav/attitude. |
| 3 | `mblist -MA -OXYZ` | Export every beam as lon, lat, Z. `-MA` = all beams, not centre only. |
| 4 | `pyproj` | WGS84 geographic → WGS84 UTM **metres**. Zone from the data (see below). |
| 5 | `laspy` | Write LAS 1.4 (point format 1) with CRS in the header. |

**Why laspy instead of PDAL?** Reprojection already happens in-process with pyproj. PDAL would add another subprocess and JSON pipeline for no gain. Both are allowed by the brief.

### UTM (mandatory, not hard-coded)

Soundings start as lon/lat (EPSG:4326). Degrees are a bad engineering unit, so X/Y are reprojected to UTM metres.

Zone is taken from the **median** lon/lat of the cloud (median so a few bad GPS zeros don’t yank the mean to the wrong hemisphere):

```text
zone = floor((lon + 180) / 6) + 1
EPSG = 326zz (north) or 327zz (south)
```

### Z convention

**LAS Z is elevation in metres, positive up.** A sounding 11.3 m below the surface is stored as `-11.3`.

The brief says `-OXYZ` is depth positive-down. The shipped `mblist` man page says the opposite:

```text
Z  topography (positive up)
z  depth (positive down)
```

Checked on the sample file: capital `Z` already comes out negative (below surface). So this pipeline **does not flip the sign**. The UI still shows depth positive-down for humans; the LAS keeps elevation positive-up for GIS.

---

## Backend

- **FastAPI + job queue** — upload returns `202` + job id; client polls. Conversions can take minutes.
- **One worker** — MB-System is heavy; parallel jobs on a small box tend to OOM.
- **Per-job scratch dirs** under a Docker volume; intermediates deleted when a job finishes; finished jobs swept after ~1 hour.
- Subprocess helper uses argv lists (no shell), timeouts, and surfaces stderr as user-facing errors when possible.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Confirms real `mbinfo` is present |
| POST | `/api/jobs` | Upload `.all` |
| GET | `/api/jobs/{id}` | Status / progress / summary |
| GET | `/api/jobs/{id}/download` | Finished LAS |
| GET | `/api/jobs/{id}/preview` | Thinned points for the plan view |
| DELETE | `/api/jobs/{id}` | Cancel / discard |

Large uploads can go straight to the backend (`PUBLIC_API_BASE` in compose) so Next doesn’t buffer multi-GB bodies in memory. Smaller JSON calls can still go through the Next `/api` proxy.

---

## Frontend

Next.js App Router, single page:

- drag-and-drop / file picker, basic client checks
- upload progress (XHR)
- named stages while converting
- plain-language errors
- result summary (points, depth range, UTM zone/EPSG, Z note)
- optional plan-view preview (depth-coloured, thinned to ~20k points)

---

## Known limitations

- Surveys that cross a UTM zone boundary are projected into the median zone only.
- No tide / geoid / chart-datum correction — only what MB-System reports.
- Job state is in memory; restarting the backend drops in-flight jobs.
- One conversion at a time (by design).
- No auth.
- Uncompressed LAS (not LAZ).
- Default upload cap 2 GiB (`MAX_UPLOAD_BYTES`).

---

## Layout

```text
.
├── docker-compose.yml
├── scripts/
│   ├── smoke-test.sh
│   └── verify_las.py
├── backend/
│   ├── Dockerfile          # MB-System from source + FastAPI
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── jobs.py
│       └── pipeline.py
└── frontend/
    ├── Dockerfile
    ├── next.config.js
    └── app/
```
