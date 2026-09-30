# Multibeam Converter — Kongsberg `.all` → LAS

A web application that takes a raw Kongsberg multibeam `.all` file, converts it
with **real MB-System tools**, reprojects the soundings into the correct
**WGS84 / UTM** zone, and returns a **LAS** point cloud that opens directly in
QGIS, CloudCompare or any standard point-cloud viewer.

```
 browser ──upload──▶ Next.js ──proxy──▶ FastAPI ──▶ mbinfo
                                           │        mbkongsbergpreprocess
                                           │        mblist
                                           │        pyproj  (WGS84 → UTM)
                                           ▼        laspy   (LAS 1.4 + CRS)
                                        output.las ──download──▶ browser
```

---

## 1. Running it

**Requirements:** Docker Engine with Compose v2. Nothing else — no MB-System,
no GDAL, no Python packages on the host.

```bash
docker compose up --build
```

Then open **<http://localhost:3000>**, drop a `.all` file on the page, and
download the LAS when it finishes.

> **First build takes 30–45 minutes.** It compiles MB-System from source.
> That cost is paid once; the layer is cached afterwards and rebuilds of the
> application code take seconds.

Verify the real toolchain is present at any time:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
docker compose exec backend mbinfo -V
```

Shut down with `docker compose down` (add `-v` to also drop the job volume).

---

## 2. Installing MB-System — what I did and what went wrong

### The approach

MB-System is built from source inside `backend/Dockerfile` on an
**`ubuntu:24.04`** base, pinned to the tag **`MB-System-5.8.2`**.

```dockerfile
RUN git clone --depth 1 --branch "${MBSYSTEM_VERSION}" \
        https://github.com/dwcaress/MB-System.git /tmp/mbsystem \
 && cmake -S /tmp/mbsystem -B /tmp/mbsystem/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DbuildGUIs=OFF -DbuildOpenCV=OFF -DbuildTRN=OFF -DbuildTests=OFF \
 && cmake --build /tmp/mbsystem/build -j2 \
 && cmake --install /tmp/mbsystem/build \
 && ldconfig
