"""企微加解密单测。

含官方《加解密方案说明》样例的**已知答案向量**——这是唯一能证伪
"对称性错误"（IV/填充块/base64 处理错）的外部基准，往返自测做不到。
"""

from src.channels.wecom.crypto import PKCS7_BLOCK_SIZE, WeComCrypto

# 官方样例：https://developer.work.weixin.qq.com/document/path/90968 「举例说明」
_SAMPLE_TOKEN = "QDG6eK"
_SAMPLE_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
_SAMPLE_RECEIVE_ID = "wx5823bf96d3bd56c7"
_SAMPLE_TIMESTAMP = "1409659813"
_SAMPLE_NONCE = "1372623149"
_SAMPLE_SIGNATURE = "477715d11cdb4164915debcba66cb864d751f3e6"
_SAMPLE_ENCRYPT = (
    "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QAD"
    "HHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k"
    "4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJYuR+LtwGc2hgf5gsijff0ekUNXZiq"
    "ATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcall"
    "crw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCm"
    "JU9p8g7m3fVKn28H3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q=="
)


def _sample_crypto() -> WeComCrypto:
    return WeComCrypto(_SAMPLE_TOKEN, _SAMPLE_AES_KEY, _SAMPLE_RECEIVE_ID)


def test_pkcs7_block_size_is_32():
    assert PKCS7_BLOCK_SIZE == 32


def test_signature_matches_official_sample():
    c = _sample_crypto()
    assert (
        c.signature(_SAMPLE_TIMESTAMP, _SAMPLE_NONCE, _SAMPLE_ENCRYPT)
        == _SAMPLE_SIGNATURE
    )


def test_decrypt_matches_official_sample():
    c = _sample_crypto()
    plain = c.decrypt(_SAMPLE_ENCRYPT)
    assert "<MsgType><![CDATA[text]]></MsgType>" in plain
    assert "<Content><![CDATA[hello]]></Content>" in plain
    assert "<ToUserName><![CDATA[wx5823bf96d3bd56c7]]></ToUserName>" in plain


def test_roundtrip_with_empty_receive_id():
    # receive_id="" 是智能机器人的场景；往返验证加解密自洽
    c = WeComCrypto("token123", "a" * 43, "")
    assert c.decrypt(c.encrypt("hello 世界")) == "hello 世界"
