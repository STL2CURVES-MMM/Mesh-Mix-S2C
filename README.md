# Manifold Mesh Mix

An app for **macOS, Windows and Linux** that converts 3D-printing meshes (**STL, 3MF, OBJ, PLY**) into **editable STEP files** made of true CAD surfaces: planes, cylinders, cones, spheres, tori and smooth NURBS surfaces. It never writes triangle facets, and every STEP file is opened again and checked after it is saved.

## Download

Get the newest version from the [Releases page](https://github.com/TUNA-THE-FIRST/Manifold-Mesh-Mix/releases/latest):

| System | File | Notes |
|---|---|---|
| **macOS** 14+ (Apple silicon) | `ManifoldMeshMix-<version>-macOS-arm64.zip` | Unzip and drag **Manifold Mesh Mix** into Applications. The app isn't notarized, so the first time, right-click it and choose **Open**. |
| **Windows** 10/11 (64-bit) | `ManifoldMeshMix-<version>-Windows-x64-Setup.exe` | Installs just for your user account (no administrator password). Windows SmartScreen may warn about an unknown publisher the first time: click **More info → Run anyway**. |
| **Linux** (x86-64; Ubuntu 22.04, Fedora 36, Debian 12 or newer) | `ManifoldMeshMix-<version>-Linux-x86_64.AppImage` | Make it executable (`chmod +x`, or Properties → Permissions) and double-click it. If it doesn't start, install `libfuse2` (Ubuntu: `sudo apt install libfuse2`). **File → Add to Applications Menu** puts it in your app menu and in "Open With" for meshes. |

### Updates

Every version can update itself. Click the **⬇ button** next to the app name (or **Check for Updates…** in the menu or in Preferences/Settings). The app looks up the newest release on GitHub, downloads the file for your system, checks it against the release's checksums, installs it and opens again. A few seconds after opening, the app also checks once by itself and shows **Version X is available** under the app name; it never installs anything without a click (turn the check off in Preferences/Settings).

- **Windows:** the installer runs silently and the app restarts.
- **Linux:** the AppImage replaces itself (the folder it is in must be writable) and restarts.
- **macOS:** the app replaces itself in its folder (normally Applications) and restarts. If macOS asks, allow Manifold Mesh Mix under **System Settings → Privacy & Security → App Management**.

## Using it

1. Open **Manifold Mesh Mix** (Applications on the Mac, the Start menu on Windows, the AppImage on Linux).
2. Drop files or whole folders onto the window, or click the drop area to browse. You can also right-click a file and choose **Open With → Manifold Mesh Mix** (Finder on the Mac, Explorer on Windows).
3. Pick a **preset** (Auto is fine for almost everything) and set the **Speed ↔ Quality** slider.
4. Press **Convert** (⌘↩ on the Mac, Ctrl+Enter on Windows/Linux). The STEP files stay inside the app until you export them; nothing is saved into your folders yet.
5. Click a file in the queue to compare the **Mesh** and **STEP** views. The STEP view shows the body the way a CAD program does; the palette button gives every face its own color, so you can see how the surfaces were split up.
6. Press **Export…** (⌘E / Ctrl+E, or **Export All** ⇧⌘E / Ctrl+Shift+E). The export window shows the file names, where they go (by default a **STEP Files** folder next to each original) and whether existing files are replaced. If replace is off and a file with the same name is already there, the app asks whether to replace it or save under a new name.
7. A green **File checked** badge means the saved STEP file was read back and is a valid solid that matches the mesh. Otherwise the app lists each problem it found in plain words (for example gaps in the surface, a part that isn't a closed solid, or a volume that doesn't match the mesh).
   **Automatic repair.** If reading the file back finds small errors (faces whose edges don't line up, small gaps, a solid whose surfaces cross), the app repairs the file the way CAD programs like Fusion do when they import one, and checks it again. Then the badge says **Repaired automatically**, so the file is clean in every program, not just in CAD programs that heal files themselves. The repair only runs when there is a problem, and a repaired version is kept only if it reads back better with the same shape and volume. If a part still has errors the repair can't fix, the app converts just that part again another way: smooth surfaces instead of CAD faces, CAD faces instead of smooth surfaces for mechanical parts, or denser smooth surfaces for organic ones. It uses the new version only if that checks out clean. It tells you when it did. Problems in the mesh itself (holes, missing pieces) can't be repaired this way and are still listed.

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

**Best quality keeps fine detail.** In the *Best quality* part of the slider (85% and up), organic shapes get more surfaces the denser the mesh is, so sculpted texture like cracks, scales and tool marks survives. At the far right the surfaces stay within a few hundredths of a millimeter of a typical sculpt. Dense meshes then make much bigger STEP files and take a lot longer: a 2-million-triangle bust took about 20 minutes and made a 130 MB file, instead of about 8 minutes and 25 MB. When that's about to happen, a warning under the slider gives the expected file size. It needs a few GB of memory. A **Smooth surface limit** (Advanced) turns it off.

## Building from source

All three versions share the conversion engine in `engine/`. The version number lives in `VERSION`.

### macOS

Needs macOS 14 or newer on Apple silicon, the Xcode command line tools and Python 3.12.

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./build.sh            # builds dist/Manifold Mesh Mix.app (Swift app + bundled engine)
./build.sh --install  # also copies it to /Applications
./build.sh --zip      # also makes the release download
```

### Windows and Linux

One Qt (PySide6) app in `desktop/` builds both. Needs Python 3.12; Windows also needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the installer.

```
python3.12 -m venv .venv                       # Windows: py -3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r desktop/requirements.txt
.venv/bin/python desktop/build.py               # Windows: .venv\Scripts\python desktop\build.py
```

This builds `dist-desktop/ManifoldMeshMix/` (the app with its engine) and the installer (Windows) or AppImage (Linux). To run the app straight from the source code: `.venv/bin/python -m mmm_app` inside `desktop/`.

### Releases

Pushing a tag such as `v1.0.1` (matching `VERSION`) makes GitHub Actions build and test all three versions and publish a release with their downloads and `SHA256SUMS.txt`, which the apps' updaters use. Every push runs the same builds and tests (`.github/workflows/build.yml`).

### Where things are

- `engine/mmm/`: the Python conversion engine (OpenCASCADE via OCP). Run it directly with `.venv/bin/python engine/mmm_engine.py convert part.stl --preset auto`.
- `app/Sources/`: the SwiftUI Mac app. `app/Tests/update_test.py` tests its self-update.
- `desktop/mmm_app/`: the Windows/Linux app; `desktop/packaging/`: its installer and AppImage files; `desktop/tests/`: unit tests, the scripted self-test checker and update tests.
- `tests/`: test-model generator (`make_models.py`, creates `tests/models/`), test suite (`run_suite.py`, with `render.py` for its pictures) and `measure_detail.py`, which measures how closely a STEP file follows its mesh.

### How the engine works (short version)

- **CAD:** the mesh is segmented by region growing with robust, area-weighted fits of planes, cylinders, cones, spheres and tori. Simpler surface types are preferred, and fits are checked between mesh vertices, not just at them. Leftover areas are split into height-field patches and fitted with trimmed B-splines. Edges are fitted curves shared by both faces; wrap-around faces get proper seams and poles.
- **Smooth:** quad remesh (Instant Meshes, with a decimate → Manifold-clean → quad-split fallback), then Catmull–Clark-style bicubic patches (Loop–Schaefer ACC) least-squares fitted to the mesh. Regular patch areas are then merged into large B-spline faces.
- Every result is checked with OpenCASCADE's shape analyzer before it's written, and the saved file is read back and checked again. When that finds problems, `engine/mmm/repair.py` opens the saved file and saves it again (as a CAD program would), then tries shape healing and sewing, keeping each step only if the file reads back better with the same volume. If problems remain, the broken parts are converted again another way (`convert_alt` in `pipeline.py`). The STEP file also stores each edge's 2D curve on its faces, so CAD programs rebuild every face exactly as it was checked (the test suite reads each file back and compares volumes).

## License

Copyright © 2026 Tuna-The-First and Claude (Anthropic's AI assistant).

Manifold Mesh Mix is free and open-source software, licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [LICENSE](LICENSE) for the full text.

In short: you may use, study, change and share it. If you give the app (or a changed version of it) to others, or run a changed version for others over a network, you must make its source code available to them under the same license. It comes with no warranty.

It bundles other open-source software; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

