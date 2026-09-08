"""One bounded CLI invocation at a time; no pipeline logic lives in the web UI."""
import atexit
import os
import subprocess
import sys
import threading
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

_lock = threading.Lock()
_process = None
_state = {"status": "idle"}


def _wait(process):
    try:
        stdout, stderr = process.communicate(timeout=900)
        code = process.returncode
        with _lock:
            if _process is process and _state["status"] == "running":
                err_msg = (stderr or "").strip()
                if len(err_msg) > 1000:
                    err_msg = err_msg[-1000:]
                _state.update(
                    status="completed" if code == 0 else "failed",
                    exit_code=code,
                    error=err_msg if code != 0 else "",
                )
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        with _lock:
            if _process is process:
                _state.update(status="timeout", error="A busca atingiu o limite de 15 minutos.")


@atexit.register
def _shutdown():
    if _process is not None and _process.poll() is None:
        _process.terminate()


@require_http_methods(["GET", "POST"])
def job(request):
    global _process
    with _lock:
        if request.method == "GET":
            return JsonResponse(_state.copy())
        if request.POST.get("action") == "cancel":
            if _process is not None and _process.poll() is None:
                _process.terminate()
                _state.update(status="cancelled")
            return JsonResponse(_state.copy())
        if _process is not None and _process.poll() is None:
            return JsonResponse({"error": "Já existe uma busca em andamento."}, status=409)
        try:
            count = int(request.POST.get("count", ""))
            if not 1 <= count <= 100:
                raise ValueError
        except ValueError:
            return JsonResponse({"error": "Escolha entre 1 e 100 leads."}, status=400)
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = "openoutreach.settings"
        env["OPENOUTREACH_DB"] = str(settings.DATABASE_PATH)
        args = [sys.executable, "-m", "openoutreach", "find", str(count)]
        emails = request.POST.get("emails") == "on"
        if emails:
            args.append("emails")
        try:
            _process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError:
            return JsonResponse({"error": "Não foi possível iniciar a busca."}, status=500)
        _state.clear()
        _state.update(status="running", count=count, emails=emails, started_at=time.time())
        threading.Thread(target=_wait, args=(_process,), daemon=True).start()
        return JsonResponse(_state.copy(), status=202)
