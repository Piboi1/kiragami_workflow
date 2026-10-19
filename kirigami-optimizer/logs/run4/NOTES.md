# Run 4 notes

- Attempt 1 (design_0001): mesh setup check failed before meshing (fluid material
  assigned to "B1" instead of the region entity). 0 CPU h. Fixed in 960e755.
- Retry 1 (design_0002): setup checks passed; meshing started 23:05 UTC and stayed
  at 10% for ~35 min. Local run stopped by the user; remote mesh operation
  cancelled at the user's request at ~23:42 UTC.
- SimScale reported 9.42 CPU hours used by the cancelled mesh, above the 6 CPU h
  per-run cap. No core-hour estimate was logged before meshing, so the cap was
  never applied.
- No CFD results; no Google Sheet row written.
