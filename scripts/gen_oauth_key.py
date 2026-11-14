"""Generate the ES256 private JWK the confidential OAuth client signs with.

Writes data/oauth_jwk.json (private, keep out of git); the public half is
served at /oauth/jwks.json. Rotating = generate a new kid and redeploy.
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec


def b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
nums = key.private_numbers()
jwk = {
    "kty": "EC",
    "crv": "P-256",
    "alg": "ES256",
    "use": "sig",
    "kid": f"k{int(time.time())}",
    "x": b64(nums.public_numbers.x),
    "y": b64(nums.public_numbers.y),
    "d": b64(nums.private_value),
}
out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/oauth_jwk.json")
out.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(jwk, f)
print(f"wrote {out} (kid={jwk['kid']})")
