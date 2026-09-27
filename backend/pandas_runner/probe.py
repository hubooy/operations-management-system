"""Signed fixed-loopback runtime probe; never imports Django or executes code locally."""
import argparse
import hmac
import http.client
import re
import time
import uuid

from .key_file import read_key
from .protocol import HEALTH_PATH, PATH, PORT, decode, encode, signature, package_digest


def probe(key_file, image, *, self_test=False):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("invalid_image")
    key = read_key(key_file)
    path = PATH if self_test else HEALTH_PATH
    job = {"frames": {"fixture": [{"cents": 1000}, {"cents": -200}]},
           "code": "result = pd.DataFrame([{'cents': int(frames['fixture']['cents'].sum())}])"}
    raw = encode(job) if self_test else b"{}"
    stamp, nonce = str(int(time.time())), uuid.uuid4().hex
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=24 if self_test else 12)
    try:
        connection.request("POST", path, body=raw, headers={"Content-Type": "application/json",
            "X-Nonce": nonce, "X-Timestamp": stamp, "X-Signature": signature(key, stamp, nonce, raw, path=path)})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096 or not hmac.compare_digest(
                response.getheader("X-Signature", ""), signature(key, stamp, nonce, body, "response", path=path)):
            raise ValueError("probe_failed")
        value = decode(body)
        if value.get("image") != image or value.get("cleanupVerified") is not True:
            raise ValueError("probe_failed")
        if self_test:
            if value.get("result", {}).get("rows") != [{"cents": 800}]:
                raise ValueError("probe_failed")
        elif value.get("status") != "ready" or value.get("runnerSha256") != package_digest():
            raise ValueError("probe_failed")
        return {"status": "ready", "image": image, "cleanupVerified": True, "selfTest": self_test}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        print(encode(probe(args.key_file, args.image, self_test=args.self_test)).decode())
    except Exception:
        print('{"status":"not_ready"}')
        raise SystemExit(1)


if __name__ == "__main__":
    main()
