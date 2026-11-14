#!/usr/bin/env python3
"""
update_hail.py - rebuild the storm overlays (hail swaths + wind reports) for the job map.
Only the last WINDOW_DAYS (365) are kept - a rolling 12-month insurance-claim window that
moves forward every time you run this script.

HAIL source: NOAA NCEI Event Footprint Catalog (EFC) MRMS hail footprints.
Source (public, no account): gs://noaa-ncei-ipg/datasets/event-catalog/hail/
  read over plain HTTPS: https://storage.googleapis.com/noaa-ncei-ipg/datasets/event-catalog/hail/mrms_hail_data_YYYY-MM-DD.geojson
  one nationwide file per convective day, Oct 14 2020 -> yesterday, refreshed daily by NOAA.

What it does
  1. Lists the bucket, downloads any daily file that is new or changed since the last run
     (a small per-day cache lives in .hail_cache/, so refreshes only fetch new days).
  2. Keeps only polygons that touch the service-area BBOX below and clips them to it.
  3. Per storm day, unions the ~1 km MRMS grid cells into one shape per hail-size class
     (each area is coloured by the LARGEST size that fell there that day).
  4. Simplifies (~250 m tolerance), rounds coordinates to 4 decimals (~11 m),
     writes storms/hail.geojson (+ storms/wind.geojson and storms/index.json).

WIND source: NOAA NCEI Storm Events Database bulk CSVs (official, QC'd, ~75-90 day lag)
  https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/StormEvents_details-ftp_v1.0_dYYYY_c*.csv.gz
  - "Thunderstorm Wind" rows with a lat/lon inside BBOX -> points in storms/wind.geojson
  - "High Wind" rows are zone-wide (no lat/lon); ones for the service-area NWS zones are listed
    in storms/index.json and shown as a list in the map panel (never placed as fake points).
  For days after the last Storm Events record, preliminary NOAA SPC storm reports
  (https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered_wind.csv) fill the gap,
  flagged "preliminary" in the data and on the map.

Usage:  python3 update_hail.py            # incremental refresh (needs: pip install shapely)
        python3 update_hail.py --full     # ignore cache, re-download everything (~2.2 GB)
"""
import json, os, sys, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import csv, gzip, io, re
from shapely.geometry import shape, box, mapping, MultiPolygon
from shapely.ops import unary_union

BUCKET = "noaa-ncei-ipg"
PREFIX = "datasets/event-catalog/hail/"
# Service area: Knoxville + the East TN counties Perimeter's past jobs cluster in
# (Anderson, Blount, Campbell, Claiborne*, Cocke, Grainger, Hamblen, Jefferson, Knox,
#  Loudon, Monroe, Morgan*, Roane, Sevier, Union; *partial). (min_lon, min_lat, max_lon, max_lat)
BBOX = (-84.75, 35.40, -83.00, 36.50)
# Display classes (lower bound in inches, label). NOAA's native bins are 0.5" wide.
CLASSES = [(0.5, '0.5–0.99"'), (1.0, '1.0–1.49"'), (1.5, '1.5–1.99"'), (2.0, '2.0–2.49"'), (2.5, '2.5"+')]
SIMPLIFY_DEG = 0.0025
DECIMALS = 4
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".hail_cache")
OUT = os.path.join(HERE, "storms")
WINDOW_DAYS = 365
SED_DIR = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
# NWS forecast-zone names (CZ_TYPE Z, WFO MRX) treated as the service area for zone-wide High Wind
ZONES = {"KNOX", "ANDERSON", "BLOUNT/SMOKY MOUNTAINS", "NORTHWEST BLOUNT", "NORTH SEVIER", "SEVIER/SMOKY MOUNTAINS",
         "LOUDON", "ROANE", "UNION", "GRAINGER", "JEFFERSON", "HAMBLEN", "COCKE/SMOKY MOUNTAINS", "NORTHWEST COCKE",
         "NORTHWEST MONROE", "CAMPBELL", "CLAIBORNE", "MORGAN"}
ET = ZoneInfo("America/New_York")

