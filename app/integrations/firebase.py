import json
import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_firebase():
    global _initialized
    if _initialized:
        return

    service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT")
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

    if service_account:
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred_dict = json.loads(service_account)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            _initialized = True
            logger.info("Firebase initialized from env var")
        except Exception as e:
            logger.warning("Firebase init failed: %s", e)
    elif service_account_path and os.path.isfile(service_account_path):
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
            _initialized = True
            logger.info("Firebase initialized from file: %s", service_account_path)
        except Exception as e:
            logger.warning("Firebase init failed: %s", e)
    else:
        logger.warning("Firebase not configured — push notifications disabled")


def is_initialized() -> bool:
    return _initialized
