from fastapi import APIRouter, Request, Response, BackgroundTasks, HTTPException, Header
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import ProcessedEvent, GiteaAccount, GiteaInstance, SystemConfig
from ..gitea import decode_user_context, verify_hmac_signature
from ..utils.audit import log_webhook_event
import json
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_webhook_signature(body: bytes, signature: str, db: Session) -> bool:
    """Verify Gitea webhook HMAC-SHA256 signature.
    
    Gitea sends X-Gitea-Signature header containing HMAC-SHA256 hex digest
    of the raw request body using the webhook secret.
    
    Returns True if signature is valid, False otherwise.
    """
    if not signature:
        return False
    
    # Get webhook signing key from system config
    config = db.query(SystemConfig).first()
    if config and config.webhook_signing_key:
        signing_key = config.webhook_signing_key
    else:
        import os
        signing_key = os.getenv("WEBHOOK_SIGNING_KEY", "")
    
    if not signing_key:
        logger.error("No webhook signing key configured")
        return False
    
    return verify_hmac_signature(body, signature, signing_key)


def check_idempotency(db: Session, event_type: str, reference_id: str) -> bool:
    """Check if event has already been processed."""
    existing = db.query(ProcessedEvent).filter(
        ProcessedEvent.event_type == event_type,
        ProcessedEvent.reference_id == reference_id
    ).first()
    return existing is not None


def try_record_event(db: Session, event_type: str, reference_id: str) -> bool:
    """Try to record an event atomically. Returns True if successfully recorded (first time)."""
    try:
        # Check if already exists first
        if check_idempotency(db, event_type, reference_id):
            return False

        event = ProcessedEvent(event_type=event_type, reference_id=reference_id)
        db.add(event)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        # If commit failed due to unique constraint or other DB error
        logger.warning(f"Failed to record event {event_type}/{reference_id}: {e}")
        return False


def is_self_trigger(payload: dict, bot_username: str) -> bool:
    """Check if the webhook was triggered by the bot itself."""
    sender = payload.get("sender", {})
    sender_login = sender.get("login", "")
    return sender_login == bot_username


def get_signing_key(db: Session) -> str:
    """Get webhook signing key from system config."""
    config = db.query(SystemConfig).first()
    if config and config.webhook_signing_key:
        return config.webhook_signing_key
    # Fallback to environment variable
    import os
    return os.getenv("WEBHOOK_SIGNING_KEY", "default-signing-key-change-me")


def get_context_from_header(authorization: str, db: Session) -> tuple[int, int]:
    """Extract instance and account IDs from Authorization header with signature validation."""
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    encoded = authorization.replace("Basic ", "")

    # Get signing key
    signing_key = get_signing_key(db)

    # Decode and validate
    instance_id, account_id = decode_user_context(encoded, signing_key)

    if instance_id == 0 or account_id == 0:
        raise HTTPException(status_code=401, detail="Invalid auth payload or signature")

    return instance_id, account_id


