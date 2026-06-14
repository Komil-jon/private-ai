"""Minimal preview server: serves / -> index.html, /static/* -> frontend/*"""
import http.server, os, sys

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/" or path == "":
            return os.path.join(FRONTEND, "index.html")
        if path.startswith("/static/"):
            return os.path.join(FRONTEND, path[len("/static/"):])
        return os.path.join(FRONTEND, path.lstrip("/"))

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8789))
    with http.server.HTTPServer(("", port), Handler) as srv:
        srv.serve_forever()
