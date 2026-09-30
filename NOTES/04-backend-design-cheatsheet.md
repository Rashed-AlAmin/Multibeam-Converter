# Backend design, explained like you're five

## Why a separate Python backend instead of Next.js API routes?

Three reasons, and they're the answer if you're asked:

1. **The tools are Python's neighbours.** `pyproj` (reprojection) and `laspy`
   (LAS writing) are Python libraries. Doing this in Node means shelling out to
   PDAL and parsing its output — more moving parts, worse error messages.
2. **Long jobs don't fit the request/response shape.** A conversion takes
   minutes. Serverless-style API routes are built for fast requests.
3. **Separation of concerns.** The frontend is a web page. The backend is a
   scientific toolchain. Different images, different lifecycles, different
   scaling needs.

---

## Why jobs, not one big request?

**Metaphor: a dry cleaner. You don't stand at the counter for three hours —
you get a ticket.**

```
POST /api/jobs        → "here's your ticket: a3f9..."   (returns immediately)
GET  /api/jobs/a3f9   → "still pressing, 45% done"      (browser asks every second)
GET  /api/jobs/a3f9/download → your clean suit
```

If we did it in one request, the browser would time out on any real survey.

---

## Why only ONE conversion at a time?

```python
ThreadPoolExecutor(max_workers=1)
```

MB-System is hungry — CPU and RAM. Three users uploading at once on a small
server means three heavy processes fighting, then the **OOM killer** murders
all of them and everyone gets an error.

One at a time: slower for user #3, but **nobody gets a crash.** Extra jobs wait
in a queue. That's a deliberate reliability trade-off, and it's the sort of
thing an interviewer wants you to have thought about.

---

## Temp files: the three rules

1. **Every job gets its own directory**, named by a random UUID. Two users
   uploading files with the same name can never collide.
2. **The moment a job finishes, the intermediates are deleted** — the uploaded
   `.all`, the `.mb59`, the 150 MB `.xyz`. Only the LAS and the small preview
   survive. Those intermediates are several times bigger than the result.
3. **A sweeper thread deletes finished jobs after an hour**, including orphan
   directories left behind by a previous container run. Disk never creeps.

**Metaphor: cook, plate the dish, then wash up — don't leave the pans out.**

---

## Subprocess handling: what "clean" means

Every external command goes through **one** helper, `_run()`. It always:

- passes arguments as a **list**, never a shell string → no shell injection,
  no breakage on filenames with spaces
- sets a **timeout** → a hung `mblist` can't hold a worker forever
- captures **stderr** and puts the last line into the user-facing error
- checks the **exit code** and raises `ConversionError`, which the API turns
  into a readable message

`mblist` output goes **straight to a file**, never through a Python string —
otherwise a 150 MB stdout gets buffered in RAM.

---

## Two error classes, on purpose

```python
except pipeline.ConversionError as exc:
    job.error = str(exc)          # safe, specific: "mbinfo could not read this file"
except Exception:
    job.error = "An unexpected error occurred while converting this file."
    log.exception(...)            # full traceback to the logs, not the user
```

**Metaphor: tell the customer "the oven broke", not the wiring diagram.**
Users get something actionable; you get the stack trace in the logs.

---

## The health endpoint proves it isn't faked

```
GET /api/health → {"ok": true, "mbinfo": "/usr/local/bin/mbinfo", ...}
```

The brief says *"simulated or mocked conversion is not accepted."* This endpoint
lets anyone verify in one curl that the real binary is present in the image.
