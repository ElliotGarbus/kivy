import socket, sys
host = sys.argv[1]
for path in sys.argv[2:]:
    s = socket.create_connection((host, 8554), timeout=10)
    s.sendall(("DESCRIBE rtsp://%s:8554/%s RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n" % (host, path)).encode())
    data = b""
    s.settimeout(10)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                head, _, body = data.partition(b"\r\n\r\n")
                n = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")]
                if not n or len(body) >= n[0]:
                    break
    except socket.timeout:
        pass
    s.close()
    text = data.decode(errors="replace")
    print("==", path, text.splitlines()[0] if text else "no reply")
    for line in text.splitlines():
        if line.startswith(("m=", "a=rtpmap")):
            print("  ", line)
