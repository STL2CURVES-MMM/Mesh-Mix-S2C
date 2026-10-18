# Handoff: Manifold Mesh Mix engine work (October 2026)

Read `CLAUDE.md` first — it holds the owner's rules. The most important ones:

- Find and confirm the real cause before changing anything.
- Measure on the whole suite before and after every change.
- Ask the owner before anything they'd want a say in.
- Never merge to main or publish a release without the owner's approval.
- Give them the macOS CI build to test first.
- Never commit the owner's own model files.

## Branch and commits

Branch: `claude/happy-mccarthy-50o8b0`. Not merged to main.

| commit | what | state |
|---|---|---|
| up to `98cfc12` | Fillets, sweeps, shrinking tapers, checker round-trip fix | verified, CI green |
| `d0f1ae0` | Mesh repair: tangled-edge pairs stay closed (`meshcheck.pair_tangled`) | verified, fixes rim |
| `7318f28` | Sweeps up to 1.95π, cleanup after sweeps, edge repair in `_settle_tolerances` | verified on suite |
| `0d28921` | Removed `_absorb_strips` (was making round-1 worse); diagnostic scripts and saved results | clean baseline |
| `e708824` | Added `_absorb_self_crossing_strips` and `_self_crossing_regions` (skeleton — was not firing due to bug) | fixed by next commit |
| `e384dce` | **Fix crossing-wire spike absorption** — shared-edge anchor criterion; freeform refit skip; improved trial acceptance | **verified** — zero suite regressions |
| `cd27158` | **WIP: per-round ShapeFix_Face** for UV-gap invalidity on curved faces | **NOT suite-tested** |

## First thing to do next session

**Verify commit `cd27158` on the full suite.** See `DEBRIEF_2026-10-17b.md` for the
exact commands. Do not accept this change until the suite shows zero regressions.
If it regresses anything, revert to `e384dce` and the crossing-wire fix stands alone.

## The owner's three open complaints

All test models are zipped in `test files/` (see its README).

### 1. Rim (`rim-x4.stl`): mostly fixed

- Fixed by `d0f1ae0`: valid solid, volume within 0.15%, 99.7% within tolerance.
- **Remaining:** gap of 0.6–1.2 mm on a flat face on the inner wall near
  (125.5, −163.1, z 18). Diagnose with `regions_at.py` and `checker_worst.py`.

### 2. Tire (`right-side-tire-x2.stl`, 1.64M triangles): not fixed

- Tread chevron ribs with ~1 mm rounded edges. The segmenter cuts the rounded
  edges into hundreds of wrong cylinders and tiny planes.
- Measured: pipe surface fits rounded edges (r ≈ 1, `fillet.fit_pipe`). Helical
  motion fits one chevron arm with 0.03° rms.
- Needed: grow each fillet triangle-by-triangle from a good seed, then snap the
  top strip to the shared r=57 cylinder about the main axis.
- Speed tip: `tools/diagnostics/tire_wedge.py` and `tire_patches.py` work on a
  40° wedge (~20s) instead of full run (~10+ min).

### 3. Mount (`sharps_mount_PETG_rev8.stl`): partially improved, not finished

**Current state (after both fixes at `cd27158`):**
- Crossing-wire fix (`e384dce`): regions 16 and 202's spike triangles are now
  absorbed into neighbours. First-round invalids reduced.
- UV-gap heal (`cd27158`, pending verification): region 36 (large torus,
  `InvalidImbricationOfWires`) becomes valid. First-round demotions drop from 8 to 6.

**What's still wrong:**
- Some surfaces on the arm/handle-bend area are still fragmented freeform patches
  rather than clean analytic faces (the arm sides have long gentle curves — B-spline
  faces would be ideal but the segmenter produces many small planes).
- The notch where the arm meets the oval loop still has a dark gap line in renders.
  Check with: `render_at.py <mmmp> <png> 0,15,16 40 1`

**Diagnosis tools used this session (reference for continuing):**
- `why_demoted2.py MESH TOL Q` — which regions are invalid/inaccurate each round
- `face_area.py MESH TOL Q R OUTDIR` — saves region R's face as .brep
- `face_status.py BREP` — wire validity errors
- `edge_status.py BREP` — edge-level gaps, self-intersections
- `wire_cross.py BREP` — UV position of crossing edges
- Python inline: `BRepAdaptor_Curve2d(e, face)` with orientation-corrected endpoints
  to find UV pcurve gaps (see `DEBRIEF_2026-10-17b.md` for the full chain)

## Engine architecture notes (what was learned this session)

### How crossing wires happen and how the fix works