def list_bucket():
    items, tok = [], None
    while True:
        u = ("https://storage.googleapis.com/storage/v1/b/%s/o?prefix=%s&fields=items(name,size,updated),nextPageToken&maxResults=1000"
             % (BUCKET, urllib.parse.quote(PREFIX)))
        if tok: u += "&pageToken=" + urllib.parse.quote(tok)
        d = json.load(urllib.request.urlopen(u, timeout=60))
        items += d.get("items", []); tok = d.get("nextPageToken")
        if not tok: return [i for i in items if i["name"].endswith(".geojson")]

def bbox_hit(coords):
    xs = [p[0] for p in coords]; ys = [p[1] for p in coords]
    return not (max(xs) < BBOX[0] or min(xs) > BBOX[2] or max(ys) < BBOX[1] or min(ys) > BBOX[3])

def fetch_day(item):
    """Download one daily file, return only in-bbox features (cached by NOAA 'updated' stamp)."""
    day = item["name"].rsplit("_", 1)[-1][:-8]
    cp = os.path.join(CACHE, day + ".json")
    if os.path.exists(cp):
        c = json.load(open(cp))
        if c.get("updated") == item["updated"]: return day, c["features"]
    url = "https://storage.googleapis.com/%s/%s" % (BUCKET, item["name"])
    for attempt in range(4):
        try:
            raw = json.load(urllib.request.urlopen(url, timeout=120)); break
        except Exception as e:
            if attempt == 3: raise
    keep = []
    for f in raw.get("features", []):
        g = f.get("geometry") or {}
        rings = g.get("coordinates") or []
        if g.get("type") == "MultiPolygon": rings = [r for poly in rings for r in poly]
        if rings and bbox_hit(rings[0]):
            keep.append({"geometry": g, "p": {k: f["properties"].get(k) for k in ("date", "hour_utc", "hail_min", "convective_day")}})
    json.dump({"updated": item["updated"], "features": keep}, open(cp, "w"))
    return day, keep

def cls_index(hmin):
    idx = 0
    for i, (lo, _) in enumerate(CLASSES):
        if hmin + 1e-9 >= lo: idx = i
    return idx

def rnd(o):
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)): return [round(o[0], DECIMALS), round(o[1], DECIMALS)]
        return [rnd(x) for x in o]
    return o

def polys_only(g):
    """Keep only polygon parts (clip/difference can leave lines/points) -> Polygon or MultiPolygon."""
    if g.is_empty: return None
    if g.geom_type in ("Polygon", "MultiPolygon"): return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    parts = [q for p in parts for q in (p.geoms if p.geom_type == "MultiPolygon" else [p])]
    return MultiPolygon(parts) if parts else None

def build_day(day, feats):
    clip = box(*BBOX)
    by_cls = {}
    times = []
    for f in feats:
        try: g = shape(f["geometry"]).buffer(0)
        except Exception: continue
        g = g.intersection(clip)
        if g.is_empty: continue
        by_cls.setdefault(cls_index(f["p"]["hail_min"] or 0.5), []).append(g)
        if f["p"].get("date") and f["p"].get("hour_utc"):
            times.append(dt.datetime.strptime(f["p"]["date"] + f["p"]["hour_utc"], "%Y-%m-%d%H%M").replace(tzinfo=dt.timezone.utc).astimezone(ET))
    if not by_cls: return []
    # "at least class k" footprints, then exclusive bands so colours never stack
    top = max(by_cls)
    atleast = {}
    acc = None
    for k in range(top, -1, -1):
        if k in by_cls:
            u = unary_union(by_cls[k]); acc = u if acc is None else unary_union([acc, u])
        if acc is not None: atleast[k] = acc
    t0 = min(times) if times else None; t1 = max(times) if times else None
    when = ""
    if t0:   # MRMS hours are hourly bins: a 2300 UTC cell means hail during that hour
        t1e = t1 + dt.timedelta(hours=1)
        h = lambda t: t.strftime("%-I %p").lower()
        when = h(t0) + "–" + h(t1e) if t1e.date() == t0.date() or t1e == dt.datetime.combine(t0.date() + dt.timedelta(days=1), dt.time(), ET) \
               else h(t0) + " – " + t1e.strftime("%b %-d ") + h(t1e)
    out = []
    for k in sorted(atleast):
        band = atleast[k] if k + 1 not in atleast else atleast[k].difference(atleast[k + 1])
        band = polys_only(band.simplify(SIMPLIFY_DEG, preserve_topology=True))
        if band is None or band.area < 1e-6: continue
        out.append({"type": "Feature", "geometry": rnd(mapping(band)),
                    "properties": {"day": day, "local_date": (t0.date().isoformat() if t0 else day), "time_et": when,
                                   "cls": k, "size": CLASSES[k][1], "max_size_day": CLASSES[top][1]}})
    return out

