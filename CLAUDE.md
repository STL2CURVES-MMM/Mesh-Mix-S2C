# Working on Manifold Mesh Mix

Instructions for every coding session on this project, from its owner.

## Find the real problem before fixing it

- Spending some extra time and credits making sure you know what the problem
  actually is beats spending a lot more building something that doesn't work
  or is pointless because the problem was something else.
- Before a major change, confirm the cause with evidence: reproduce it,
  measure it, and look at the actual spot (the worst point, the failing face,
  the log line). Don't build on a guess.
- Check your measuring tools before you trust them. A checker, test or
  benchmark can be wrong too: spot-check its numbers against an independent
  method (an exact or brute-force calculation, another library, the saved
  file read back) before tuning the engine on them. Example from 1.5.x: the
  shape checker's own errors (coarse tessellation of faces with a loose
  tolerance, an approximate nearest-triangle search) made correct faces look
  wrong, and rules tuned on those numbers broke good conversions.
- Every so often, and always before a big change, step back and ask whether
  this is the best way to do it, not just the next patch.
- Different kinds of models need different rules (clean CAD exports, coarse
  exports such as OpenSCAD, noisy scans, organic or sculpted models). Tune
  each one on its own data instead of one rule for everything.
- Plans put a check step before every big change: measure first (read-only
  experiments on cases with known answers), then build only what the data
  shows is needed, then measure again against the previous results.
- At each checkpoint, report the results and keep going, unless something is
  wrong or urgent, or a decision is the owner's to make: then stop, ask, and
  wait for the answer.

## How the engine checks its work

- Different checking methods are good at different problems. Run the fast
  one first; give its verdict a certainty score (low when the result is near
  a limit or the mesh is noisy); when certainty is low, a second, finer
  check decides. Each kind of mesh (clean, coarse, scan: see
  `engine/mmm/fidelity.py`, `mesh_kind`) has its own rules, switched on by
  measurements of the mesh itself, never by guesses.
- The same goes for converting: Auto's first choice runs by default; when
  its result passes with low certainty, the other method runs too and the
  better result is kept.
- Tell the app's user at every stage what is happening and why (a second
  check, another method being tried), so they know why a file takes longer.

## Verify before saying it works

- Compare against the previous release on the whole model suite
  (`tests/make_models.py` makes the models), not just the files that
  prompted the change: fidelity, face count, validity and time.
- Look at results the way the owner will (the STEP read back, a render),
  and include the owner's own problem files.
- Don't call something fixed until it was measured and checked.

## Ask first

- Ask before anything the owner might want to know about, disagree with or
  revise, including design choices made along the way.
- Ask before merging into main or publishing a release. Before a public
  release, give the owner the macOS build from CI to test first.
- Never push empty commits. Don't add the owner's own model files to the
  repository.

## Checks

- Unit tests: `.venv/bin/python -m pytest -q desktop/tests`
- Lint: `.venv/bin/python -m pyflakes desktop`
- CI (`.github/workflows/build.yml`) builds and tests Linux, Windows and
  macOS on every push.
