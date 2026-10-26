import multiprocessing
import sys

if __name__ == "__main__":
    # The merged engine works in processes of its own: a pool of workers (multiprocessing,
    # which in the built app starts this program again; freeze_support hands those runs to
    # the pool), and rival conversions and judges, started as `python -m MODULE` from
    # source and as this program with --run-module in the built app (stl2curves/rivals.py)
    multiprocessing.freeze_support()
    if len(sys.argv) > 2 and sys.argv[1] == "--run-module":
        import runpy

        module = sys.argv[2]
        sys.argv = [module] + sys.argv[3:]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        sys.exit(0)
    from mmm.cli import main

    sys.exit(main())
