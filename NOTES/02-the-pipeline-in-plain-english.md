# The conversion pipeline, explained like you're five

## The big metaphor

The sonar writes its diary in **a private shorthand only it can read** (`.all`).
You need to hand your boss **a spreadsheet of 3D dots** (`.las`).

There is no single translate button. You go through five rooms.

```
.all  ──▶ [1 mbinfo] ──▶ [2 preprocess] ──▶ [3 mblist] ──▶ [4 UTM] ──▶ [5 laspy] ──▶ .las
 raw       "is this      "rewrite it        "list every    "convert    "write the
 sonar      even          neatly"            dot"           degrees     dots to the
 diary      readable?"                                      to metres"  standard format"
```

---

## Room 1 — `mbinfo -I input.all`

**Metaphor: opening the box and counting what's inside before you buy it.**

Reads the file and reports: how many pings, what date, what area of the world,
what depths, and the **format ID** (Kongsberg `.all` is usually `58`).

**Why it's first:** it's fast, and if the file is corrupt this is where you find
out — in 2 seconds, not 20 minutes in. That's free robustness.

---

## Room 2 — `mbkongsbergpreprocess -I input.all`

**Metaphor: the sonar's handwriting is messy. This rewrites it in neat print.**

The raw `.all` has the depth measurements in one place and the ship's
position/tilt (navigation and attitude) scattered in other records, recorded at
different moments. Preprocessing **merges and tidies** them into MB-System's
own working format, `.mb59`.

**Why bother?** Because without it, the position attached to each sounding is
sloppier. This is the step that makes the output *correct*, not just *produced*.

---

## Room 3 — `mblist -I input.mb59 -MA -OXYZ`

**Metaphor: dumping the neat notebook out as a plain list of dots.**

Two flags carry all the meaning:

- **`-MA`** = "**A**ll beams." One sonar ping fans out like a torch beam and
  measures ~400 points across the sea floor. Without `-MA` you'd only get the
  single point directly under the ship — you'd throw away 99% of your data.
- **`-OXYZ`** = output **longitude, latitude, depth.**

Output is boring text, one dot per line:

```
-2.9481230   58.6620140    42.317
-2.9481190   58.6620110    42.885
```

**⚠️ The trap:** capital `Z` means **depth, positive DOWN** (42.3 = 42.3 metres
*below* the surface). Lowercase `z` would mean elevation, positive up. Get this
backwards and your sea floor points at the sky.

---

## Room 4 — Reproject to UTM (the mandatory bit)

See `03-utm-and-crs.md`. Short version: degrees → metres.

---

## Room 5 — Write the LAS

**Metaphor: everyone in the industry uses the same clipboard. Put your dots on it.**

`laspy` writes a LAS 1.4 file: a header (what's inside, what projection, the
bounding box) followed by millions of tightly packed points.

We flip the sign here — **once, in exactly one place** — so LAS Z is elevation
(positive up), which is what GIS software expects. It's written down in the
README and shown in the UI, because the brief asks you to document it.

---

## Why we stream instead of slurp

A 29 MB `.all` becomes ~3 million soundings, which is a ~150 MB text file.
`f.read().split()` on that would create 9 million Python float objects and
could eat several GB of RAM — on a 7 GB laptop that's an OOM kill.

So `pipeline.py` reads it **500,000 lines at a time** and converts each block
straight into a compact numpy array. Same answer, a fraction of the memory.

**Metaphor: you don't empty the whole swimming pool into your bathroom. You
carry buckets.**
