import uuid

import boto3
from botocore.exceptions import ClientError

from app.config import settings


class S3Client:
    def __init__(self):
        self.client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.bucket = settings.aws_s3_bucket

    def upload_file(
        self, file_content: bytes, filename: str, content_type: str = "application/octet-stream"
    ) -> str:
        key = f"uploads/{uuid.uuid4()}/{filename}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=file_content,
            ContentType=content_type,
        )
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete_file(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
