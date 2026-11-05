"""End-to-end payload sealing for the mandated HTTP endpoint.

Android pins the server SPKI, wraps an ephemeral AES-256 key with RSA-OAEP,
and uses distinct GCM nonces for every request, response and WS frame.
"""
import base64, json, os, time
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def b64(b): return base64.b64encode(b).decode('ascii')
def unb64(s): return base64.b64decode(s, validate=True)

class CryptoBox:
    def __init__(self, root: Path):
        path = root / 'transport-key.pem'
        if not path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            if os.name != 'nt': path.chmod(0o600)
        self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        self.public_der = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.seen = {}  # Reject replayed envelopes during a process lifetime.
        self._session_keys: dict[str, tuple[bytes, float]] = {}  # Cache unwrapped AES session keys for 30 min

    def public(self): return {'public_key':b64(self.public_der), 'algorithm':'RSA-OAEP-SHA256+A256GCM'}

    def open(self, envelope):
        if len(envelope.get('data','')) > 6_000_000: raise ValueError('请求过大')
        now = time.time()
        enc_key_b64 = envelope['key']
        cached = self._session_keys.get(enc_key_b64)
        if cached and now - cached[1] < 1800:
            key = cached[0]
        else:
            key = self.key.decrypt(unb64(enc_key_b64), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
            if len(self._session_keys) > 256:
                self._session_keys = {k: v for k, v in self._session_keys.items() if now - v[1] < 1800}
            self._session_keys[enc_key_b64] = (key, now)
        nonce = unb64(envelope['nonce'])
        data = json.loads(AESGCM(key).decrypt(nonce, unb64(envelope['data']), None))
        stamp = data.get('timestamp', 0)
        if abs(now - stamp) > 300: raise ValueError('设备时钟偏差超过 5 分钟')
        replay = enc_key_b64[-32:] + ':' + envelope['nonce']
        if len(self.seen) > 2000:
            self.seen = {k:v for k,v in self.seen.items() if v > now - 300}
        if replay in self.seen: raise ValueError('重复请求')
        self.seen[replay] = now
        return key, data

    @staticmethod
    def seal(key, data):
        nonce = os.urandom(12)
        raw = json.dumps(data, ensure_ascii=False, separators=(',',':')).encode()
        return {'nonce':b64(nonce), 'data':b64(AESGCM(key).encrypt(nonce,raw,None))}

    @staticmethod
    def frame(key, envelope):
        return json.loads(AESGCM(key).decrypt(unb64(envelope['nonce']),unb64(envelope['data']),None))
