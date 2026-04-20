import logging
from datetime import datetime
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import GiteaAccount, GiteaInstance, SystemConfig, ProcessedEvent
from ..gitea import GiteaClient
from ..core.event_processor import EventProcessor
import asyncio

logger = logging.getLogger(__name__)


async def poll_notifications():
    """Poll Gitea notifications for all accounts."""
    db = SessionLocal()
    try:
        # Get system config for poll interval
        config = db.query(SystemConfig).first()
        poll_interval = config.notification_poll_interval if config else 1
        
        accounts = db.query(GiteaAccount).all()
        for account in accounts:
            # Check if it's time to poll this account
            if account.last_notified_at:
                elapsed = datetime.utcnow() - account.last_notified_at
                if elapsed.total_seconds() < (poll_interval * 60) - 5: # 5s buffer
                    continue
            
            instance = db.query(GiteaInstance).filter(GiteaInstance.id == account.instance_id).first()
            if not instance:
                continue
            
            await process_account_notifications(account, instance, db)
            
    except Exception as e:
        logger.error(f"Notification polling task error: {e}", exc_info=True)
    finally:
        db.close()


async def process_account_notifications(account: GiteaAccount, instance: GiteaInstance, db: Session):
    """Fetch and process notifications for a single account."""
    client = GiteaClient(
        instance.url,
        account.access_token,
        account_id=account.id,
        db_session=db
    )
    
    try:
        # Fetch unread notifications
        # Use since=account.last_notified_at if available
        notifications = await client.get_notifications(
            all_notifications=False,
            since=account.last_notified_at
        )
        
        if not notifications:
            return

        logger.info(f"Found {len(notifications)} new notifications for account {account.gitea_username}")
        
        # Sort by updated_at to process in order
        notifications.sort(key=lambda x: x.get("updated_at", ""))
        
        for note in notifications:
            await handle_notification(note, client, account, instance, db)
            
        # Update last_notified_at
        account.last_notified_at = datetime.utcnow()
        db.commit()
        
    except Exception as e:
        logger.error(f"Error processing notifications for {account.gitea_username}: {e}")
        db.rollback()


async def handle_notification(note: dict, client: GiteaClient, account: GiteaAccount, instance: GiteaInstance, db: Session):
    """Process a single notification."""
    note_id = note.get("id")
    subject = note.get("subject", {})
    subject_type = subject.get("type")  # "Issue", "PullRequest"
    repository = note.get("repository", {})
    
    if not note_id or not subject_type or not repository:
        return

    # We only care about mentions in issues and PRs
    if subject_type not in ["Issue", "PullRequest"]:
        return

    latest_comment_url = subject.get("latest_comment_url")
    if not latest_comment_url:
        # Might be a new issue/PR without comments yet, but bot is mentioned in body
        # For now, focus on comments as they are the main trigger
        return

    # Extract comment ID from URL: .../issues/comments/123
    try:
        comment_id = int(latest_comment_url.split("/")[-1])
    except (ValueError, IndexError):
        logger.warning(f"Could not parse comment ID from {latest_comment_url}")
        return

    # Check idempotency using comment ID
    event_type = "issue_comment"
    reference_id = str(comment_id)
    
    existing = db.query(ProcessedEvent).filter(
        ProcessedEvent.event_type == event_type,
        ProcessedEvent.reference_id == reference_id
    ).first()
    
    if existing:
        # Already processed this comment
        # Mark notification as read to avoid seeing it again
        await client.mark_notification_as_read(note_id)
        return

    logger.info(f"Processing new notification {note_id} for comment {comment_id}")

    try:
        # Fetch comment details
        owner = repository.get("owner", {}).get("login")
        repo_name = repository.get("name")
        
        if not owner or not repo_name:
            # Fallback to parsing from full_name
            full_name = repository.get("full_name", "")
            if "/" in full_name:
                owner, repo_name = full_name.split("/", 1)
            else:
                return

        comment = await client.get_comment_by_id(owner, repo_name, comment_id)
        
        # Check if bot is mentioned
        bot_username = account.gitea_username
        comment_body = comment.get("body", "")
        
        if f"@{bot_username}" not in comment_body:
            # Not a mention for us
            await client.mark_notification_as_read(note_id)
            return

        # Fetch issue details
        issue_url = subject.get("url")
        if not issue_url:
            return
            
        issue_number = int(issue_url.split("/")[-1])
        issue = await client.get_issue(owner, repo_name, issue_number)
        
        # Construct payload compatible with WebhookProcessor
        payload = {
            "action": "created",
            "issue": issue,
            "comment": comment,
            "repository": repository,
            "sender": comment.get("user", {})
        }
        
        # Record event for idempotency
        event = ProcessedEvent(event_type=event_type, reference_id=reference_id)
        db.add(event)
        db.commit()
        
        # Process using EventProcessor
        processor = EventProcessor(instance, account, db)
        await processor.process(event_type, payload, db)
        
        # Mark as read
        await client.mark_notification_as_read(note_id)
        
    except Exception as e:
        logger.error(f"Failed to handle notification {note_id}: {e}", exc_info=True)
        db.rollback()


def run_polling_task():
    """Synchronous wrapper for APScheduler."""
    asyncio.run(poll_notifications())
