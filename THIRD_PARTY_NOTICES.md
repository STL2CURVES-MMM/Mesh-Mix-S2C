# Third-party software

Manifold Mesh Mix is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`).
The app bundles the following open-source software, each under its own license:

| Software | Used for | License |
|---|---|---|
| [Open CASCADE Technology](https://dev.opencascade.org/) | CAD geometry kernel, STEP reading and writing | LGPL-2.1 with the Open CASCADE exception |
| [OCP](https://github.com/CadQuery/OCP) (cadquery-ocp) | Python bindings for Open CASCADE | Apache-2.0 |
| [VTK](https://vtk.org/) | needed by OCP | BSD-3-Clause |
| [MeshFix](https://github.com/MarcoAttene/MeshFix-V2.1) via [PyMeshFix](https://github.com/pyvista/pymeshfix) | mesh repair | GPL-3.0 (MeshFix) / AGPL-3.0 (PyMeshFix) |
| [Manifold](https://github.com/elalish/manifold) (manifold3d) | mesh cleanup and simplification | Apache-2.0 |
| [Instant Meshes](https://github.com/wjakob/instant-meshes) via [pynanoinstantmeshes](https://pypi.org/project/pynanoinstantmeshes/) | quad remeshing for the Smooth preset | BSD-3-Clause |
| [fast-simplification](https://github.com/pyvista/fast-simplification) | mesh decimation | MIT |
| [trimesh](https://github.com/mikedh/trimesh) | mesh loading and processing | MIT |
| [Rtree](https://github.com/Toblerity/rtree) and libspatialindex | spatial lookups | MIT |
| [NumPy](https://numpy.org/) (with OpenBLAS) | math | BSD-3-Clause and others (see the NumPy license) |
| [SciPy](https://scipy.org/) | math, fitting | BSD-3-Clause |
| [Python](https://www.python.org/) | runtime for the conversion engine | Python Software Foundation License |
| [PyInstaller](https://pyinstaller.org/) | packaging the engine | GPL-2.0-or-later with the bootloader exception |

The full license texts are included with each package's source code at the links above.
