import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def start_health_server():
    # استفاده از پورتی که رندر می‌دهد (مهم!)
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)  # حتماً 0.0.0.0 باشد
    print(f"✅ Health check server running on port {port}")
    server.serve_forever()

def start_health_server_in_background():
    thread = threading.Thread(target=start_health_server, daemon=True)
    thread.start()
    return thread
