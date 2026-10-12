"""Stand-in for the conversion engine in tests: answers instantly with the
same JSON events. What it does depends on words in the input file name:

    broken  - can't be read (inspect fails)
    parts   - a project with two parts ("Left", "Right")
    crash   - convert ends without a result (exit code 3)
    error   - convert reports an error message and exits 1
    slow    - convert takes 60 s (for cancel tests)
    issues  - the check finds a problem
"""
import argparse
import json
import os
import struct
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def preview(path, offset=0.0):
    """A small valid MMMP file: one triangle and one edge."""
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    P = [0 + offset, 0, 0, 10 + offset, 0, 0, 0, 10, 5]
    N = [0, 0, 1] * 3
    with open(path, "wb") as fh:
        fh.write(b"MMMP")
        fh.write(struct.pack("<IIII", 1, 3, 1, 2))
        fh.write(struct.pack("<9f", *P))
        fh.write(struct.pack("<9f", *N))
        fh.write(bytes([200, 120, 60, 255] * 3))
        fh.write(struct.pack("<3I", 0, 1, 2))
        fh.write(struct.pack("<6f", 0, 0, 0, 10, 0, 0))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("inspect")
    i.add_argument("input")
    i.add_argument("--json", action="store_true")
    i.add_argument("--preview")
    i.add_argument("--part-previews")
    c = sub.add_parser("convert")
    c.add_argument("input")
    c.add_argument("-o", "--output")
    c.add_argument("--json", action="store_true")
    c.add_argument("--preview")
    c.add_argument("--split", action="store_true")
    c.add_argument("--part-output", action="append", default=[])
    c.add_argument("--part-preview", action="append", default=[])
    args, rest = ap.parse_known_args()
    name = os.path.basename(args.input).lower()
    if os.environ.get("FAKE_ENGINE_LOG"):
        with open(os.environ["FAKE_ENGINE_LOG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\n")
    if not os.path.exists(args.input):
        emit({"event": "error", "message": "FileNotFoundError: The file couldn't be found."})
        return 1
    parts = "parts" in name
    if args.cmd == "inspect":
        if "broken" in name:
            emit({"event": "error", "message": "MeshError: This file isn't a mesh the app can read."})
            return 1
        preview(args.preview)
        ev = {"event": "result", "triangles": 2_060_000 if "dense" in name else 1234, "bodies": 2 if parts else 1,
              "open_shells": 0,
              "parts": 2 if parts else 1, "bbox": [[0, 0, 0], [40, 30, 20.5]], "part_list": []}
        if parts:
            for k, nm in enumerate(["Left", "Right"]):
                p = os.path.join(args.part_previews, f"part-{k}.mmmp")
                preview(p, k * 20)
                ev["part_list"].append({"index": k, "name": nm, "triangles": 600 + k, "bbox": [[0, 0, 0], [10, 10, 10]],
                                        "preview": p})
        emit(ev)
        return 0
    emit({"event": "stage", "stage": "Reading the mesh", "progress": 0.05})
    emit({"event": "log", "message": f"args: {' '.join(rest)}"})
    if "slow" in name:
        for k in range(600):
            time.sleep(0.1)
            emit({"event": "progress", "stage": "Fitting", "progress": 0.05 + k / 700})
    if "crash" in name:
        return 3
    if "error" in name:
        emit({"event": "error", "message": "ConversionError: The mesh has no usable faces."})
        return 1
    emit({"event": "stage", "stage": "Writing STEP", "progress": 0.9})
    emit({"event": "warning", "message": "A small warning."})
    checks = [{"code": "gaps", "level": "warning", "message": "There is 1 open edge."}] if "issues" in name else []
    base = {"preset": "cad", "valid": True, "solids": 1, "shells": 1, "faces": 69, "seconds": 1.5,
            "volume": 100.0, "mesh_volume": 100.0, "tolerance": 0.05, "checked": True, "checks": checks}
    if args.split:
        for k, out in enumerate(args.part_output):
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(f"ISO-10303-21; part {k}\n")
            pv = args.part_preview[k] if k < len(args.part_preview) else None
            preview(pv, k * 20)
            emit({"event": "part", "part": k, "name": ["Left", "Right"][k % 2], "output": out, "preview": pv,
                  "file_size": os.path.getsize(out), **base})
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write("ISO-10303-21; fake\n")
    preview(args.preview)
    emit({"event": "result", "output": args.output, "file_size": os.path.getsize(args.output), **base})
    return 0


if __name__ == "__main__":
    sys.exit(main())
