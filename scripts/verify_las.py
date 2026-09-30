#!/usr/bin/env python3
"""Independent check that the written LAS is actually correct.

Deliberately re-reads the file from disk rather than trusting the pipeline's
own summary: the point of the check is to confirm what a downstream GIS tool
would see.
"""

import sys

import laspy

path = sys.argv[1] if len(sys.argv) > 1 else "output.las"
f = laspy.read(path)
crs = f.header.parse_crs()

print(f"  point count   {f.header.point_count:,}")
print(f"  version       {f.header.version}  point format {f.header.point_format.id}")
print(f"  CRS           {crs.name if crs else 'MISSING'} -> EPSG:{crs.to_epsg() if crs else None}")
print(f"  X metres      {f.x.min():.3f} .. {f.x.max():.3f}")
print(f"  Y metres      {f.y.min():.3f} .. {f.y.max():.3f}")
print(f"  Z metres      {f.z.min():.3f} .. {f.z.max():.3f}")

assert f.header.point_count > 0, "the cloud is empty"
assert crs is not None and crs.to_epsg(), "no CRS is recorded in the LAS header"
assert crs.is_projected, "the LAS CRS is not projected - coordinates are not in metres"
assert f.z.max() < 0, "soundings are not below the surface - the sign convention is wrong"
print("\n  OK")
