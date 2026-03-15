import boto3
from botocore.exceptions import ClientError

from app.config import settings


class SESClient:
    def __init__(self):
        self.client = boto3.client(
            "ses",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.sender = settings.aws_ses_sender_email

    def send_email(
        self,
        to: str | list[str],
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> dict:
        if isinstance(to, str):
            to = [to]

        body: dict = {"Html": {"Charset": "UTF-8", "Data": html_body}}
        if text_body:
            body["Text"] = {"Charset": "UTF-8", "Data": text_body}

        try:
            response = self.client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": to},
                Message={
                    "Subject": {"Charset": "UTF-8", "Data": subject},
                    "Body": body,
                },
            )
            return {"message_id": response["MessageId"]}
        except ClientError as e:
            raise RuntimeError(f"SES error: {e.response['Error']['Message']}") from e
