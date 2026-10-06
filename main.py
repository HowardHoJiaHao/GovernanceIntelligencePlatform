"""Run EGIP locally without Docker: backend API on :5001 and web frontend on :5000.

Usage:  uv run main.py
Needs a .env file (copy .env.example) and Ollama running for the AI features.
"""
import os
import signal
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def stop(process):
    """Stop a process and its children (venv launchers and the Flask reloader spawn some)."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True)
    elif process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait()


def main():
    load_dotenv(ROOT / '.env')
    env = os.environ.copy()
    # Local defaults; values from .env or the shell win
    env.setdefault('BACKEND_PORT', '5001')
    env.setdefault('FRONTEND_PORT', '5000')
    if not env.get('BACKEND_URL') or 'backend:' in env['BACKEND_URL']:
        env['BACKEND_URL'] = f"http://127.0.0.1:{env['BACKEND_PORT']}"
    # "localhost" makes Windows try IPv6 first, which adds ~2 s to every request
    # to the backend and to Ollama (both only listen on IPv4)
    for name in ('BACKEND_URL', 'OLLAMA_HOST'):
        if env.get(name):
            env[name] = env[name].replace('://localhost', '://127.0.0.1')
    env.setdefault('PYTHONUNBUFFERED', '1')
    # The backend logs emoji; Windows pipes default to cp1252 and would crash on them
    env.setdefault('PYTHONUTF8', '1')

    # Own process group so stop() can take down the Flask reloader's child process too
    group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    processes = [
        subprocess.Popen([sys.executable, 'api.py'], cwd=ROOT / 'backend', env=env, **group),
        subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT / 'frontend', env=env, **group),
    ]
    print(f"EGIP running: open http://localhost:{env['FRONTEND_PORT']}  (Ctrl+C to stop)", flush=True)
    crashed = None
    try:
        while crashed is None:
            for name, p in zip(('Backend', 'Frontend'), processes):
                if p.poll() is not None:
                    crashed = (name, p.returncode)
            try:
                processes[0].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        for p in processes:
            stop(p)
    if crashed:
        print(f"{crashed[0]} stopped unexpectedly (exit code {crashed[1]}); see the log above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
