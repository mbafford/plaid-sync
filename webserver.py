#!python
"""
Wraps a very simple webserver designed only for serving up the HTML to
run the Plaid Link service and capture the response JSON from the Plaid Link
API. This API must be run in the web browser, so this does the bare minimum
to accomplish this.

https://plaid.com/docs/link/
"""

import json
import logging
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict

log = logging.getLogger(__name__)


class DataStore:
    def __init__(self, config_json: Dict):
        self.config_json: Dict = config_json
        self.plaid_response: Dict = None


class PlaidLinkHTTPServer(BaseHTTPRequestHandler):
    def __init__(self, data_store: DataStore, *args, **kwargs):
        self.data_store = data_store
        super().__init__( *args, **kwargs)

    def serve_file(self, file_path: str):
        mimetype = mimetypes.guess_type(file_path)
        self.send_response(200)
        self.send_header('Content-type', mimetype[0])
        self.end_headers()

        with open(file_path, "r") as f:
            html = f.read()

        html = html.replace(
            "{{CONFIG_JSON}}",
            json.dumps(self.data_store.config_json)
        )

        self.wfile.write(html.encode('utf-8'))

        self.wfile.flush()

    def log_request(self, code=None, size=None) -> None:
        pass

    def send_404(self):
        self.send_response(404)
        self.send_header('Content-type', "text/plain")
        self.end_headers()
        self.wfile.write(b"not found")
        self.wfile.flush()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/success":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)

            self.data_store.plaid_response = json.loads(body)

            self.server.shutdown()
            self.server.server_close()

            return
        else:
            self.send_404()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/link.html":
            self.serve_file("html/link.html")
            return
        else:
            self.send_404()


def serve(env: str, clientName: str, token: str, pageTitle: str, accountName: str, type: str) -> Dict:
    """
    Starts a webserver and serves the html/link.html file with the
    specified configuration.

    Host and port will be 127.0.0.1:4583

    Returns the JSON returned by the Plaid Link API when the user has successfully
    finished the authorization flow.
    """

    config_json = dict(
        env=env,
        clientName=clientName,
        token=token,
        pageTitle=pageTitle,
        accountName=accountName,
        type=type
    )

    ds: DataStore = DataStore(config_json)

    def make_handler(*args, **kwargs):
        return PlaidLinkHTTPServer(ds, *args, **kwargs)

    with ThreadingHTTPServer(('127.0.0.1', 4583), make_handler) as httpd:
        host, port = httpd.socket.getsockname()
        print('Open the following page in your browser to continue:')
        print(f'    http://{host}:{port}/link.html')

        try:
            # well, until the API to shutdown is called
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Keyboard interrupt received, exiting.")
            sys.exit(0)

    return ds.plaid_response


if __name__ == '__main__':
    serve({})