@router.post("/gitea")
async def receive_gitea_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitea_signature: str = Header(None, alias="X-Gitea-Signature"),
    x_gitea_event: str = Header(None, alias="X-Gitea-Event"),
    authorization: str = Header(None, alias="Authorization")
):
    """Receive and process Gitea webhook.

    Security: Validates HMAC-SHA256 signature from X-Gitea-Signature header
    before processing the webhook payload.
    """
    logger.info("=== WEBHOOK RECEIVED ===")

    # Get client IP for audit logging
    client_ip = request.client.host if request.client else "unknown"

    body = await request.body()

    # Verify webhook signature FIRST - reject invalid signatures immediately
    db_verify = SessionLocal()
    try:
        if not verify_webhook_signature(body, x_gitea_signature, db_verify):
            logger.warning(f"Invalid webhook signature: {x_gitea_signature[:20] if x_gitea_signature else 'missing'}...")
            # Audit log for failed signature
            log_webhook_event(
                db_verify,
                event_type=x_gitea_event or "unknown",
                status="FAILURE",
                details={"reason": "invalid_signature", "signature": x_gitea_signature[:20] if x_gitea_signature else "missing"},
                ip_address=client_ip
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        logger.info("Webhook signature verified successfully")
        
        # Audit log for successful signature verification
        log_webhook_event(
            db_verify,
            event_type=x_gitea_event or "unknown",
            status="SUCCESS",
            details={"action": action},
            ip_address=client_ip
        )
    except HTTPException:
        db_verify.close()
        raise
    finally:
        db_verify.close()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = x_gitea_event or "unknown"
    action = payload.get("action", "unknown")
    logger.info(f"Event: {event_type}, Action: {action}")

    # Get context from Authorization header with signature validation
    db_auth = SessionLocal()
    try:
        instance_id, account_id = get_context_from_header(authorization, db_auth)
    except HTTPException:
        db_auth.close()
        raise
    finally:
        db_auth.close()

    # Quick validation - return immediately if invalid
    db = SessionLocal()
    try:
        account = db.query(GiteaAccount).filter(GiteaAccount.id == account_id).first()
        if not account:
            logger.warning(f"Account {account_id} not found")
            return Response(status_code=200)

        instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()
        if not instance:
            logger.warning(f"Instance {instance_id} not found")
            return Response(status_code=200)

        bot_username = account.gitea_username
        logger.debug(f"Bot username: {bot_username}")
    except Exception as e:
        logger.error(f"Database query error: {e}")
        return Response(status_code=200)
    finally:
        db.close()

    # Only process created/opened actions - skip edited/deleted/closed etc
    allowed_actions = ["created", "opened"]
    if action.lower() not in allowed_actions:
        logger.debug(f"Skipping action: {action}, only processing created/opened")
        return Response(status_code=200)

    # Determine reference_id for idempotency
    ref_raw = ""
    if event_type == "issue_comment":
        ref_raw = payload.get("comment", {}).get("id")
    elif event_type == "issues":
        ref_raw = payload.get("issue", {}).get("id")
    elif event_type == "pull_request":
        ref_raw = payload.get("pull_request", {}).get("id")
    
    reference_id = str(ref_raw) if ref_raw else ""

    # Check idempotency atomically
    db = SessionLocal()
    try:
        if reference_id:
            if not try_record_event(db, event_type, reference_id):
                logger.info(f"Event already processed or processing: {event_type}/{reference_id}")
                return Response(status_code=200)
            logger.debug(f"Event recorded successfully: {event_type}/{reference_id}")
    except Exception as e:
        logger.error(f"Idempotency error: {e}")
    finally:
        db.close()

    # Check if bot is mentioned (quick check before scheduling background task)
    content_to_check = ""
    if event_type == "issue_comment":
        content_to_check = payload.get("comment", {}).get("body", "")
    elif event_type == "issues":
        content_to_check = payload.get("issue", {}).get("body", "")
    elif event_type == "pull_request":
        content_to_check = payload.get("pull_request", {}).get("body", "")

    logger.debug(f"Content to check: '{content_to_check[:100] if content_to_check else 'empty'}'")
    logger.debug(f"Looking for @{bot_username}")

    if f"@{bot_username}" not in (content_to_check or ""):
        logger.info(f"No @mention found, skipping")
        return Response(status_code=200)

    logger.info(f"Bot mentioned, scheduling background processing")

    # Schedule background task for AI processing
    background_tasks.add_task(
        process_webhook_async,
        instance_id,
        account_id,
        event_type,
        payload
    )

    # Return immediately
    return Response(status_code=200)


async def process_webhook_async(
    instance_id: int,
    account_id: int,
    event_type: str,
    payload: dict
):
    """Background task to process webhook and call AI."""
    logger.info(f"=== BACKGROUND PROCESSING STARTED ===")

    db = SessionLocal()
    try:
        instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()
        account = db.query(GiteaAccount).filter(GiteaAccount.id == account_id).first()

        if not instance or not account:
            logger.error(f"Instance or account not found in background task")
            return

        from .processor import WebhookProcessor
        processor = WebhookProcessor(instance, account, db)

        await processor.process(event_type, payload, db)
        logger.info(f"=== BACKGROUND PROCESSING COMPLETED ===")

    except Exception as e:
        logger.error(f"Background processing error: {e}", exc_info=True)
    finally:
        db.close()