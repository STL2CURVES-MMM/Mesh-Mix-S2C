# Handoff: Manifold Mesh Mix engine work (October 2026)

For whoever (person or AI) picks this up next. Read `CLAUDE.md` first: it
holds the owner's rules for working here. The most important ones:

- Find and confirm the real cause before changing anything.
- Measure on the whole suite before and after.
- Ask the owner before anything they'd want a say in.
- Never merge to main or publish a release without the owner's approval. Give
  them the macOS CI build to test first.
- Never commit the owner's own model files.

## Branch and commits

Branch: `claude/happy-mccarthy-50o8b0`. Not merged to main. No release made.

| commit | what | state |
|---|---|---|
| up to `98cfc12` | Fillets (pipe surfaces), shrinking/tapered sweeps, checker round-trip fix, build ID in log, "nearly right beats far off" ranking | verified on the suite earlier, CI green |
| `d0f1ae0` | Mesh repair: pieces touching along an edge each stay closed (`meshcheck.pair_tangled`) + test | **verified**: unit tests pass; fixes the owner's rim |
| `7318f28` | WIP in `engine/mmm/cad.py` (see below) | **not yet measured on the full suite** |
| this commit | `HANDOFF.md`, `tools/diagnostics/` | scripts and saved result files |

### What the WIP commit `7318f28` changes (engine/mmm/cad.py)

1. **Turned surfaces up to almost a full turn.** In `_find_sweeps`, a revolution
   may span up to 1.95π. The old 1.6π limit, meant for threads, stays for helices.
2. **Cleanup after sweeps.** In `run()`, after `_find_sweeps`/`_find_fillets`
   find anything, `_absorb_slivers`, `_smooth_borders` and `_absorb_strips` run
   again. `_smooth_borders` may now move triangles onto swept and fillet
   surfaces too.
3. **Edge repair in `_settle_tolerances`.** Edges that `BRepLib.SameParameter`
   gives up on are fixed with `ShapeFix_Edge.FixSameParameter`. Verified on the
   owner's mount: 4 of 13 invalid faces fixed, with no tolerance increase.
4. **`_absorb_strips` (experimental).** Strips of triangles poking into a
   neighbouring surface (every corner on its border) join it if they fit. Its
   aim is to remove "spikes" (borders that go in and straight back out), which
   make wires cross themselves. First test on the mount was mixed: round 1
   invalid faces went 9 → 12, later rounds 4 → 2. **Measure it, and remove it
   if it doesn't clearly help.**

Partial suite run with items 1 and 2 (before 3 and 4): every user and gear
model was identical or better (mount 271 → 236 faces, same accuracy). The
generated test models failed in that run only because of a path mistake in
how the run was started (relative paths from the wrong directory), so
**re-run them**.

## The owner's three open complaints

The owner's files are NOT in the repo. They are `rim-x4.stl`,
`right-side-tire-x2.stl` and `sharps_mount_PETG_rev8.stl`. Ask the owner for
them if needed.

### 1. Rim (`rim-x4.stl`): mostly fixed

- **Cause:** at 4 ribs a 3 mm notch is cut under the rib (z 0–10), so the rib
  touches the wall only along two lines. That gives 8 edges, each shared by 4
  triangles. The repair split those into fans and filled the slits with
  zero-width triangles: zero-area faces, km-long faces, STEP volume 11× the
  mesh.
- **Fix (`d0f1ae0`):** pair the triangles around each such edge by angle
  (`meshcheck.pair_tangled`).
- **Now:** valid solid, volume within 0.15%, 99.7% within tolerance, and Auto
  picks CAD.
- **Left:** the largest gap is 0.6–1.2 mm, on a flat face on the inner wall
  near (125.5, −163.1, z 18). The rim also has an inner hub (r 2.5–6) and
  radial walls. Find out why that plane is wrong there.

### 2. Tire (`right-side-tire-x2.stl`, 1.64M triangles): not fixed

- **Shape:** the tread is chevron ribs. Each rib is a flat top strip (cylinder
  r = 57.00 about z) with a ~1.0 mm-radius rounded edge on each side, running
  along a helix about the tire axis, plus straight side walls. Tire centre
  (379, −154).
- **Problem:** the segmenter cuts the rounded edges into hundreds of cylinders
  with wrong radii (1–283 mm) and tiny planes, which looks faceted. The top
  strip is fitted as cylinders of r 49–50 instead of 57 (a narrow strip is
  ill-conditioned).
- **Measured:** a pipe (fillet) surface fits the rounded edge well (0.60 tol
  deviation, mid-edge 0.86 tol, `fillet.fit_pipe` on regions with r ≈ 1). A
  helical motion about z fits one chevron arm with 0.03° rms. But
  `_find_fillets` only joins whole regions of the same radius, and the
  regions straddle the fillet's borders.
