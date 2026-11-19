"""Rate-limited static file server, so an HLS client can't fetch the whole
VOD before the test kills the server.

    python slow_http.py PORT DIRECTORY BYTES_PER_SECOND
"""
import functools
import http.server
import sys
import time


class SlowHandler(http.server.SimpleHTTPRequestHandler):
    rate = 1_000_000

    def copyfile(self, source, outputfile):
        chunk = 16384
        delay = chunk / self.rate
        while True:
            buf = source.read(chunk)
            if not buf:
                break
            outputfile.write(buf)
            time.sleep(delay)

    def log_message(self, *args):
        pass


def main():
    port, directory, rate = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    SlowHandler.rate = rate
    handler = functools.partial(SlowHandler, directory=directory)
    http.server.ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever()


if __name__ == '__main__':
    main()
