"""R2BlobStore -- BlobStore backed by Cloudflare R2 via httpx + AWS SigV4 (NO boto3).

boto3/aioboto3 pull botocore, whose tight pins drag pydantic-ai down ~69 versions -- so we
sign the S3 requests ourselves with stdlib hmac/hashlib over the app's httpx client. R2 is
S3-compatible; only PUT/GET object are needed (both off the hot path). Drop-in for BlobStore.

Setup:  env R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET (+ optional R2_PREFIX)
        api.py:  raw = R2BlobStore.from_env(app.state.http)
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import logfire

_ALGO = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
_REGION = "auto"  # R2 uses the literal region 'auto'


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class R2BlobStore:
    """BlobStore backed by Cloudflare R2, signed with SigV4 over httpx. url -> <prefix><sha256(url)>."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        bucket: str,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        prefix: str = "data/",
    ):
        self._http = http
        self._bucket = bucket
        self._prefix = prefix
        self._akid = access_key_id
        self._secret = secret_access_key
        self._host = f"{account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls, http: httpx.AsyncClient) -> R2BlobStore:
        """Build from R2_* env vars (set these locally in .env and on Railway)."""
        return cls(
            http,
            os.environ["R2_BUCKET"],
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            prefix=os.environ.get("R2_PREFIX", "data/"),
        )

    def _object_key(self, url: str) -> str:
        return self._prefix + hashlib.sha256(url.encode()).hexdigest()

    def _sign(self, method: str, key: str, body: bytes) -> tuple[str, dict[str, str]]:
        """Return (url, headers) for a SigV4-signed path-style S3 request."""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        canonical_uri = "/" + quote(f"{self._bucket}/{key}", safe="/")
        payload_hash = _sha256_hex(body)

        canonical_headers = (
            f"host:{self._host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
        )

        scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [_ALGO, amz_date, scope, _sha256_hex(canonical_request.encode())]
        )

        k_date = _hmac(("AWS4" + self._secret).encode(), date_stamp)
        k_region = _hmac(k_date, _REGION)
        k_service = _hmac(k_region, _SERVICE)
        k_signing = _hmac(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        authorization = (
            f"{_ALGO} Credential={self._akid}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"https://{self._host}{canonical_uri}"
        headers = {
            "Authorization": authorization,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        return url, headers

    async def get(self, url: str) -> bytes | None:
        req_url, headers = self._sign("GET", self._object_key(url), b"")
        # R2 cache ops are infra plumbing -- don't trace them. A miss is a normal 404 that
        # would otherwise show up as a red "error" span in logfire.
        with logfire.suppress_instrumentation():
            resp = await self._http.get(req_url, headers=headers)
        if resp.status_code == 404:
            return None  # not cached -> caller scrapes
        resp.raise_for_status()
        return resp.content

    async def put(self, url: str, data: bytes) -> None:
        req_url, headers = self._sign("PUT", self._object_key(url), data)
        with logfire.suppress_instrumentation():
            resp = await self._http.put(req_url, headers=headers, content=data)
        resp.raise_for_status()