1. `Segmenter.run()` labels triangles into regions.
2. `split_pinched_regions` handles figure-eight boundaries.
3. `RegionGraph` turns mesh boundary loops into chains (edge runs between corners).
4. `CADAssembler.build_face()` builds each face:
   - `FixAddPCurve` projects edge 3D curves onto the surface to get pcurves.
   - `FixShifted` adjusts pcurves by multiples of 2π for periodic surfaces.
   - `B.Add(face, sfw.Wire())` adds the wire to the face.
5. `_self_crossing_regions` detects faces where `BRepCheck_Wire.SelfIntersect` fires.
6. `_absorb_self_crossing_strips` moves border triangles from crossing regions to
   neighbours, gated on: shared-edge anchor (≥1 edge vertex in target's touch set)
   + fits within tol + gap improves.
7. A trial rebuild is accepted only if crossings decrease and no NEW bad faces appear.

### How UV-gap invalidity happens and how the heal fix works

`FixAddPCurve` projects the 3D edge polyline onto the surface. On a torus, the
V parameter (tube angle) at the endpoint may land 0.02–0.03 away from the true
vertex UV due to interpolation imprecision. `FixShifted` only handles 2π shifts,
not small non-periodic gaps. The outer wire then has a tiny gap in UV space, making
`ClassifyWires` fail with `InvalidImbricationOfWires`.

The fix: after `_settle_tolerances` for still-invalid faces, run `ShapeFix_Face`
at `precision=tol*0.5` (4× larger than the end-of-run `tol*0.1`). This closes UV
gaps smaller than tol/2 without modifying 3D curves. Only accept if the result is
actually valid and not blown up.

The key diagnostic insight: the problem looks like a wire structure issue but the
mesh loops are perfectly connected. The gap is purely in the 2D pcurve representation.
Use `BRepAdaptor_Curve2d(e, face)` with orientation correction (reversed edges
traverse t1→t0) to see the actual UV continuity.

### Half-edge convention (important for any future fix in _absorb_*)

`nb[fi, edge]` is the neighbour triangle across the edge from `F[fi, edge]` to
`F[fi, (edge+1)%3]`. That edge has vertices `F[fi, edge]` and `F[fi, (edge+1)%3]`.
This is NOT the "edge opposite vertex" convention — it's edge-FROM, not edge-opposite.

## Tools (`tools/diagnostics/`)

Run all scripts from repo root with `.venv/bin/python`.

- `bench.py ENGINE_DIR OUT.jsonl MODEL...` — converts like the app, one row per model
- `bench_cmp.py OLD.jsonl NEW.jsonl` — side-by-side comparison
- `why_demoted2.py MESH TOL Q` — which regions each round replaces, and why
- `face_area.py MESH TOL Q R1,R2 OUTDIR` — save region faces as .brep
- `face_status.py BREP`, `edge_status.py BREP`, `wire_cross.py BREP` — face/wire/edge diagnostics
- `step_bad.py STEP` — invalid/tiny/blown-up faces in a STEP
- `checker_worst.py MESH STEP TOL` — worst-point gap
- `regions_at.py MESH TOL Q x,y,z` — which regions sit at a location
- `render_at.py MMMP PNG x,y,z DIST SCALE` — close-up render (needs display or xvfb-run)

Tolerances: mount 0.06917 (q=0.7), rim 0.0443, tire 0.0879 (q=0.6)

## Saved suite results

- `tools/diagnostics/results/r6_all.jsonl` — oldest baseline (before this work)
- `tools/diagnostics/results/r8_commit7318f28.jsonl` — 55-model Mac bench from earlier

## Before giving the owner a build

1. Unit tests pass: `.venv/bin/python -m pytest -q desktop/tests` (177 passed)
2. Lint clean: `.venv/bin/python -m pyflakes desktop`
3. Full suite vs `r6_all.jsonl` — fidelity, faces, validity, time all same or better
4. Owner files rendered — mount, rim, tire look right
5. Push branch, CI green on Linux/Windows/macOS
6. Give owner macOS artifact to test
7. Owner approves before merge

## Notes for the next AI

- The suite path file is at `/tmp/suite_paths.txt` (30 absolute paths). Regenerate
  if needed: `cat tools/diagnostics/results/suite8_models.txt | grep -v scratch | sed "s|tests/models/|$(pwd)/tests/models/|" > /tmp/suite_paths.txt`
- Run `tests/make_models.py` first if tests/models/ is empty.
- Don't use shell `$()` to pass multiple paths to bench.py — use `$(cat file)` with
  word splitting, or pass paths explicitly as separate arguments.
- Background bench processes (with `&`) get killed by the sandbox. Run sequentially.
- `pgrep` doesn't work in the sandbox. Wait for output files instead.
- The owner is in Brazil on a MacBook Air M4.
- Ask before any decision the owner might want to revise.
