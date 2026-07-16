from io import BytesIO
from typing import Optional
from minio import Minio
from minio.error import S3Error
from loguru import logger

from src.config import settings


class FileStore:
    def __init__(
        self,
        endpoint: str = settings.MINIO_ENDPOINT,
        access_key: str = settings.MINIO_ACCESS_KEY,
        secret_key: str = settings.MINIO_SECRET_KEY,
        bucket: str = settings.MINIO_DOC_BUCKET,
        secure: bool = False,
    ):
        self._bucket = bucket
        self._client = Minio(endpoint, access_key, secret_key, secure=secure)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("Created MinIO bucket '{}'", self._bucket)

    @staticmethod
    def build_path(user_id: str, kb_id: str, doc_id: str, filename: str) -> str:
        return f"documents/{user_id}/{kb_id}/{doc_id}/{filename}"

    def upload(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        try:
            self._client.put_object(
                self._bucket, key, BytesIO(data), len(data), content_type=content_type
            )
            return True
        except S3Error as e:
            logger.error("MinIO upload failed: {} - {}", key, e)
            return False

    def download(self, key: str) -> Optional[bytes]:
        try:
            resp = self._client.get_object(self._bucket, key)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return data
        except S3Error as e:
            logger.warning("MinIO download failed: {} - {}", key, e)
            return None

    def delete(self, key: str) -> bool:
        try:
            self._client.remove_object(self._bucket, key)
            return True
        except S3Error as e:
            logger.warning("MinIO delete failed: {} - {}", key, e)
            return False
