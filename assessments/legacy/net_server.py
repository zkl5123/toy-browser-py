"""Local HTTP server fixtures for network tests (stdlib only)."""
import http.server, socketserver, threading, gzip, io, os

PAGES = {
    '/index.html': "<html><body><h1>Local</h1><p style='color:red'>red text</p><a href='/two.html'>two</a></body></html>",
    '/two.html': "<html><head><link rel='stylesheet' href='/style.css'></head><body><p>page two</p></body></html>",
    '/style.css': "p { color: blue; font-size: 20px; }",
    '/redirect': None,  # 302 -> /two.html
    '/redirect_loop': None,
    '/404': None,
    '/500': None,
    '/slow': None,
    '/gzip': None,
}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path
        if p == '/redirect':
            self.send_response(302); self.send_header('Location', '/two.html'); self.end_headers(); return
        if p == '/redirect_loop':
            self.send_response(302); self.send_header('Location', '/redirect_loop'); self.end_headers(); return
        if p == '/404':
            self.send_response(404); self.send_header('Content-Length', '0'); self.end_headers(); return
        if p == '/500':
            self.send_response(500); self.send_header('Content-Length', '0'); self.end_headers(); return
        if p == '/slow':
            import time; time.sleep(3)
            self.send_response(200); self.end_headers(); self.wfile.write(b'slow ok'); return
        if p == '/gzip':
            body = PAGES['/index.html'].encode()
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode='wb') as f: f.write(body)
            self.send_response(200)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(buf.getvalue())))
            self.end_headers(); self.wfile.write(buf.getvalue()); return
        if p in PAGES and PAGES[p] is not None:
            body = PAGES[p].encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'text/html' if p.endswith('.html') else 'text/css')
            self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.end_headers()

def start(port=0):
    httpd = socketserver.TCPServer(('127.0.0.1', port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]

if __name__ == '__main__':
    httpd, port = start(8977)
    print(f"server on http://127.0.0.1:{port}")
    try:
        while True: httpd.handle_request()
    except KeyboardInterrupt:
        pass
