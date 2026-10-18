# Handoff: Manifold Mesh Mix engine work (October 2026)

Read `CLAUDE.md` first — it holds the owner's rules. The most important ones:

- Find and confirm the real cause before changing anything.
- Measure on the whole suite before and after every change.
- Ask the owner before anything they'd want a say in.
- Never merge to main or publish a release without the owner's approval.
- Give them the macOS CI build to test first.
- Never commit the owner's own model files.

---

## How to think and work on this project

This section is for every AI that picks this up. Read it before doing anything.

### Step back before every big move

Before writing any code, stop and ask:
- Do I actually understand the root cause, or am I guessing?
- Is there a simpler explanation I haven't checked?
- Is the fix I'm about to write model-agnostic — does it work on any STL, not
  just the one file that surfaced the bug?
- Is this the right level to fix the problem (segmenter vs assembler vs healer)?
- What's the smallest change that solves the confirmed cause?

Do this again after you have a fix working: step back and ask whether you've
actually solved the right thing or just patched a symptom.

At the start of each new problem area (not just each session), re-read the
relevant code before touching it. Never write a fix for code you haven't read.

### Diagnose first, code second

The pattern that worked this session:

1. **Reproduce:** run `bench.py` on the problem model, confirm the issue is real
   and measure it (face count, validity, max gap, volume).
2. **Isolate:** use `why_demoted2.py` to find which regions fail each round.
3. **Inspect:** save failing faces with `face_area.py`, examine with `face_status.py`,
   `edge_status.py`, `wire_cross.py`. Look at the actual numbers — UV coordinates,
   3D gap sizes, vertex positions.
4. **Understand the mechanism:** trace through the code to understand exactly why
   the numbers look the way they do. Write small Python scripts that re-run the
   relevant internal step and instrument it.
5. **Confirm the cause:** before writing a fix, you should be able to state
   "the problem is X because the measurement at step Y shows Z". If you can't
   state it that precisely, keep diagnosing.
6. **Write the minimal fix:** one targeted change that addresses the confirmed cause.
7. **Verify:** run `bench.py` on the problem file, then on the full 30-model suite.
   Compare with `bench_cmp.py`. Zero regressions is the bar.

Never skip steps. The previous agent who ran a rejected experiment (unconditional
border-tongue rule) skipped step 5 and it made things worse. The session that worked
spent most of its time in steps 2–5 before writing a single line.

### How to run tests

**Unit tests** (fast, run after every code change):
```bash
.venv/bin/python -m pytest -q desktop/tests
# Expected: 177 passed, 6-7 skipped, 0 errors
```

**Lint** (fast, run after every code change):
```bash
.venv/bin/python -m pyflakes desktop
# Expected: no output (clean)
```

**Single-model bench** (medium, run to check a specific problem file):
```bash
cp -r engine /tmp/engine_test  # always bench on a copy so you can keep editing
.venv/bin/python tools/diagnostics/bench.py /tmp/engine_test /tmp/result.jsonl \
  "$(pwd)/test files/owner problem files/sharps_mount_PETG_rev8.stl"
# Output: model name, face count, valid, ok, time
```

**Full suite** (slow, ~15-20 min, run before accepting any change):
```bash
# Generate models first if tests/models/ is missing:
.venv/bin/python tests/make_models.py

# Build the path list:
cat tools/diagnostics/results/suite8_models.txt | grep -v scratch \
  | sed "s|tests/models/|$(pwd)/tests/models/|" > /tmp/suite_paths.txt

# Run on two engine copies and compare:
cp -r engine /tmp/engine_before   # baseline (last verified commit)
cp -r engine /tmp/engine_after    # your change
.venv/bin/python tools/diagnostics/bench.py /tmp/engine_before /tmp/suite_before.jsonl \
  $(cat /tmp/suite_paths.txt)
.venv/bin/python tools/diagnostics/bench.py /tmp/engine_after /tmp/suite_after.jsonl \
  $(cat /tmp/suite_paths.txt)
.venv/bin/python tools/diagnostics/bench_cmp.py /tmp/suite_before.jsonl /tmp/suite_after.jsonl
```

**Important bench.py gotchas:**
- Always pass model paths as separate arguments — NOT via `$()` string expansion.
  `$(cat file)` with word splitting works; a single `"$(cat file)"` does not.
- Do NOT run bench in the background with `&`. The sandbox kills background processes.
  Run sequentially and wait for each to finish.
- `pgrep` doesn't work in the sandbox. Check progress by looking at the output file:
  `wc -l /tmp/result.jsonl` counts finished models.
- bench.py skips models already in the output file. Delete the file to re-run from scratch.

**What "zero regressions" means:** every model in `bench_cmp.py` output shows
`(same)`, or shows strictly better numbers (fewer faces, better within2, lower max
gap) with no model going from valid→invalid or ok→not-ok.