def in_bbox(lat, lon):
    return BBOX[1] <= lat <= BBOX[3] and BBOX[0] <= lon <= BBOX[2]

SED_TZ = {"EST-5": -5, "EDT-4": -4, "CST-6": -6, "CDT-5": -5}

def build_wind(cutoff, today):
    """Storm Events Database (official) + SPC preliminary reports for the recent gap."""
    listing = urllib.request.urlopen(SED_DIR, timeout=60).read().decode()
    years = range(cutoff.year, today.year + 1)
    pts, zone_events, sed_last = [], [], None
    for y in years:
        names = sorted(set(re.findall(r"StormEvents_details-ftp_v1\.0_d%d_c\d{8}\.csv\.gz" % y, listing)))
        if not names: continue
        name = names[-1]
        raw = urllib.request.urlopen(SED_DIR + name, timeout=300).read()
        for r in csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8", errors="replace")):
            d = dt.date(int(r["BEGIN_YEARMONTH"][:4]), int(r["BEGIN_YEARMONTH"][4:]), int(r["BEGIN_DAY"]))
            if sed_last is None or d > sed_last: sed_last = d
            if d < cutoff or r["EVENT_TYPE"] not in ("Thunderstorm Wind", "High Wind"): continue
            t = r["BEGIN_TIME"].zfill(4)
            off = SED_TZ.get(r["CZ_TIMEZONE"].upper(), -5)
            local = (dt.datetime(d.year, d.month, d.day, int(t[:2]), int(t[2:])) - dt.timedelta(hours=off)).replace(tzinfo=dt.timezone.utc).astimezone(ET)
            mag = float(r["MAGNITUDE"]) if r["MAGNITUDE"] else None
            mtype = {"EG": "estimated gust", "MG": "measured gust", "ES": "estimated sustained", "MS": "measured sustained"}.get(r["MAGNITUDE_TYPE"], "")
            # SED wind magnitude is in knots; show mph too
            props = {"date": local.date().isoformat(), "time_et": local.strftime("%-I:%M %p").lower(), "type": r["EVENT_TYPE"],
                     "kts": mag, "mph": round(mag * 1.15078) if mag else None, "mag_type": mtype,
                     "place": (r["BEGIN_LOCATION"] or "").title(), "county": r["CZ_NAME"].title(),
                     "damage": r["DAMAGE_PROPERTY"], "note": (r["EVENT_NARRATIVE"] or "")[:280],
                     "src": "NOAA Storm Events", "prelim": False, "id": r["EVENT_ID"]}
            if r["BEGIN_LAT"] and r["BEGIN_LON"]:
                la, lo = float(r["BEGIN_LAT"]), float(r["BEGIN_LON"])
                if in_bbox(la, lo):
                    pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 4), round(la, 4)]}, "properties": props})
            elif r["CZ_TYPE"] == "Z" and r["WFO"] == "MRX" and r["STATE"] == "TENNESSEE" and r["CZ_NAME"].upper() in ZONES:
                zone_events.append(props)
        print("  Storm Events", name, file=sys.stderr)
    # SPC preliminary reports for days after the last Storm Events record
    start = max(cutoff, (sed_last + dt.timedelta(days=1)) if sed_last else cutoff)
    prelim_days, d = 0, start
    while d < today:
        u = "https://www.spc.noaa.gov/climo/reports/%s_rpts_filtered_wind.csv" % d.strftime("%y%m%d")
        try: txt = urllib.request.urlopen(u, timeout=60).read().decode("utf-8", "replace")
        except Exception: txt = ""
        prelim_days += 1
        for r in csv.DictReader(io.StringIO(txt)):
            try: la, lo = float(r["Lat"]), float(r["Lon"])
            except Exception: continue
            if not in_bbox(la, lo): continue
            t = r["Time"].zfill(4)   # SPC times are UTC; the report "day" runs 12Z-12Z
            day = d if int(t[:2]) >= 12 else d + dt.timedelta(days=1)
            local = dt.datetime(day.year, day.month, day.day, int(t[:2]), int(t[2:]), tzinfo=dt.timezone.utc).astimezone(ET)
            mph = int(r["Speed"]) if r["Speed"].isdigit() else None
            pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 4), round(la, 4)]},
                        "properties": {"date": local.date().isoformat(), "time_et": local.strftime("%-I:%M %p").lower(), "type": "Thunderstorm Wind",
                                       "kts": round(mph / 1.15078) if mph else None, "mph": mph, "mag_type": "reported" if mph else "",
                                       "place": r["Location"], "county": r["County"], "damage": "", "note": r["Comments"][:280],
                                       "src": "NOAA SPC preliminary report", "prelim": True}})
        d += dt.timedelta(days=1)
    pts.sort(key=lambda f: f["properties"]["date"])
    zone_events.sort(key=lambda p: p["date"], reverse=True)
    return pts, zone_events, sed_last, start, prelim_days

