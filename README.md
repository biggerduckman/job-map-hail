# Job Map + Storm Layers (hail & wind) — Perimeter Roofing

A **separate copy** of the "Roofs We've Done Near You" job map
(original: <https://biggerduckman.github.io/job-map/>, repo `biggerduckman/job-map`, which is **not modified**),
with two free NOAA storm layers added for door-to-door canvassing in East Tennessee.

Live copy: <https://biggerduckman.github.io/job-map-hail/>

## What changed vs. the original

| | Original | This copy |
|---|---|---|
| Client pins (991 built-in jobs), 3-mile radius, "near you" geolocation, Knoxville fallback, popups, optional Google-Sheet CSV | ✔ | ✔ identical (same `INLINE_DATA`, same code) |
| **Storms** button (top-right, below the title) | – | ✔ opens a panel with two toggles: **Hail swaths** and **Wind reports** (both off on load, so the page loads exactly as fast as before; data downloads only when switched on) |
| Hail swaths, colored by size | – | 0.5–0.99" / 1.0–1.49" / 1.5–1.99" / 2.0–2.49" / 2.5"+ (largest size that fell on that spot that day) |
| Tap anywhere on a swath | – | popup listing **every hail date at that spot** in the window, with the time (ET) and hail size |
| Wind report points | – | colored by speed (70+ mph / 58–69 / under 58 / damage with no speed); popup shows date, time ET, mph (kt), measured/estimated, place, damage $ and the NWS narrative. Dashed ring = preliminary (SPC) report |
| Date filter | – | Last 12 months / 6 months / 90 days / 30 days; hail size filter (all, 1"+, 1.5"+) |
| Data window | – | **Rolling 12 months only** (the insurance-claim window). Today's build: **Nov 14, 2025 – Nov 14, 2026** |

Code changes to `index.html` are three small additions (CSS block, `<script src="storms.js">`, and one
`initHail(map)` call at the end of `initMap`). All storm logic is in `storms.js`. Client pins stay clickable
above the swaths (swaths draw in Leaflet's overlay pane under the marker pane).

Files: `index.html` (122 KB, same as the original), `storms.js` (10 KB), `storms/hail.geojson` (~150 KB, ~20 KB gzipped
as served by GitHub Pages), `storms/wind.geojson` (~36 KB, ~5 KB gzipped), `storms/index.json` (metadata).
Platform is Leaflet 1.9.4 (not Google My Maps), so the My Maps KML limits don't apply; no KML conversion was needed.

## Data sources (all free, no account) — retrieved Nov 14, 2026

### Hail swaths — NOAA NCEI Event Footprint Catalog (EFC)
- Product page: <https://www.ncei.noaa.gov/products/event-footprint-catalog>
- Bulk files (public Google Cloud bucket, read over plain HTTPS):
  `https://storage.googleapis.com/noaa-ncei-ipg/datasets/event-catalog/hail/mrms_hail_data_YYYY-MM-DD.geojson`
  (one nationwide GeoJSON per convective day, Oct 14 2020 → yesterday; refreshed daily by NOAA).
  Schema: `datasets/event-catalog/EFC_Dataset_README.md` in the same bucket.
- Underlying data: NOAA MRMS radar hail size estimates (~1 km grid), polygons per UTC hour per 0.5" size bin, hail ≥ 0.5".
- These are **radar estimates**, not ground reports or roof inspections.

### Wind — NOAA NCEI Storm Events Database (official)
- <https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/> — `StormEvents_details-ftp_v1.0_dYYYY_cYYYYMMDD.csv.gz`
  (the newest `c` = creation date is used for each year).
- **Thunderstorm Wind** rows with a lat/lon in the service area → map points. Magnitude is in knots in the database; shown as mph (kt × 1.15078) and kt.
- **High Wind** rows are zone-wide (no lat/lon). They are **not** placed on the map as fake points; the ones for service-area NWS Morristown zones are listed in the Storms panel.

### Wind, recent gap — NOAA SPC preliminary storm reports
- `https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered_wind.csv`, used **only for days after the last Storm Events record**
  (currently Aug 1 → Nov 13, 2026). Speeds are in mph, "UNK" = damage report with no speed. Flagged "preliminary" (dashed ring).

### ⚠ Storm Events lag — the most recent quarter may have gaps
The Storm Events Database runs about **3 months behind**, and its bulk CSV releases are typically **~75 days** behind the
end of a month. Today the official file ends **Jul 31, 2026**. The SPC preliminary reports fill some of that gap, but they are
unverified, incomplete (not every damage report reaches SPC) and are later replaced by the official records. For the most
recent ~3 months, **ExoVzn's free daily-updated hail & wind maps** (<https://www.exovzn.com/maps>) are a good supplement
to check by hand — note only, not integrated here. (Hail from the EFC is radar-based and is usually current to yesterday.)

### Service area (bounding box)
`lon -84.75 → -83.00, lat 35.40 → 36.50` — Knoxville plus the counties the 991 past jobs cluster in: Knox, Anderson,
Blount, Sevier, Loudon, Roane, Union, Grainger, Jefferson, Hamblen, Cocke, Campbell, Monroe (north), Claiborne/Morgan (partial).
Change `BBOX` (and `ZONES` for zone-wide High Wind) at the top of `update_hail.py`.

### Today's build (Nov 14, 2026)
- Hail: 365 daily EFC files checked (Nov 14 2025 – Nov 13 2026) → **67 hail days** in the area (95 shapes), Jan 16 – Sep 24, 2026.
  Largest size per day: 46 days 0.5–0.99", 14 days 1.0–1.49", 6 days 1.5–1.99", 1 day 2.0–2.49" (Sep 4–5, 2026).
- Wind: **87 points** on 19 days (55 Storm Events through Jul 31 2026 + 32 SPC preliminary, Aug–Sep 2026),
  plus 5 zone-wide High Wind events (Sevier/Smoky Mountains).

## Conversion / simplification (`update_hail.py`)
1. List the EFC bucket (JSON API); keep only files whose date is inside the rolling window (**today − 365 days**). Older data is never downloaded.
2. Download each new/changed daily file (16 in parallel), keep polygons whose bounding box touches `BBOX`;
   a tiny per-day cache (`.hail_cache/`, git-ignored) means refreshes only fetch new days.
3. Per storm day: clip to `BBOX`, union the 1 km cells per size class, then turn them into non-overlapping
   "largest size here" bands (no double-shading).
4. Simplify (Douglas–Peucker, 0.0025° ≈ 250 m, topology-preserving), drop non-polygon fragments,
   round coordinates to 4 decimals (~11 m) → `storms/hail.geojson`.
5. Wind: read the Storm Events CSVs for the window's years, filter event type + window + bbox, convert local
   standard time → ET, knots → mph; then SPC preliminary daily CSVs for the gap → `storms/wind.geojson`.
6. `storms/index.json` records window, sources, counts and "data through" dates (shown in the panel).

## Refreshing after a new storm (one command)
```bash
pip install shapely          # once
python3 update_hail.py && git add storms && git commit -m "Refresh storm data" && git push
```
The window rolls forward automatically every run (anything older than 365 days drops out).
`python3 update_hail.py --full` clears the cache and re-downloads the whole window (~365 files).

**Automatic:** `.github/workflows/update-storms.yml` runs the same script every day at 6:30 am ET on GitHub Actions and
commits the new `storms/` files, so the live map stays current with no manual step. Run it on demand from the repo's
Actions tab ("Refresh storm data" → Run workflow). Disable it there if not wanted.

## Phase 2 (paid — NOT implemented)
Prices and terms below are from public listings/memory and **must be confirmed with each vendor** before buying.
- **HailTrace** — hail/wind maps for contractors; has had a free/limited tier, paid plans add meteorologist-verified swaths, address history reports and canvassing tools.
- **Verisk (Benchmark/Geomni) hail reports** — property-level hail verification report, roughly **$35 per report**; useful as claim documentation.
- **Cotality (formerly CoreLogic) Weather Verify** — property-level hail/wind verification used by insurers and adjusters; pricing by quote.
Any of these could replace or sit beside the free NOAA layer; the map code would only need a new data file/URL.

## Disclaimer
Storm layers are informational, from public NOAA data; they are not proof of damage. NOAA notes EFC outputs are
"for informational and research purposes only"; the Storm Events Database is the authoritative record.