- **Suggested fix:** grow each fillet triangle by triangle from a good seed
  (an r≈1 cylinder piece). Add neighbouring triangles that lie on the pipe
  within tolerance with matching normals, then refit and repeat. Then snap the
  top strip to the shared r = 57 cylinder about the part's main axis.
- **Speed tip:** `tools/diagnostics/tire_wedge.py` and `tire_patches.py` work
  on a 40° wedge of the tire, which takes about 20 s to segment instead of a
  10+ min full run.

### 3. Mount (`sharps_mount_PETG_rev8.stl`): handle-bend patchwork and a notch, not fixed

- **Cause, confirmed:**
  1. In the first CAD build, about 13 faces come out **invalid** (mostly
     cylinders and tori).
  2. `_inaccurate_regions` leaves invalid faces out of the shape, so their
     triangles look uncovered. They get "demoted" to freeform cells.
  3. Freeform cells that still fail become the mesh's own flat triangles
     (`_flat_facets`). That is the triangle patchwork on the arm.
- **Reasons faces were invalid:**
  - one edge per face not "SameParameter": fixed in WIP item 3
  - self-crossing wires from spikes in region borders: face 16 had edges going
    0.6 mm in and back; WIP item 4 tries to fix this
  - `InvalidImbricationOfWires` (an inner wire not inside the outer one): faces
    36 and 197
  - `BadOrientationOfSubshape`: face 198
- **Next:**
  1. Finish the invalid-face fixes. Trace with `why_demoted2.py`, save faces
     with `face_area.py`, find the cause with `face_status.py`,
     `edge_status.py` and `wire_cross.py`.
  2. Check the notch where the arm meets the oval loop: there's a dark gap
     line in close-up renders (`render_at.py <mmmp> <png> 0,15,16 40 1`).
  3. The arm's sides are gently curved with long facets: deflection is
     0.005 mm, triangles up to 6 mm long. Ideally they become one B-spline
     face, not many planes.
- **Tried and reverted:** accepting more sweeps by relaxing `_no_worse` (allow
  ≤1.2 tol). It made faces invalid, and those sweeps weren't on the bend
  anyway.

## Tools (`tools/diagnostics/`)

All scripts take paths as arguments. Run them from the repo root with
`.venv/bin/python`. Each script's first line explains it. The main ones:

- `bench.py ENGINE_DIR OUT.jsonl MODEL...`: converts like the app (Auto,
  quality by size). Use **absolute** model paths. Run it on a *copy* of
  `engine/` so you can keep editing while it runs.
  `bench_cmp.py OLD.jsonl NEW.jsonl` compares two runs.
- `results/`: earlier suite results (`r6_*` = before this work, `r8_*` =
  partial run with WIP items 1–2) and the model list (`suite8_models.txt`).
  - Generated models: `tests/make_models.py` makes them.
  - Gear models: from the owner's gear set.
  - Owner files: ask the owner.
- `why_demoted2.py MESH TOL Q`: which regions each build round replaces, and
  whether they were invalid or inaccurate (with far-point locations).
- `face_area.py MESH TOL Q R1,R2 OUTDIR`: builds once, saves chosen regions'
  faces as .brep, compares areas.
- `face_status.py`, `edge_status.py`, `edge_check.py`, `wire_cross.py`: why a
  saved face is invalid.
- `step_bad.py`, `brep_big.py`: invalid, zero-area or giant faces in a STEP.
- `render_views.py` / `render_at.py`: renders a .mmmp preview (face colours).
  Run with `xvfb-run -a`. Look at results the way the owner does.
- `checker_worst.py MESH STEP TOL`: the worst point of a result.
- `regions_at.py MESH TOL Q x,y,z`: which regions sit at a spot.

Tolerances used: mount 0.06917 (quality 0.7), rim 0.0443, tire 0.0879
(quality 0.6).

## Before giving the owner a build

1. Unit tests: `.venv/bin/python -m pytest -q desktop/tests`. Lint:
   `.venv/bin/python -m pyflakes desktop`.
2. Full suite compared against `results/r6_*` (fidelity, faces, validity,
   time), plus the owner's three files, rendered.
3. Push, wait for CI green on Linux, Windows and macOS
   (`.github/workflows/build.yml`), and give the owner the macOS artifact to
   test.
4. No merge or release without the owner's OK.

## Notes for the AI

- Don't wait on background jobs with `pgrep -f <pattern>` loops: the pattern
  matches the loop itself and never ends. Wait for a file or marker instead.
- The owner is in Brazil and uses a MacBook Air M4.
- Ask before decisions the owner might want to revise.
