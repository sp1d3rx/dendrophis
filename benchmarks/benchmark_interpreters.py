import os
import subprocess
import time


def run_benchmark(cmd, name):
    print(f"Running benchmark: {name}...")
    # Warm up
    subprocess.run([*cmd, "--version"], capture_output=True)

    start_time = time.perf_counter()
    # We use a non-interactive command that exercise some logic
    # --list-sessions is good because it imports most of the core and does some IO/JSON
    result = subprocess.run([*cmd, "--list-sessions"], capture_output=True, text=True)
    end_time = time.perf_counter()

    return {
        "name": name,
        "duration": end_time - start_time,
        "status": "success" if result.returncode == 0 else "failed",
    }


def main():
    benchmarks = [
        ([".venv/bin/python", "-m", "dendrophis"], "CPython 3.13"),
        ([".venv/bin/python", "-X", "jit", "-m", "dendrophis"], "CPython 3.13 JIT"),
        ([".venv_pypy/bin/python", "-m", "dendrophis"], "PyPy 3.11"),
    ]

    results = []
    for cmd, name in benchmarks:
        if os.path.exists(cmd[0]):
            results.append(run_benchmark(cmd, name))
        else:
            print(f"Skipping {name}: {cmd[0]} not found")

    print("\n" + "=" * 40)
    print(f"{'Interpreter':<20} | {'Time (s)':<10}")
    print("-" * 40)
    for r in sorted(results, key=lambda x: x["duration"]):
        print(f"{r['name']:<20} | {r['duration']:>10.4f}s")
    print("=" * 40)


if __name__ == "__main__":
    main()