### What quality means for this project

This is a geometry engine that converts STL meshes to STEP/CAD. Quality means:

- **Valid:** the STEP output is topologically valid (BRepCheck_Analyzer passes).
  This is non-negotiable.
- **Fewer analytic faces is better** when they're correct CAD faces (planes,
  cylinders, tori). Freeform B-spline faces are a fallback — they're correct but
  ugly in a CAD viewer.
- **Lower max gap is better** — how far the output STEP deviates from the input mesh.
- **Volume accuracy** — within 0.5% of mesh volume is good.
- **Speed** matters less than quality. The owner said explicitly: quality and
  reliability over speed. A fix that adds 10 seconds but produces better faces
  is the right trade.

Any fix must work on any STL, not just the files that surfaced the bug. If you
find yourself writing code that checks for specific region numbers, specific
coordinates, or specific model names, stop — that's a model-specific hack.
The fix must be driven by geometric properties (gap size, wire validity, surface
fit quality) that are meaningful for any input.

### When to stop and ask the owner

Stop and ask before:
- Any design choice that trades one quality metric for another (e.g. "fewer faces
  but slightly larger gap").
- Any change to the segmenter's core region-assignment logic.
- Any change that affects more than ~3 files.
- Any change to how the app presents results to the user.
- Merging to main, publishing a release, or pushing to a shared branch.
- You've tried two different approaches to the same problem and both failed.

---

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
| `cd27158` | WIP: per-round ShapeFix_Face (superseded — do not use, see e3b004e) | reverted/superseded |
| `e3b004e` | **Fix disconnected face fragments** — `_absorb_isolated_fragments` absorbs triangles with no same-region neighbours after `split_pinched_regions` | **verified** — zero suite regressions, checker_worst identical to baseline |

---

## First thing to do next session

Run `why_demoted2.py` on the mount to see what's still invalid after both fixes:

```bash
.venv/bin/python tools/diagnostics/why_demoted2.py \
  "test files/owner problem files/sharps_mount_PETG_rev8.stl" 0.06917 0.7
```

The known remaining invalids after `e384dce` were: regions 16 (cylinder), 36 (torus — now fixed by e3b004e), 124 (plane), 198 (cylinder), 199 (torus), 201 (torus), 202 (cylinder), 203 (cylinder). Re-run to see what's left and which to tackle next.

---

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

**Current state (after e384dce + e3b004e):**
- Crossing-wire fix (`e384dce`): regions 16 and 202's spike triangles absorbed. First-round invalids reduced.
- Fragment fix (`e3b004e`): region 36's disconnected fragment absorbed. `ClassifyWires` now passes for region 36.

**What's still wrong:**
- Regions 16 (cylinder), 124 (plane), 198 (cylinder), 199 (torus), 201 (torus), 202 (cylinder), 203 (cylinder) were still invalid in round 1 at `e384dce`. Re-run `why_demoted2.py` to check current state.
- Some arm/handle-bend surfaces are still fragmented freeform patches.
- The notch where the arm meets the oval loop may still have a gap. Check with: `render_at.py <mmmp> <png> 0,15,16 40 1`

---

## Engine architecture (what was learned this session)

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
at `precision=tol*0.5`. This closes UV gaps smaller than tol/2 without modifying
3D curves. Only accept if the result is actually valid and not blown up.

The key diagnostic insight: the problem looks like a wire structure issue but the
mesh loops are perfectly connected. The gap is purely in the 2D pcurve representation.
Use `BRepAdaptor_Curve2d(e, face)` with orientation correction (reversed edges
traverse t1→t0) to see the actual UV continuity.

### Half-edge convention (important for any future fix in _absorb_*)

`nb[fi, edge]` is the neighbour triangle across the edge from `F[fi, edge]` to
`F[fi, (edge+1)%3]`. That edge has vertices `F[fi, edge]` and `F[fi, (edge+1)%3]`.
This is NOT "edge opposite vertex" — it's edge-FROM, not edge-opposite.

---

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

---

## Saved suite results

- `tools/diagnostics/results/r6_all.jsonl` — oldest baseline (before this work)
- `tools/diagnostics/results/r8_commit7318f28.jsonl` — 55-model Mac bench from earlier

---

## Before giving the owner a build

1. Unit tests pass: `.venv/bin/python -m pytest -q desktop/tests` (177 passed)
2. Lint clean: `.venv/bin/python -m pyflakes desktop`
3. Full suite vs `r6_all.jsonl` — fidelity, faces, validity, time all same or better
4. Owner files rendered — mount, rim, tire look right
5. Push branch, CI green on Linux/Windows/macOS
6. Give owner macOS artifact to test
7. Owner approves before merge

