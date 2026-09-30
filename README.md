# Multibeam Converter — Kongsberg `.all` → LAS

A web application that takes a raw Kongsberg multibeam `.all` file, converts it
with **real MB-System tools**, reprojects the soundings into the correct
**WGS84 / UTM** zone, and returns a **LAS** point cloud that opens directly in
QGIS, CloudCompare or any standard point-cloud viewer.

```
 browser ──upload──▶ Next.js ──proxy──▶ FastAPI ──▶ mbinfo
                                           │        mbpreprocess
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

> **The first build compiles MB-System from source.** On a 2-core machine that
> stage takes about 4–5 minutes, with the dependency install ahead of it taking
> longer than the compile itself; budget roughly 15 minutes for a cold build of
> both images. The cost is paid once — the layer ordering means rebuilds after
> an application-code change take seconds — measured at 2.8 s for a backend
> rebuild after editing `pipeline.py`.

Verify the real toolchain is present at any time:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
docker compose exec backend mbinfo -V
```

Shut down with `docker compose down` (add `-v` to also drop the job volume).

### Verifying it works

```bash
./scripts/smoke-test.sh /path/to/0014_20240913_042229_ShipName.all
```

Uploads over HTTP exactly as the browser does, polls to completion, downloads
the LAS, re-reads it to confirm the point count and CRS, then checks that junk
input, a wrong extension and an unknown job id are all handled. Output against
the supplied sample file:

```
  points        526,038
  depth         0.28 to 16.51 m (positive down)
  UTM zone      45N  EPSG:32645  WGS 84 / UTM zone 45N
  LAS size      14,729,600 bytes

  point count   526,038
  version       1.4  point format 1
  CRS           WGS 84 / UTM zone 45N -> EPSG:32645
  X metres      760430.441 .. 760500.448
  Y metres      2774467.125 .. 2774552.760
  Z metres      -16.505 .. -0.277

  junk .all accepted for processing: HTTP 202
  status:  error
  message: This file contains no sonar records. It is not a valid Kongsberg
           .all file, or it was truncated before any data was written.
  wrong extension rejected: HTTP 400
  unknown job id:            HTTP 404
```

**526,038 is exactly the "Number of Good Beams" figure `mbinfo` reports for
that file**, so no soundings are lost or duplicated in the pipeline. Every Z is
negative, confirming the whole cloud sits below the surface. End-to-end time is
about 5 seconds.

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

**`mbkongsbergpreprocess` no longer exists.** The brief's pipeline calls for it,
but MB-System 5.8.2 does not ship it — the first end-to-end run failed with
`FileNotFoundError`. It has been replaced by the format-generic `mbpreprocess`
(`--input=` / `--format=` instead of `-I`). The code now tries `mbpreprocess`
first and falls back to the legacy name, so either generation works.

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

**Validating input properly.** `mbinfo` guesses a format from the `.all`
extension and **exits 0 even on a file containing no sonar data at all** — it
simply reports `Number of Records: 0`. A zero exit status is therefore not
proof of a usable file. The first version of the gate trusted the exit code,
and junk input got as far as the preprocessing stage before failing with the
unhelpful message "mbpreprocess produced no .mb59 output". `inspect()` now
parses the record and good-beam counts out of the mbinfo report and rejects an
unusable file in well under a second, with a sentence a surveyor can act on.

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

### Step 2 — `mbpreprocess --input=input.all --format=58`

Converts the raw file into MB-System's working format (`.mb59`), merging the
navigation and attitude records with the soundings. This is the step that makes
the output *correct* rather than merely *produced* — without it, each sounding
carries a cruder position.

The brief names `mbkongsbergpreprocess` for this step. **That program does not
exist in MB-System 5.8.2** — it was superseded by the format-generic
`mbpreprocess`, which takes long options rather than `-I`. The code prefers
`mbpreprocess` and falls back to `mbkongsbergpreprocess` if only the older tool
is present, so the pipeline works against either generation of MB-System.

The output filename is located by globbing for `*.mb59`, because the tool also
drops sidecar files (`.fbt`, `.fnv`, `.inf`) beside it and derives all those
names itself.

### Step 3 — `mblist -I input.mb59 -MA -OXYZ`

Exports every sounding as longitude, latitude and depth.

- **`-MA`** emits *all* beams. A ping fans out across a swath of hundreds of
  beams; without this flag only the centre beam survives and the vast majority
  of the survey is discarded.
- **`-OXYZ`** emits longitude, latitude and **topography**. See the sign note
  below — this is the one place where the brief's description is inverted
  relative to MB-System itself.

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

### The Z convention — verified, not assumed

> **LAS `Z` is ELEVATION in metres, positive up.** A sounding 11.3 m beneath the
> surface is written as `-11.3`.

The brief states that `-OXYZ` gives *"depth (positive down)"* and that lowercase
`z` gives elevation. **That is the wrong way round.** The `mblist(1)` man page
shipped with MB-System says:

```
Z  for topography (positive upwards) (m)
z  for depth (positive downwards) (m)
```

and running both against the sample file confirms it:

```
mblist ... -OXYZ  ->  89.5816280249   25.0637342985   -11.3155
mblist ... -OXYz  ->  89.5816280249   25.0637342985    11.3155
```

So capital `Z` is **already** elevation positive up — the convention LAS and
every GIS viewer expect. **This pipeline therefore applies no sign flip at
all.** My first implementation negated it on the strength of the brief's
wording, which put the sea bed 11 m into the air; reading the man page and
testing the actual binary is what caught it.

No automatic sign detection is applied either. A "flip it if the values look
positive" heuristic would corrupt legitimately positive data — an inland survey
referenced to the ellipsoid can sit above zero — so the documented convention
is followed and stated, rather than guessed at.

The depth figures in the UI and in `Summary` are the same values expressed
positive-down, because that is how surveyors talk about them.

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
├── scripts/
│   ├── smoke-test.sh       # full HTTP round trip against a running stack
│   └── verify_las.py       # independent re-read of a written LAS
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
