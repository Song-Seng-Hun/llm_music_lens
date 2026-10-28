"""Music Lens LAN API v0.6: canonical string + HMAC helper shared by server and tests.

Canonical = UTF-8 bytes of 8 fields joined by LF, no trailing LF:
CLIENT, METHOD, TARGET, CONTENT_TYPE, CONTENT_LENGTH, TIME, NONCE, BODY_SHA256.
HMAC key = the 32 raw bytes decoded from the 64-hex key file.
Stdlib only; Python 3.9 compatible.
"""
import hashlib
import hmac
import re
import secrets
import time
import urllib.parse

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
KEY_RE = re.compile(r"\A[0-9a-f]{64}\n?\Z")
CLIENT_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")


class KeyFormatError(ValueError):
    pass


def parse_key_text(text):
    """64 lowercase hex + at most one trailing LF, else error. Returns 32 raw bytes."""
    if not isinstance(text, str) or not KEY_RE.match(text):
        raise KeyFormatError("key must be 64 lowercase hex plus at most one trailing LF")
    return bytes.fromhex(text[:64])


def canonical(client, method, target, content_type, content_length, time_s, nonce, body_sha256):
    fields = [client, method.upper(), target, content_type, str(content_length), str(time_s), nonce, body_sha256]
    for f in fields:
        if "\n" in f:
            raise ValueError("canonical field contains LF")
    return "\n".join(fields).encode("utf-8")


def signature(key, canonical_bytes):
    return hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()


def encode_name(name):
    """Percent-encode a UTF-8 label like JS encodeURIComponent for the chars that matter here."""
    return urllib.parse.quote(name, safe="-_.!~*'()")


def sign_headers(client, key, method, target, body=b"", content_type=None, time_s=None, nonce=None):
    """Build the X-ML-* headers (and CT/CL) for a request. Returns (headers dict, canonical bytes)."""
    method = method.upper()
    time_s = int(time.time()) if time_s is None else time_s
    nonce = secrets.token_hex(16) if nonce is None else nonce
    if method == "GET":
        ct, cl, body_hash = "", 0, EMPTY_SHA256
    else:
        ct = "application/octet-stream" if content_type is None else content_type
        cl, body_hash = len(body), hashlib.sha256(body).hexdigest()
    canon = canonical(client, method, target, ct, cl, time_s, nonce, body_hash)
    headers = {"X-ML-Client": client, "X-ML-Time": str(time_s), "X-ML-Nonce": nonce,
               "X-ML-Body-SHA256": body_hash, "X-ML-Sig": signature(key, canon)}
    if method != "GET":
        headers["Content-Type"] = ct
        headers["Content-Length"] = str(cl)
    return headers, canon


# Fixed vector shared with sign_vector.mjs (test key only; never a real key).
VECTOR = {
    "key_hex": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
    "client": "claude-window",
    "time": 1793000000,
    "nonce": "0123456789abcdef0123456789abcdef",
    "name": "테스트 곡",
    "post_body_hex": "52494646",
}


def vector_values():
    v = VECTOR
    key = bytes.fromhex(v["key_hex"])
    target = "/analyze?language=ko&emotion_mode=voice&name=" + encode_name(v["name"])
    body = bytes.fromhex(v["post_body_hex"])
    c_post = canonical(v["client"], "POST", target, "application/octet-stream", len(body), v["time"], v["nonce"],
                       hashlib.sha256(body).hexdigest())
    c_get = canonical(v["client"], "GET", "/status", "", 0, v["time"], v["nonce"], EMPTY_SHA256)
    return {"post_target": target, "post_canonical_hex": c_post.hex(), "post_sig": signature(key, c_post),
            "get_canonical_hex": c_get.hex(), "get_sig": signature(key, c_get)}


if __name__ == "__main__":
    import json
    print(json.dumps(vector_values(), ensure_ascii=False))
