"""Isolated Playwright smoke test using a temporary PostgreSQL database."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from tests.postgres import temporary_database

with temporary_database() as storage:
    env=dict(os.environ,DATABASE_URL=storage.url,TINVEST_SANDBOX_TOKEN='')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    server=subprocess.Popen([sys.executable,'-m','uvicorn','kontur.api:app','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ready',timeout=1)
                break
            except Exception:
                if server.poll() is not None:raise RuntimeError(server.stderr.read().decode())
                time.sleep(.1)
        else:raise RuntimeError('Server did not become ready')
        result=subprocess.run(['node','tests/browser.cjs'],cwd=root,env=dict(env,BASE_URL=f'http://127.0.0.1:{port}'),timeout=120)
    finally:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill()
    sys.exit(result.returncode)
