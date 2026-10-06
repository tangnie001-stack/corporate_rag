"""企业微信回调加解密：AES-256-CBC + PKCS#7（块 32）+ SHA1 签名。

规格（官方《回调和回复的加解密方案》）：
  AESKey = Base64_Decode(EncodingAESKey + "=")   # 32 字节
  IV     = AESKey[:16]
  明文   = random(16) + uint32_be(len(msg)) + msg + receive_id
填充用 PKCS#7，块大小固定 32（不是 AES 的 16）。
"""

import base64
import hashlib
import os
import struct

from Crypto.Cipher import AES

# PKCS#7 填充块大小；企微固定 32，误用 AES 的 16 会解密失败
PKCS7_BLOCK_SIZE: int = 32


def _pkcs7_pad(data: bytes) -> bytes:
    """按块大小 32 做 PKCS#7 填充。"""
    pad_len = PKCS7_BLOCK_SIZE - (len(data) % PKCS7_BLOCK_SIZE)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    """去掉 PKCS#7 填充。"""
    pad_len = data[-1]
    if pad_len < 1 or pad_len > PKCS7_BLOCK_SIZE:
        raise ValueError("invalid pkcs7 padding")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid pkcs7 padding")
    return data[:-pad_len]


class WeComCrypto:
    """企微回调的签名与加解密。"""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str = ""):
        """初始化。

        Args:
            token: 回调 Token（3~32 位）
            encoding_aes_key: 回调 EncodingAESKey（43 位）
            receive_id: 智能机器人场景传空字符串
        """
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.key[:16]
        self.receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        """计算 msg_signature：四参数字典序排序后拼接取 sha1。"""
        items = sorted([self.token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def decrypt(self, encrypt_b64: str) -> str:
        """解密 encrypt 字段，返回明文（JSON 字符串）。"""
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        plain = _pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt_b64)))
        msg_len = struct.unpack("!I", plain[16:20])[0]
        # 不校验尾部 receive_id：智能机器人场景恒为空串，校验无意义
        return plain[20 : 20 + msg_len].decode("utf-8")

    def encrypt(self, msg: str) -> str:
        """加密明文，返回 encrypt 字段（base64）。"""
        raw = msg.encode("utf-8")
        plain = (
            os.urandom(16)
            + struct.pack("!I", len(raw))
            + raw
            + self.receive_id.encode("utf-8")
        )
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return base64.b64encode(cipher.encrypt(_pkcs7_pad(plain))).decode("utf-8")
