# UTM, EPSG and the Z trap — explained like you're five

## Why degrees are useless for engineering

**Metaphor: an orange peel.**

The Earth is round. Maps are flat. Longitude/latitude are *angles* on a ball,
not distances on a sheet.

The killer problem: **1 degree of longitude is not a fixed distance.**

- At the equator, 1° of longitude ≈ **111 km**
- In Scotland (58°N), 1° of longitude ≈ **58 km**
- At the North Pole, 1° of longitude ≈ **0 km**

So if your point cloud is in degrees, the X axis and Y axis have **different
scales** — and they change as you move north. Measure a distance, get nonsense.
Look at it in 3D, see a squashed pancake.

That's why the brief says reprojection is **mandatory**. Engineers need metres.

---

## What UTM does

**Metaphor: you can't flatten a whole orange peel without tearing it — but you
CAN flatten one thin segment almost perfectly.**

UTM slices the globe into **60 vertical strips, 6° of longitude wide.**
Inside one strip, the curvature is small enough that you can pretend it's flat.
Within that strip, **X and Y are both honest metres.**

- **X = easting** — metres east. Centred on 500,000 m so it's never negative.
- **Y = northing** — metres north from the equator (northern hemisphere), or
  from the equator + 10,000,000 m (southern, so it's never negative either).

---

## The zone formula — memorise this

```python
zone = floor((longitude + 180) / 6) + 1
```

**Walk through it** with longitude = **-3.0°** (north of Scotland):

```
-3.0 + 180      = 177.0      # shift so the range starts at 0 instead of -180
177.0 / 6       = 29.5       # each strip is 6 degrees wide
floor(29.5)     = 29         # which whole strip are we in?
29 + 1          = 30         # strips are numbered from 1, not 0
                → zone 30
```

## From zone to EPSG code

**EPSG is just a catalogue number**, like an ISBN for a coordinate system.
Everyone in the world agrees on these numbers.

```python
epsg = 32600 + zone   if latitude >= 0   # northern hemisphere
epsg = 32700 + zone   if latitude <  0   # southern hemisphere
```

So Scotland, zone 30, north → **EPSG:32630** = "WGS 84 / UTM zone 30N".

| Place | Lon | Zone | Hemisphere | EPSG |
|---|---|---|---|---|
| North Scotland | -3° | 30 | N | 32630 |
| New York | -74° | 18 | N | 32618 |
| Sydney | 151° | 56 | S | 32756 |

**Why we must NOT hard-code it:** the brief says so explicitly, and rightly —
the app has no idea where the next survey will be. We read the **median**
longitude/latitude of the actual soundings and derive the zone from the data.

Median, not average, because a handful of corrupt GPS fixes at (0, 0) would
drag an average halfway to Africa. The median shrugs them off.

---

## The Z trap — the easiest mark to lose

Two words that sound the same and mean opposite things:

| Word | Direction | Sea floor 11.3 m down is... |
|---|---|---|
| **Depth** | positive **down** | `+11.3` |
| **Elevation** (topography) | positive **up** | `-11.3` |

LAS files and every GIS viewer expect **elevation**. If you feed them depth,
your sea bed renders **11 metres in the air** and the survey is upside down.

### Now the part that actually bit us

The assignment brief says:

> `-OXYZ` outputs longitude, latitude and depth... Z gives depth (positive
> down); lowercase z gives elevation.

**That is backwards.** The `mblist` man page that ships with MB-System says:

```
Z  for topography (positive upwards) (m)
z  for depth (positive downwards) (m)
```

We ran both against the real file. The man page was right:

```
mblist ... -OXYZ  ->  ...  -11.3155     <- elevation, already correct for LAS
mblist ... -OXYz  ->  ...   11.3155     <- depth
```

So the correct code is **no flip at all**:

```python
elevation = points[:, 2]   # capital Z is already positive-up topography
```

The first version of this pipeline negated it, on the strength of the brief's
wording. The result passed every test that only checked *shapes and counts* —
526,038 points, right zone, right CRS — and was still completely wrong, with
the entire river bed floating in the sky.

**The lesson, and it's the big one:** a sign convention is not something you
read about, it's something you *measure*. Two commands and four numbers settled
what a paragraph of prose got wrong. When a document and a binary disagree,
the binary wins.

### Why we don't "auto-detect" the sign

Tempting: *"if the numbers look positive, flip them."* Don't. A survey inland,
referenced to the ellipsoid, can legitimately sit **above** zero — the
heuristic would silently corrupt it. Follow the documented convention, state it
in the README, and show it in the UI.

---

## Putting the CRS *inside* the LAS file

Writing metres isn't enough. The file must **say** which metres.

```python
header.add_crs(CRS.from_epsg(32630), keep_compatibility=True)
```

**Metaphor: a photo with no location tag.** Without the CRS in the header, QGIS
opens your cloud and asks "where on Earth is this?" — or worse, guesses wrong
and puts your survey in the Atlantic. `keep_compatibility=True` writes both the
modern WKT tag and the older GeoTIFF-style tags, so old software can read it too.

---

## Known limitation worth mentioning

A survey that **straddles two UTM zones** gets forced into one zone (the one
containing the median point). Points far outside their zone distort slightly.
This is standard practice, but it's honest to write it in the README under
"known limitations" — showing you know where your own edges are is worth marks.
