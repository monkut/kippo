import hashlib
import hmac
import json
from pathlib import Path


def load_webhookevent(filepath: Path, secret_encoded: bytes, decode: bool = False) -> tuple[bytes, str]:
    with filepath.open("rb") as content_f:
        content = content_f.read()
        # calculate the 'X-Hub-Signature' header
        s = hmac.new(key=secret_encoded, msg=content, digestmod=hashlib.sha1).hexdigest()
        signature = f"sha1={s}"
        if decode:
            content = json.loads(content)
    return content, signature