def main():
    os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    if "--full" in sys.argv:
        for fn in os.listdir(CACHE): os.remove(os.path.join(CACHE, fn))
    today = dt.datetime.now(ET).date()
    cutoff = today - dt.timedelta(days=WINDOW_DAYS)      # rolling 12-month claim window
    # drop cache entries that have aged out of the window
    for fn in os.listdir(CACHE):
        if fn[:10] < cutoff.isoformat(): os.remove(os.path.join(CACHE, fn))
    items = [i for i in list_bucket() if i["name"].rsplit("_", 1)[-1][:10] >= cutoff.isoformat()]
    print("Window %s .. %s; NOAA EFC hail files in window: %d" % (cutoff, today, len(items)), file=sys.stderr)
    with ThreadPoolExecutor(16) as ex: days = sorted(ex.map(fetch_day, items))
    hail, events = [], []
    for day, feats in days:
        if not feats: continue
        fs = build_day(day, feats)
        if not fs: continue
        hail.extend(fs)
        events.append({"day": day, "max": fs[0]["properties"]["max_size_day"], "max_cls": max(f["properties"]["cls"] for f in fs)})
    with open(os.path.join(OUT, "hail.geojson"), "w") as fh:
        json.dump({"type": "FeatureCollection", "features": hail}, fh, separators=(",", ":"))
    wind, zone_events, sed_last, gap_start, prelim_days = build_wind(cutoff, today)
    with open(os.path.join(OUT, "wind.geojson"), "w") as fh:
        json.dump({"type": "FeatureCollection", "features": wind}, fh, separators=(",", ":"))
    idx = {"built": dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"), "window_start": cutoff.isoformat(), "window_end": today.isoformat(),
           "bbox": BBOX,
           "hail": {"source": "NOAA NCEI Event Footprint Catalog - MRMS hail footprints",
                    "source_url": "https://www.ncei.noaa.gov/products/event-footprint-catalog",
                    "data_url": "https://storage.googleapis.com/%s/%s" % (BUCKET, PREFIX),
                    "classes": [c[1] for c in CLASSES], "first_file": days[0][0] if days else None,
                    "last_file": days[-1][0] if days else None, "files_checked": len(days),
                    "hail_days": len(events), "features": len(hail), "events": events},
           "wind": {"source": "NOAA NCEI Storm Events Database (bulk CSV) + NOAA SPC preliminary storm reports for the recent gap",
                    "source_url": SED_DIR, "storm_events_through": sed_last.isoformat() if sed_last else None,
                    "prelim_from": gap_start.isoformat(), "prelim_days_checked": prelim_days,
                    "points": len(wind), "points_prelim": sum(1 for f in wind if f["properties"]["prelim"]),
                    "wind_days": len({f["properties"]["date"] for f in wind}), "zone_high_wind": zone_events}}
    json.dump(idx, open(os.path.join(OUT, "index.json"), "w"), indent=1)
    print("hail: %d days, %d shapes, %.0f KB | wind: %d points (%d preliminary), %d zone-wide high-wind events, Storm Events through %s"
          % (len(events), len(hail), os.path.getsize(os.path.join(OUT, "hail.geojson")) / 1024, len(wind), idx["wind"]["points_prelim"],
             len(zone_events), idx["wind"]["storm_events_through"]), file=sys.stderr)

if __name__ == "__main__":
    main()
