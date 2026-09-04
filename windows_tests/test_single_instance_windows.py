"""
Windows-only: verify the named-mutex single-instance guard.
Run on Windows from the project folder:  python windows_tests\test_single_instance_windows.py

It imports main.acquire_single_instance() twice in separate processes and
asserts the second acquisition is refused. It never launches the GUI.
"""
import subprocess, sys, os, textwrap

CHILD = textwrap.dedent(r"""
    import sys, os, time
    sys.path.insert(0, os.path.abspath('.'))
    import mtgdb.main as main

    got = main.acquire_single_instance()
    print("ACQUIRED" if got else "REFUSED", flush=True)
    if got:
        time.sleep(5)          # hold the mutex while the second process tries
        main.release_single_instance()
""")

def main():
    if sys.platform != "win32":
        print("SKIP: single-instance guard is Windows-only.")
        return 0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    p1 = subprocess.Popen([sys.executable, "-c", CHILD], cwd=root, env=env,
                          stdout=subprocess.PIPE, text=True)
    # give p1 time to acquire
    import time; time.sleep(1.5)
    p2 = subprocess.run([sys.executable, "-c", CHILD], cwd=root, env=env,
                        stdout=subprocess.PIPE, text=True, timeout=30)
    first = p1.stdout.readline().strip()
    second = p2.stdout.strip()
    p1.wait(timeout=30)
    ok = first == "ACQUIRED" and second == "REFUSED"
    print(f"  first process : {first}")
    print(f"  second process: {second}")
    print("SINGLE-INSTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
