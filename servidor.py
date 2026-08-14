"""
Servidor en la nube (Render) del dashboard de Energia No Generada (ENG) —
modelo de "empuje" (push).

Este servidor NO lee Excel ni OneDrive. Se queda esperando que la
laptop del usuario (que si tiene el Excel a mano, sin problemas de
permisos) le envie los datos ya calculados por POST /actualizar, y
simplemente los guarda en memoria y se los muestra a quien pida la
pagina. Asi el dashboard esta disponible para todo el equipo aunque la
laptop este apagada en ese momento — solo que mientras este apagada,
lo que se ve es la ultima version que si se alcanzo a enviar.

Variables de entorno esperadas (se configuran en Render):
  PUSH_TOKEN  -> clave secreta que debe coincidir con la que manda la
                  laptop en el header 'X-Auth-Token'. Obligatoria.
  PORT        -> puerto en el que escuchar (Render lo define solo).
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
PLANTILLA_PATH = CARPETA / "plantilla.html"
PUERTO = int(os.environ.get("PORT", "8765"))
PUSH_TOKEN = os.environ.get("PUSH_TOKEN", "").strip()

_lock = threading.Lock()
_ultimo_payload = None       # dict ya calculado (kpis, registros, etc.)
_ultimo_recibido_en = None   # datetime UTC de cuando llego el ultimo push

PAGINA_ESPERA = """<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<title>Dashboard ENG — esperando datos</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#f9f9f7; color:#0b0b0b; padding:40px; }}
  .box {{ max-width:560px; margin:60px auto; background:#fff; border:1px solid #e1e0d9;
          border-radius:12px; padding:28px 32px; text-align:center; }}
  h1 {{ font-size:18px; }}
  p {{ color:#52514e; font-size:14px; line-height:1.6; }}
</style></head>
<body><div class="box">
  <h1>Todavía no ha llegado ningún dato</h1>
  <p>Este dashboard muestra lo último que le haya enviado el servidor local de la
     planta. Abre el dashboard local (<code>iniciar_dashboard.bat</code>) para que
     haga el primer envío — después de eso, esta página se actualiza sola.</p>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[servidor] " + (fmt % args) + "\n")

    def _enviar(self, status, body_bytes, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/healthz":
            self._enviar(200, b"ok", "text/plain")
            return

        with _lock:
            payload = _ultimo_payload
            recibido_en = _ultimo_recibido_en

        if payload is None:
            self._enviar(200, PAGINA_ESPERA.encode("utf-8"))
            return

        try:
            datos_json = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
            plantilla = PLANTILLA_PATH.read_text(encoding="utf-8")
            html = plantilla.replace("__DATOS_JSON__", datos_json)
            aviso = (
                '<div style="position:sticky;top:0;background:#e7f0fb;color:#0b0b0b;'
                'padding:6px 16px;font:12px system-ui,sans-serif;text-align:center;z-index:99">'
                f"Última vez que la laptop envió datos: {recibido_en.strftime('%Y-%m-%d %H:%M UTC')}</div>"
            )
            html = html.replace("<body>", "<body>" + aviso, 1)
            self._enviar(200, html.encode("utf-8"))
        except Exception as exc:
            self._enviar(500, f"Error mostrando el dashboard: {exc}".encode("utf-8"), "text/plain")

    def do_POST(self):
        global _ultimo_payload, _ultimo_recibido_en

        if self.path != "/actualizar":
            self._enviar(404, b"not found", "text/plain")
            return

        if not PUSH_TOKEN:
            self._enviar(500, b"El servidor no tiene PUSH_TOKEN configurado.", "text/plain")
            return

        token = self.headers.get("X-Auth-Token", "")
        if token != PUSH_TOKEN:
            self._enviar(401, b"Token invalido.", "text/plain")
            return

        try:
            largo = int(self.headers.get("Content-Length", "0"))
            if largo <= 0 or largo > 20_000_000:
                raise ValueError("tamaño de envío inválido")
            cuerpo = self.rfile.read(largo)
            datos = json.loads(cuerpo)
            if "kpis" not in datos or "registros" not in datos:
                raise ValueError("el JSON recibido no tiene la forma esperada (faltan 'kpis' o 'registros')")
        except Exception as exc:
            self._enviar(400, f"Envío inválido: {exc}".encode("utf-8"), "text/plain")
            return

        with _lock:
            _ultimo_payload = datos
            _ultimo_recibido_en = datetime.now(timezone.utc)

        self._enviar(200, b'{"ok": true}', "application/json")


class ServidorHilos(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    if not PUSH_TOKEN:
        print("ATENCION: no hay PUSH_TOKEN configurado como variable de entorno. "
              "El endpoint /actualizar rechazará todo hasta que lo configures en Render.")
    httpd = ServidorHilos(("0.0.0.0", PUERTO), Handler)
    print(f"Dashboard ENG (modo push) escuchando en el puerto {PUERTO}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