```

### Problems solved

**Ubuntu 24.04, not 26.04.** My development machine runs Ubuntu 26.04, but the
image targets 24.04 LTS. MB-System's dependency chain (GMT, GDAL, PROJ, netCDF)
is version-sensitive, and 24.04 has the longest-tested combination of those
packages. Building against a non-LTS release invites breakage that has nothing
to do with this application.

**Dropping X11 and Motif.** The brief's suggested `apt-get` line includes
`libx11-dev` and `libmotif-dev`. I checked MB-System's `CMakeLists.txt` and
found the build options it exposes:

```
option(buildGUIs "build graphical tools" ON)
option(buildOpenCV "build OpenCV tools" ON)
option(buildTRN "build MBTRN tools" ON)
```

`-DbuildGUIs=OFF` removes the only consumer of X11/Motif, so neither package is
installed. `-DbuildOpenCV=OFF` drops the photomosaicing tools (OpenCV is a
large dependency we never call) and `-DbuildTRN=OFF` drops terrain-relative
navigation. `-DbuildTests=OFF` skips the unit-test targets. The result is a
meaningfully smaller image and a faster, less fragile build — and nothing the
conversion pipeline uses is lost. This is a headless server; it has no display
to draw on.

**A pinned tag, not `master`.** Upstream's most recent tags are `5.8.3beta*`.
Pinning `MB-System-5.8.2` — the latest non-beta release — means the image is
reproducible: it will build the same way in six months. `--depth 1` on the
clone avoids pulling the project's full history.

**Compiling with `-j2`, not `-j$(nproc)`.** The build machine reports 4 threads
but only has 2 physical cores and 7 GB of RAM. Parallel C++ compilation is
memory-hungry, and `-j4` risks the OOM killer taking out the build 30 minutes
in. `-j2` is the reliable setting here.

**`ldconfig` after install.** MB-System installs shared libraries into
`/usr/local/lib`. Without refreshing the linker cache, `mbinfo` builds fine but
fails at runtime with a missing-library error. The Dockerfile runs `mbinfo -V`
as the final step of that layer, so a broken install fails the *build* rather
than the first user request.

**PEP 668.** Ubuntu 24.04 marks its system Python as externally managed, so
`pip3 install` refuses to run. Inside a container there is no system Python to
protect, so `--break-system-packages` is the correct, deliberate answer rather
than the ceremony of a virtualenv.

---

## 3. The conversion pipeline, and why

Implemented in `backend/app/pipeline.py`.

### Step 1 — `mbinfo -I input.all`

Confirms the file is readable sonar data and reports its MBIO format ID
(Kongsberg `.all` is normally 58). **Run first because it is cheap.** A corrupt
or mislabelled upload is rejected in seconds instead of failing twenty minutes
into a conversion.

### Step 2 — `mbkongsbergpreprocess -I input.all`

Converts the raw file into MB-System's working format (`.mb59`), merging the
navigation and attitude records with the soundings. This is the step that makes
the output *correct* rather than merely *produced* — without it, each sounding
carries a cruder position.

The output filename is located by globbing for `*.mb59` rather than by assuming
a naming convention, so the code does not break if a future MB-System version
names its output differently.

### Step 3 — `mblist -I input.mb59 -MA -OXYZ`

Exports every sounding as longitude, latitude and depth.

- **`-MA`** emits *all* beams. A ping fans out across a swath of hundreds of
  beams; without this flag only the centre beam survives and the vast majority
  of the survey is discarded.
- **`-OXYZ`** emits longitude, latitude and depth. **Capital `Z` is depth,
  positive down.**

`mblist`'s stdout is redirected **straight to a file**. For a 29 MB input this
text is ~150 MB; buffering it through a Python string would be a needless spike
in memory.

### Step 4 — Reproject to UTM *(mandatory, and never hard-coded)*

MB-System emits WGS84 geographic coordinates (EPSG:4326). Degrees are not a
usable engineering unit — one degree of longitude is ~111 km at the equator and
~58 km at 58°N — so X and Y are not on the same scale and distances are
meaningless.

The zone is derived **from the data**:

```python
zone = int(math.floor((lon + 180.0) / 6.0)) + 1
epsg = (32600 if lat >= 0 else 32700) + zone
```

The centre point is the **median** of all soundings, not the mean. A handful of
dropped-out GPS fixes near (0, 0) would drag a mean thousands of kilometres;
the median is unmoved by them.

`pyproj` then transforms every point into that CRS, giving eastings and
northings **in metres**.

### Step 5 — Write LAS with `laspy`

LAS 1.4, point format 1, millimetre scaling (`0.001`) with offsets set to the
cloud's own minimum corner so the scaled integer coordinates stay well inside
the 32-bit range even at UTM northings near 10,000,000 m.

`header.add_crs(crs, keep_compatibility=True)` writes the projection into the
file as both a WKT VLR and the legacy GeoTIFF keys, so modern and older GIS
software alike open the cloud in the right place.

I chose **laspy over PDAL** because the reprojection already happens in-process
with `pyproj`; adding PDAL would mean marshalling millions of points through a
second subprocess and a JSON pipeline definition for no gain. Both are permitted
by the brief.

### The Z convention — stated explicitly

> **LAS `Z` is ELEVATION in metres, positive up.** A sounding 42.3 m beneath the
> surface is written as `-42.3`.

MB-System reports depth positive *down*; the sign is flipped **exactly once**,
in `reproject()`, and nowhere else. This matches what QGIS, CloudCompare and
PDAL expect — without it the sea bed renders above the water surface. The
convention is repeated in the UI after every conversion so the user is never
guessing.

---

## 4. Back-end design

**FastAPI + a job queue**, not a single blocking request. A conversion takes
minutes; a synchronous endpoint would hit browser and proxy timeouts. Upload
returns `202` with a job id, and the browser polls for progress.

**One conversion at a time** (`ThreadPoolExecutor(max_workers=1)`). MB-System is
CPU- and memory-intensive; running several concurrently on a modest server
invites the OOM killer to take out *all* of them. Queuing means user three waits
— but nobody gets a crash. A deliberate reliability trade-off.

**Every job is isolated** in its own UUID-named directory, so two users
uploading files with identical names cannot collide.

**Cleanup is layered:**
1. The instant a job finishes (success *or* failure) the intermediates — the
   uploaded `.all`, the `.mb59`, the `.xyz` — are deleted. They are several
   times larger than the result.
2. A sweeper thread removes finished jobs after one hour, and also removes
   orphan directories left behind by a previous container run.
3. Job data lives on a named Docker volume, so large uploads never inflate the
   container's writable layer.

**Subprocess handling** is centralised in one `_run()` helper: argument lists
(never shell strings), a timeout on every command, `stderr` captured and
surfaced as the user-facing error, exit codes checked.

**Errors are split in two.** `ConversionError` carries messages written for an
end user ("mbinfo could not read this file as Kongsberg sonar data"). Anything
else becomes a generic message to the user and a full traceback in the logs.

### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Confirms the real `mbinfo` binary is present |
| `POST` | `/api/jobs` | Upload a `.all` file, returns `202` + job |
| `GET` | `/api/jobs/{id}` | Status, progress, message, summary |
| `GET` | `/api/jobs/{id}/download` | The finished LAS |
| `GET` | `/api/jobs/{id}/preview` | Thinned sample for the plan view |
| `DELETE` | `/api/jobs/{id}` | Discard a job and delete its files |

---

## 5. Front end

Next.js 15 (App Router). One page, deliberately:

- **Drag-and-drop or click to browse**, with `.all` and empty-file validation
  before anything is sent.
- **Real upload progress** via `XMLHttpRequest` — `fetch` cannot report upload
  progress, and a large survey needs a moving bar.
- **A named stage list** during conversion (inspect → preprocess → export →
  reproject → write) so a non-technical user can see *what* is happening, not
  just that something is.
- **Errors in plain language**, with a route back to trying another file.
- **A result summary**: point count, depth range, auto-detected UTM zone and
  EPSG code, LAS size, and the Z convention in words.
- **Cancel** at any stage, which aborts the upload and deletes the server-side job.

The browser only ever talks to the Next.js origin; `next.config.js` proxies
`/api/*` to the backend service. One origin means no CORS handling and no
backend hostname compiled into the client bundle.

### Bonus features (both implemented)

- **Summary of point count and depth range** — shown in the result panel.
- **Point-cloud preview** — a plan view of the swath rendered to a canvas,
  coloured by depth with a viridis ramp. The backend writes an evenly-thinned
  sample (max 20,000 points) rather than shipping millions to the browser: the
  screen has fewer pixels than the cloud has points, so the full set would cost
  bandwidth and buy nothing.

---

## 6. Known limitations

- **Surveys straddling two UTM zones** are projected entirely into the zone
  containing the median sounding. Points far from that zone's central meridian
  carry slightly more distortion. Splitting a survey across zones is the
  alternative, but it produces multiple files and is rarely what a user wants.
- **Vertical datum is not transformed.** Z is the depth MB-System reports,
  negated. No tide, geoid or chart-datum reduction is applied — that requires
  survey metadata not present in the `.all` file.
- **Job state is in memory.** Restarting the backend forgets in-flight jobs.
  A production deployment would use Redis or a database; for a single-node
  service this is a deliberate simplicity choice.
- **One conversion at a time**, by design (see above). Throughput is bounded by
  a single MB-System process.
- **No authentication.** Anyone who can reach the port can convert files.
- **LAS, not LAZ.** Uncompressed, for maximum compatibility. Adding `lazrs` to
  the requirements would enable compressed output.
- **The 2 GiB upload cap** is a default (`MAX_UPLOAD_BYTES`), chosen to bound
  disk use rather than because of any pipeline limit.

---

## 7. Layout

```
.
├── docker-compose.yml      # one command brings both services up
├── backend/
│   ├── Dockerfile          # MB-System from source + FastAPI
│   ├── requirements.txt
│   └── app/
│       ├── main.py         # HTTP surface
│       ├── jobs.py         # job store, worker, cleanup
│       └── pipeline.py     # the five conversion stages
├── frontend/
│   ├── Dockerfile          # multi-stage Next.js standalone build
│   ├── next.config.js      # /api proxy to the backend service
│   └── app/                # page, layout, styles, helpers
└── NOTES/                  # background notes written while building
```
