# Manifold Mesh Mix

A Mac app that converts 3D-printing meshes (**STL, 3MF, OBJ, PLY**) into **editable STEP files** made of true CAD surfaces: planes, cylinders, cones, spheres, tori and smooth NURBS surfaces. It never writes triangle facets, and every STEP file is opened again and checked after it is saved.

## Using it

1. Open **Manifold Mesh Mix** (in Applications).
2. Drop files or whole folders onto the window, or click the drop area to browse. You can also right-click a file in Finder and choose **Open With → Manifold Mesh Mix**.
3. Pick a **preset** (Auto is fine for almost everything) and set the **Speed ↔ Quality** slider.
4. Press **Convert** (⌘↩). The STEP files stay inside the app until you export them; nothing is saved into your folders yet.
5. Click a file in the queue to compare the **Mesh** and **STEP** views. The STEP view shows the body the way a CAD program does; the palette button gives every face its own color, so you can see how the surfaces were split up.
6. Press **Export…** (⌘E, or **Export All** ⇧⌘E). The export window shows the file names, where they go (by default a **STEP Files** folder next to each original) and whether existing files are replaced. If replace is off and a file with the same name is already there, the app asks whether to replace it or save under a new name.
7. A green **File checked** badge means the saved STEP file was read back and is a valid solid that matches the mesh. Otherwise the app lists each problem it found in plain words (for example gaps in the surface, a part that isn't a closed solid, or a volume that doesn't match the mesh).

### Presets

| Preset | Best for | What you get |
|---|---|---|
| **Auto** | Everything | Checks every part and picks CAD or Smooth for it. |
| **CAD** | Mechanical parts, brackets, enclosures, wheels | Planes, holes, cylinders, cones, fillets (tori) and spheres as exact CAD faces you can select, measure and edit. Anything else becomes fitted NURBS. |
| **Smooth** | Sculpts, figurines, organic shapes, scans | Large smooth NURBS surfaces fitted to the mesh. |
| **Precise** | When accuracy matters most | Like Auto, but with a tighter tolerance and more detail. |

### 3MF projects (Bambu Studio, OrcaSlicer, Snapmaker Orca, PrusaSlicer)

The app reads the actual parts from slicer project files:
- **Modifiers and support blockers/enforcers are skipped.** Only real printable parts are converted.
- **Copies on the plate are converted once**, in their original orientation and position (the same coordinates as the STL they came from). Scaling you applied in the slicer is kept.
- **Split to parts (on by default):** every part becomes its own STEP file, named *Project - Part.step*, like "Split to parts" in the slicer. The preview shows one tab per part, so you can look at each part's mesh and STEP separately. Turn it off (the **Split to parts** button above the preview, or **Split 3MF projects into parts** in the settings) to get one STEP file with all parts as named bodies.
- To keep the plate arrangement instead, set **Advanced → 3MF projects → Whole build plate** (this keeps all parts in one file).

### Speed ↔ Quality

Higher quality tightens the fitting tolerance and adds surface detail, but takes longer. The default (70%) leans toward quality. Very large sculpts (millions of triangles) take a minute or two.

## Building from source

Needs macOS 14 or newer on Apple silicon, the Xcode command line tools and Python 3.12.

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./build.sh            # builds dist/Manifold Mesh Mix.app (Swift app + bundled engine)
./build.sh --install  # also copies it to /Applications
```

- `engine/mmm/`: the Python conversion engine (OpenCASCADE via OCP). Run it directly with `.venv/bin/python engine/mmm_engine.py convert part.stl --preset auto`.
- `app/Sources/`: the SwiftUI app.
- `tests/`: test-model generator (`make_models.py`, creates `tests/models/`), test suite (`run_suite.py`) and debugging tools.

### How the engine works (short version)

- **CAD:** the mesh is segmented by region growing with robust, area-weighted fits of planes, cylinders, cones, spheres and tori. Simpler surface types are preferred, and fits are checked between mesh vertices, not just at them. Leftover areas are split into height-field patches and fitted with trimmed B-splines. Edges are fitted curves shared by both faces; wrap-around faces get proper seams and poles.
- **Smooth:** quad remesh (Instant Meshes, with a decimate → Manifold-clean → quad-split fallback), then Catmull–Clark-style bicubic patches (Loop–Schaefer ACC) least-squares fitted to the mesh. Regular patch areas are then merged into large B-spline faces.
- Every result is checked with OpenCASCADE's shape analyzer before it's written. The STEP file also stores each edge's 2D curve on its faces, so CAD programs rebuild every face exactly as it was checked (the test suite reads each file back and compares volumes).

## License

Copyright © 2026 Tuna-The-First and Claude (Anthropic's AI assistant).

Manifold Mesh Mix is free and open-source software, licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [LICENSE](LICENSE) for the full text.

In short: you may use, study, change and share it. If you give the app (or a changed version of it) to others, or run a changed version for others over a network, you must make its source code available to them under the same license. It comes with no warranty.

It bundles other open-source software; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

