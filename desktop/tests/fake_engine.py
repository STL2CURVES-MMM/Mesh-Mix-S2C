"""Stand-in for the conversion engine in tests: answers instantly with the
same JSON events. What it does depends on words in the input file name:

    broken  - can't be read (inspect fails)
    parts   - a project with two parts ("Left", "Right")
    crash   - convert ends without a result (exit code 3)
    error   - convert reports an error message and exits 1
    slow    - convert takes 60 s (for cancel tests)
    issues  - the check finds a problem
    holes   - the mesh check fixes holes and flipped triangles
    overlap - the mesh check finds overlapping pieces (merged with --merge-overlaps)
    mergefail - merging the overlapping pieces fails
    touching  - a project whose two parts touch each other (one part after
                --touching-parts merge)
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


def issue(code, status, problem, result, count=1):
    return {"code": code, "count": count, "status": status, "problem": problem, "result": result}


def mesh_check(name, merge, touching="ask"):
    """The mesh check report for this test file."""
    issues, status, ready, problems = [], "ok", True, []
    if "touching" in name:
        merged = touching == "merge"
        issues.append(issue("touching_parts", "fixed" if merged else "note" if touching == "split" else "action",
                            "2 parts touch each other (Left, Right)", "Merged them into one solid." if merged else "Kept.", 2))
        status = "fixed" if merged else "ok" if touching == "split" else "action"
    if "holes" in name:
        issues += [issue("holes", "fixed", "2 holes in the surface", "Filled them.", 2),
                   issue("flipped", "fixed", "3 triangles facing the wrong way", "Turned them around.", 3)]
        status = "fixed"
    if "overlap" in name:
        if merge:
            issues.append(issue("overlaps", "fixed", "2 pieces overlapping each other", "Merged them into one solid.", 2))
            status = "fixed" if status == "ok" else status
        else:
            issues.append(issue("overlaps", "action", "2 pieces overlapping each other",
                                "Kept as separate solids. Use “Merge overlapping pieces” to join them into one.", 2))
            status = "action"
    if "crossing" in name:
        issues.append(issue("self_intersections", "not_fixed", "the surface passes through itself in 4 places",
                            "They couldn't be removed without changing the shape.", 4))
        status, ready, problems = "problems", False, ["the surface still passes through itself in 4 places"]
    return {"status": status, "issues": issues, "verified": {"ready": ready, "problems": problems}}


def _log_line(path, line):
    """Append one line to the shared log. Two engines started together
    (Convert all) append at once, and on Windows one line could overwrite
    the other: a lock file lets one write at a time."""
    lock = path + ".lock"
    for _ in range(500):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.01)
    else:
        fd = None  # (a lock left behind: write anyway)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    finally:
        if fd is not None:
            os.close(fd)
            os.remove(lock)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("inspect")
    i.add_argument("input")
    i.add_argument("--json", action="store_true")
    i.add_argument("--preview")
    i.add_argument("--part-previews")
    i.add_argument("--merge-overlaps", action="store_true")
    i.add_argument("--touching-parts", default="ask")
    i.add_argument("--keep-debris", action="store_true")
    x = sub.add_parser("fix")
    x.add_argument("input")
    x.add_argument("-o", "--output")
    x.add_argument("--json", action="store_true")
    x.add_argument("--merge-overlaps", action="store_true")
    c = sub.add_parser("convert")
    c.add_argument("input")
    c.add_argument("-o", "--output")
    c.add_argument("--json", action="store_true")
    c.add_argument("--preview")
    c.add_argument("--split", action="store_true")
    c.add_argument("--part-output", action="append", default=[])
    c.add_argument("--part-preview", action="append", default=[])
    c.add_argument("--merge-overlaps", action="store_true")
    c.add_argument("--touching-parts", default="ask")
    args, rest = ap.parse_known_args()
    name = os.path.basename(args.input).lower()
    if os.environ.get("FAKE_ENGINE_LOG"):
        _log_line(os.environ["FAKE_ENGINE_LOG"], json.dumps(sys.argv[1:]))
    if not os.path.exists(args.input):
        emit({"event": "error", "message": "FileNotFoundError: The file couldn't be found."})
        return 1
    touching = "touching" in name
    merged = touching and getattr(args, "touching_parts", "ask") == "merge"
    parts = ("parts" in name or touching) and not merged
    if args.cmd == "fix":
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write("solid fixed\nendsolid fixed\n")
        emit({"event": "result", "output": args.output, "file_size": os.path.getsize(args.output),
              "mesh_check": mesh_check(name, args.merge_overlaps, getattr(args, "touching_parts", "ask"))})
        return 0
    if args.cmd == "inspect":
        if "mergefail" in name and args.merge_overlaps:
            emit({"event": "error", "message": "The pieces couldn't be joined.", "reason": "A test failure."})
            return 1
        if "broken" in name:
            emit({"event": "error", "message": "MeshError: This file isn't a mesh the app can read."})
            return 1
        emit({"event": "stage", "stage": "Reading the file", "progress": 0.0})
        emit({"event": "stage", "stage": "Looking for hidden internal walls", "progress": 0.3})
        if "slowread" in name:
            for k in range(100):
                time.sleep(0.05)
                emit({"event": "progress", "stage": "Looking for hidden internal walls", "progress": 0.3 + k / 200})
        preview(args.preview)
        ev = {"event": "result", "triangles": 2_060_000 if "dense" in name else 1234, "bodies": 2 if parts else 1,
              "open_shells": 0,
              "parts": 2 if parts else 1, "bbox": [[0, 0, 0], [40, 30, 20.5]], "part_list": [],
              "mesh_check": mesh_check(name, args.merge_overlaps, getattr(args, "touching_parts", "ask"))}
        if touching:
            ev["touching"] = [{"parts": [0, 1], "names": ["Left", "Right"]}]
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
        emit({"event": "error", "message": "ConversionError: The mesh has no usable faces.",
              "reason": "The file itself is the problem, not the app.",
              "details": f"ConversionError: no faces\nTraceback: File \"{os.path.abspath(args.input)}\", line 1"})
        return 1
    emit({"event": "stage", "stage": "Writing STEP", "progress": 0.9})
    emit({"event": "warning", "message": "A small warning."})
    checks = [{"code": "gaps", "level": "warning", "message": "There is 1 open edge."}] if "issues" in name else []
    base = {"preset": "cad", "valid": True, "solids": 1, "shells": 1, "faces": 69, "seconds": 1.5,
            "volume": 100.0, "mesh_volume": 100.0, "tolerance": 0.05, "checked": True, "checks": checks,
            "fidelity": {"ok": "rough" not in name, "tolerance": 0.05, "within": 0.99, "within2": 0.998,
                         "p99": 0.04 if "rough" not in name else 0.4, "max": 0.12 if "rough" not in name else 1.3,
                         "wrong_way": 0.001}}
    if "repaired" in name:
        base["repaired"] = ["read back and saved again"]

    def alternative(out, pv):
        """No method followed the mesh: a valid second version to choose from."""
        if "twoways" not in name:
            return {}
        alt = os.path.splitext(out)[0] + ".valid.step"
        with open(alt, "w", encoding="utf-8") as fh:
            fh.write("ISO-10303-21; valid version\n")
        alt_pv = os.path.splitext(pv)[0] + ".valid.mmmp" if pv else None
        preview(alt_pv, 50)
        return {"alternative": {"output": alt, "preview": alt_pv, "valid": True, "checked": True, "checks": [],
                                "solids": 1, "shells": 1, "faces": 420, "file_size": os.path.getsize(alt),
                                "fidelity": {"ok": False, "tolerance": 0.05, "within": 0.85, "within2": 0.9,
                                             "p99": 0.6, "max": 1.5, "wrong_way": 0.002}}}

    if "twoways" in name:
        base["valid"] = False
        base["checks"] = [{"code": "bad_faces", "level": "error", "message": "2 faces have errors."}]
    if args.split:
        for k, out in enumerate(args.part_output):
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(f"ISO-10303-21; part {k}\n")
            pv = args.part_preview[k] if k < len(args.part_preview) else None
            preview(pv, k * 20)
            emit({"event": "part", "part": k, "name": ["Left", "Right"][k % 2], "output": out, "preview": pv,
                  "file_size": os.path.getsize(out), **base, **(alternative(out, pv) if k == 1 else {})})
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write("ISO-10303-21; fake\n")
    preview(args.preview)
    emit({"event": "result", "output": args.output, "file_size": os.path.getsize(args.output), **base,
          **({} if args.split else alternative(args.output, args.preview)),
          "mesh_check": mesh_check(name, args.merge_overlaps, getattr(args, "touching_parts", "ask"))})
    return 0


if __name__ == "__main__":
    sys.exit(main())
