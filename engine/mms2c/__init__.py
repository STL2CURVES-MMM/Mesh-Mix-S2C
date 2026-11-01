"""stl2curves: convert STL meshes and 3MF projects into solid STEP files with true
curved surfaces (see README.md). The command line is `stl2curves` (convert.main)."""

__version__ = "0.4.0"

import os as _os
import platform as _platform
import sys as _sys

BLAS_CORE = "Haswell"   # OpenBLAS kernel family every x86-64 PC with AVX2 runs alike (see _pin_blas)


def _has_avx2():
    """Whether this processor runs AVX2 code, found without loading numpy (Windows and Linux;
    elsewhere unknown, so False)."""
    try:
        if _sys.platform == "win32":
            import ctypes
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))     # (PF_AVX2_INSTRUCTIONS_AVAILABLE)
        if _sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("flags"):
                        flags = line.split()
                        return "avx2" in flags and "fma" in flags
    except Exception:
        pass
    return False


def _pin_blas():
    """Run numpy's and scipy's OpenBLAS on one kernel family on every PC.

    OpenBLAS picks a kernel built for the processor it finds (AVX-512 on a new laptop, AVX2 on
    an older desktop), and the kernels round the last digit differently. A last-digit
    difference is harmless, except where a decision sits right on its threshold: then the
    two machines decide it differently and the difference grows from there (stl2step's test13
    gave 106 faces on one machine and 94 on the other from the same code). The AVX2 kernels
    run on every x86-64 processor with AVX2, alike, so all of them give the same result. Set
    before numpy loads (it reads the setting once), and inherited by the worker processes.
    Not set over the user's own choice, nor when numpy is already loaded in this process: its
    workers must then run the kernel it runs. Macs use Apple's own library and can't be
    pinned this way."""
    if "OPENBLAS_CORETYPE" in _os.environ or "numpy" in _sys.modules:
        return
    if _platform.machine().lower() in ("amd64", "x86_64") and _has_avx2():
        _os.environ["OPENBLAS_CORETYPE"] = BLAS_CORE


_pin_blas()


def __getattr__(name):
    # stl_to_solid and friends live in convert, which loads OpenCascade: import it only
    # when asked, so `import mms2c` (a worker process, --version) stays light.
    if name in ("stl_to_solid", "write_step", "count", "volume", "main"):
        from . import convert
        return getattr(convert, name)
    raise AttributeError(f"module 'mms2c' has no attribute {name!r}")
