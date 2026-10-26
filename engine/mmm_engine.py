import multiprocessing
import os
import sys
import threading
import time


def _windows_job():
    """Puts this process in a Windows job object that ends every process in it when its
    last handle closes, which is when this process ends, however it ends: the apps stop
    a conversion by ending the engine (TerminateProcess), which alone would leave its
    rival conversions and worker processes running. Children join the job as they start.
    Returns the handle, kept for the process's life (None if it couldn't be made)."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # extended limits
    if not ok or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        return None
    return job


def _end_with_parent():
    """Make sure no engine process outlives the one that started it."""
    if os.name == "nt":
        try:
            return _windows_job()
        except Exception:
            return None
    import signal

    # the apps ask the engine to stop with SIGTERM: as a normal exit, the conversion's
    # clean-up runs, which stops its rivals and worker processes (SIGKILL follows 2 s later)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parent = os.getppid()

    def watch():
        # (an engine whose parent is gone has no one to report to: a rival whose engine
        # was killed outright, or an engine whose app crashed)
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch, daemon=True).start()
    return None


if __name__ == "__main__":
    # The merged engine works in processes of its own: a pool of workers (multiprocessing,
    # which in the built app starts this program again; freeze_support hands those runs to
    # the pool), and rival conversions and judges, which start as this program with
    # --run-module MODULE (stl2curves/rivals.py)
    multiprocessing.freeze_support()
    _job = _end_with_parent()
    if len(sys.argv) > 2 and sys.argv[1] == "--run-module":
        import runpy

        module = sys.argv[2]
        sys.argv = [module] + sys.argv[3:]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        sys.exit(0)
    from mmm.cli import main

    sys.exit(main())
