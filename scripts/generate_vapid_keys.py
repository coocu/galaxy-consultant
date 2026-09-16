"""Render 환경변수에 넣을 Web Push VAPID 키 생성 스크립트."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_numbers = private_key.private_numbers()
    public_numbers = private_numbers.public_numbers

    private_bytes = private_numbers.private_value.to_bytes(32, "big")
    public_bytes = (
        b"\x04"
        + public_numbers.x.to_bytes(32, "big")
        + public_numbers.y.to_bytes(32, "big")
    )

    print("VAPID_PUBLIC_KEY=" + base64url(public_bytes))
    print("VAPID_PRIVATE_KEY=" + base64url(private_bytes))
    print("VAPID_SUBJECT=mailto:code_note95@naver.com")


if __name__ == "__main__":
    main()
