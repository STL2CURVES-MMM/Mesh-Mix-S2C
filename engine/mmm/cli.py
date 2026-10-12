"""Command line interface.

    mmm-engine convert INPUT [-o OUTPUT] [--preset auto|exact|cad|smooth] ...
    mmm-engine inspect INPUT [--preview FILE]
"""
import argparse
import json
import os
import sys
import traceback

from .report import Reporter


def _inspect(args, rep):
    import numpy as np

    from . import stepio
    from .meshio import EdgeTopology, face_normals_areas, load_and_prepare

    bodies, info = load_and_prepare(args.input, repair="off", remove_debris=True, report=rep)
    d = info.as_dict()
    d["closed"] = info.open_shells == 0
    d["parts"] = info.parts
    # 3MF projects with several parts: one entry (and preview) per part
    part_list = []
    if info.parts > 1 and len(info.body_part) == len(bodies):
        for p, name in enumerate(info.part_names):
            entry = {"index": p, "name": name, "triangles": info.part_triangles[p], "bbox": info.part_bbox[p]}
            sel = [b for b, bp in zip(bodies, info.body_part) if bp == p]
            if args.part_previews and sel:
                os.makedirs(args.part_previews, exist_ok=True)
                path = os.path.join(args.part_previews, f"part-{p}.mmmp")
                Vs, Fs, off = [], [], 0
                for b in sel:
                    for s in b:
                        Vs.append(s.V)
                        Fs.append(s.F + off)
                        off += len(s.V)
                stepio.export_mesh_preview(np.vstack(Vs), np.vstack(Fs), path)
                entry["preview"] = path
            part_list.append(entry)
    d["part_list"] = part_list
    if args.preview:
        Vs, Fs, off = [], [], 0
        for b in bodies:
            for s in b:
                Vs.append(s.V)
                Fs.append(s.F + off)
                off += len(s.V)
        V = np.vstack(Vs)
        F = np.vstack(Fs)
        if len(F) > 1_500_000:
            sel = np.random.default_rng(0).choice(len(F), 1_500_000, replace=False)
            F = F[np.sort(sel)]
        stepio.export_mesh_preview(V, F, args.preview)
    rep.result(d)


def main(argv=None):
    p = argparse.ArgumentParser(prog="mmm-engine", description="Manifold Mesh Mix STL to STEP engine")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("convert", help="convert a mesh to STEP")
    c.add_argument("input")
    c.add_argument("-o", "--output")
    c.add_argument("--preset", default="auto", choices=["auto", "cad", "smooth", "precise", "exact"])
    c.add_argument("--repair", default="auto", choices=["off", "auto", "aggressive"])
    c.add_argument("--keep-debris", action="store_true")
    c.add_argument("--unit", default="mm", choices=["mm", "cm", "m", "in", "um", "ft"])
    c.add_argument("--tolerance", type=float, default=None, help="absolute fit tolerance (model units)")
    c.add_argument("--quality", "--detail", dest="quality", type=float, default=0.7, help="0 = fastest, 1 = best quality")
    c.add_argument("--layout", default="parts", choices=["parts", "plate"], help="3MF: one copy of each part, or the full build plate")
    c.add_argument("--max-faces", type=int, default=0)
    c.add_argument("--preview", default=None, help="write a preview file for the app")
    c.add_argument("--no-validate", action="store_true")
    c.add_argument("--split", action="store_true", help="3MF: write one STEP file per part")
    c.add_argument("--part-output", action="append", default=[], help="with --split: output file for part 1, 2, ... (repeat)")
    c.add_argument("--part-preview", action="append", default=[], help="with --split: preview file for part 1, 2, ... (repeat)")
    c.add_argument("--json", action="store_true", help="JSON-lines progress on stdout")

    i = sub.add_parser("inspect", help="report mesh statistics")
    i.add_argument("input")
    i.add_argument("--preview", default=None)
    i.add_argument("--part-previews", default=None, help="folder for one preview per 3MF part")
    i.add_argument("--json", action="store_true")

    sub.add_parser("version")
    sub.add_parser("selfcheck", help="report which optional components are available")

    args = p.parse_args(argv)
    if args.cmd is None:
        p.print_help()
        return 2
    if args.cmd == "version":
        from . import __version__

        print(__version__)
        return 0
    if args.cmd == "selfcheck":
        report = {}
        for mod in ("OCP", "numpy", "scipy", "scipy.interpolate", "trimesh", "pymeshfix", "fast_simplification", "manifold3d", "pynanoinstantmeshes"):
            try:
                m = __import__(mod, fromlist=["_"])
                report[mod] = getattr(m, "__version__", "ok")
            except Exception as e:
                report[mod] = f"MISSING: {e}"
        print(json.dumps(report, indent=1))
        return 0 if all(not str(v).startswith("MISSING") for v in report.values()) else 1

    json_mode = getattr(args, "json", False)
    stream = None
    if json_mode:
        # Keep stdout clean for JSON lines: anything else (including native
        # library chatter) is redirected to stderr at the file descriptor level.
        saved = os.dup(1)
        os.dup2(2, 1)
        stream = os.fdopen(saved, "w", buffering=1)
    rep = Reporter(json_mode=json_mode, stream=stream)
    try:
        if args.cmd == "inspect":
            _inspect(args, rep)
            return 0
        from .pipeline import Options, convert

        out = args.output or os.path.splitext(args.input)[0] + ".step"
        opts = Options(
            preset=args.preset,
            repair=args.repair,
            remove_debris=not args.keep_debris,
            unit=args.unit,
            tolerance=args.tolerance,
            quality=args.quality,
            layout=args.layout,
            max_faces=args.max_faces,
            preview=args.preview,
            validate=not args.no_validate,
            split=args.split,
            part_outputs=args.part_output,
            part_previews=args.part_preview,
        )
        res = convert(args.input, out, opts, rep)
        if not rep.json_mode:
            print(json.dumps({k: v for k, v in res.items() if k != "input"}, indent=1))
        return 0
    except Exception as e:  # report and exit non-zero
        import errno

        from .meshio import MeshError
        from .pipeline import ConversionError

        if isinstance(e, (MeshError, ConversionError)):
            msg = str(e)
        elif isinstance(e, MemoryError):
            msg = "Ran out of memory. Move the slider toward Faster, or convert fewer files at once (Advanced → Files at once)."
        elif isinstance(e, FileNotFoundError):
            msg = "The file couldn't be found. Was it moved, renamed or deleted?"
        elif isinstance(e, PermissionError):
            system = "macOS" if sys.platform == "darwin" else "Windows" if sys.platform == "win32" else "Linux"
            msg = f"{system} didn't allow reading this file or saving the STEP file in that folder. Choose another output folder in Save STEP files to."
        elif isinstance(e, OSError) and e.errno == errno.ENOSPC:
            msg = "The disk is full, so the STEP file couldn't be saved. Free up some space and convert again."
        elif "STEP write failed" in str(e) or "STEP transfer failed" in str(e):
            msg = "The STEP file couldn't be written. Check that the output folder still exists and has free space."
        else:
            msg = f"Something went wrong while converting ({e})." if str(e) else f"Something went wrong while converting ({type(e).__name__})."
        if rep.json_mode:
            rep.error(msg)
            sys.stderr.write(traceback.format_exc())
        else:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
