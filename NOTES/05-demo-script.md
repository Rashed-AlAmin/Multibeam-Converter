# The 5-minute demo script

What to actually do when you show this, in order.

## 1. Prove it's one command

```bash
docker compose up --build
```

Open <http://localhost:3000>.

## 2. Prove MB-System is real, not mocked

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Shows the real `mbinfo` path and version. Or go further:

```bash
docker compose exec backend mbinfo -V
```

## 3. Convert the real file

Drag `0014_20240913_042229_ShipName.all` onto the page. Talk through the stage
list as it moves: inspect → preprocess → export → reproject → write.

## 4. Prove the output is correct

The summary panel shows point count, depth range, and the **auto-detected UTM
zone**. Say out loud: *"nothing about that zone is hard-coded — it's derived
from the median position of the soundings."*

Then verify the LAS independently:

```bash
# in the container, or anywhere with GDAL/PDAL
pdal info output.las --summary
# or
python3 -c "import laspy; f=laspy.read('output.las'); print(f.header.point_count, f.header.parse_crs())"
```

Best of all: **open it in QGIS or CloudCompare.** It lands in the right place
on a basemap, in metres, the right way up. That is the brief's actual bar:
*"must open correctly in standard GIS software."*

## 5. Prove it fails gracefully

Upload a `.txt` → rejected instantly, clear message.
Upload a renamed junk file called `broken.all` → `mbinfo` rejects it with a
readable error, and the job directory is cleaned up.

## 6. The three sentences that sell it

> "MB-System builds from a pinned stable tag with the GUI tools switched off,
> so the image is reproducible and doesn't drag in X11.
>
> The zone is derived from the data's median position, never hard-coded, and
> the CRS is written into the LAS header so it opens correctly in GIS.
>
> Depth comes out of MB-System positive-down and is negated exactly once, in
> one documented place, so LAS Z is elevation — the convention every viewer
> expects."
