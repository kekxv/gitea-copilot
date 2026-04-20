import logging
from datetime import datetime
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import GiteaAccount, GiteaInstance, SystemConfig, ProcessedEvent
from ..gitea import GiteaClient
from ..core.event_processor import EventProcessor
import asyncio

logger = logging.getLogger("uvicorn.error")


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
    """Fetch and process notifications for a single account with tail-catching loop."""
    client = GiteaClient(
        instance.url,
        account.access_token,
        account_id=account.id,
        db_session=db
    )
    
    max_tails = 5  # Maximum number of consecutive checks to avoid infinite loops
    checks = 0
    
    while checks < max_tails:
        try:
            # 1. Capture the start time of this poll
            current_poll_time = datetime.utcnow()
            
            # 2. Fetch unread notifications
            notifications = await client.get_notifications(
                all_notifications=False,
                since=account.last_notified_at
            )
            
            if not notifications:
                break

            logger.info(f"Check #{checks+1}: Found {len(notifications)} new notifications for @{account.gitea_username}")
            
            # 3. Sort and process
            notifications.sort(key=lambda x: x.get("updated_at", ""))
            for note in notifications:
                try:
                    await handle_notification(note, client, account, instance, db)
                except Exception as e:
                    logger.error(f"Error handling notification {note.get('id')}: {e}")
                
            # 4. Update last_notified_at to the start time of THIS successful poll
            # This ensures that anything arriving AFTER current_poll_time will be 
            # picked up in the next iteration or next scheduler run.
            account.last_notified_at = current_poll_time
            db.commit()
            
            checks += 1
            
        except Exception as e:
            logger.error(f"Error in poll iteration for {account.gitea_username}: {e}")
            db.rollback()
            break
    
    if checks >= max_tails:
        logger.warning(f"Reached max poll iterations ({max_tails}) for @{account.gitea_username}, stopping for now.")



async def handle_notification(note: dict, client: GiteaClient, account: GiteaAccount, instance: GiteaInstance, db: Session):
    """Process a single notification using the 'catch-up' strategy."""
    note_id = note.get("id")
    subject = note.get("subject", {})
    subject_type = subject.get("type")  # "Issue", "PullRequest"
    repository = note.get("repository", {})
    bot_username = account.gitea_username
    
    if not note_id or not subject_type or not repository:
        return

    if subject_type not in ["Issue", "PullRequest"]:
        # For non-issue/PR notifications, just mark as read
        await client.mark_notification_as_read(note_id)
        return

    # 1. Mark as read immediately to clear Gitea state
    await client.mark_notification_as_read(note_id)
    logger.info(f"Marked notification {note_id} as read, analyzing thread...")

    try:
        owner = repository.get("owner", {}).get("login") or repository.get("full_name", "").split("/")[0]
        repo_name = repository.get("name") or repository.get("full_name", "").split("/")[1]
        issue_url = subject.get("url", "")
        issue_number = int(issue_url.split("/")[-1])
        
        # 2. Fetch all comments for this thread
        comments = await client.get_issue_comments(owner, repo_name, issue_number)
        
        # 3. Fetch issue/PR details
        issue = await client.get_issue(owner, repo_name, issue_number)
        
        # 4. Find the last time the bot replied
        # Sort comments by creation time (Gitea API usually returns them sorted, but let's be sure)
        comments.sort(key=lambda x: x.get("created_at", ""))
        
        last_bot_reply_time = None
        for comment in reversed(comments):
            if comment.get("user", {}).get("login") == bot_username:
                last_bot_reply_time = comment.get("created_at")
                break
        
        # 5. Collect all new @mentions after the last bot reply
        # If bot never replied, we also consider the issue body
        to_process = []
        
        # Check issue body if it's "newer" than the last bot reply
        # (Usually only relevant if bot never replied or issue description was edited, 
        # but Gitea notifications are mostly triggered by comments)
        if not last_bot_reply_time or issue.get("created_at") > last_bot_reply_time:
            if f"@{bot_username}" in (issue.get("body") or ""):
                to_process.append({
                    "type": "issues" if subject_type == "Issue" else "pull_request",
                    "ref": f"subject_{subject_type}_{issue_number}",
                    "item": issue,
                    "sender": issue.get("user", {})
                })

        for comment in comments:
            created_at = comment.get("created_at")
            if last_bot_reply_time and created_at <= last_bot_reply_time:
                continue
                
            if f"@{bot_username}" in (comment.get("body") or ""):
                to_process.append({
                    "type": "issue_comment",
                    "ref": f"comment_{comment.get('id')}",
                    "item": comment,
                    "sender": comment.get("user", {})
                })

        if not to_process:
            logger.info(f"No new mentions for @{bot_username} found in thread #{issue_number}")
            return

        logger.info(f"Found {len(to_process)} new mentions to process in thread #{issue_number}")

        # 6. Process each mention
        processor = EventProcessor(instance, account, db)
        for task in to_process:
            event_type = task["type"]
            # Unique ID per account and per comment/subject
            ref_id = f"acc{account.id}_{task['ref']}"
            
            # Idempotency check
            existing = db.query(ProcessedEvent).filter(
                ProcessedEvent.event_type == event_type,
                ProcessedEvent.reference_id == ref_id
            ).first()
            
            if existing:
                continue

            logger.info(f"Processing mention: {ref_id}")
            
            # Construct payload
            payload = {
                "action": "created" if event_type == "issue_comment" else "opened",
                "repository": repository,
                "sender": task["sender"]
            }
            
            if event_type == "issue_comment":
                payload["issue"] = issue
                payload["comment"] = task["item"]
            else:
                # issue or pull_request
                if subject_type == "Issue":
                    payload["issue"] = task["item"]
                else:
                    payload["pull_request"] = task["item"]

            try:
                # Record before processing to prevent loops if something goes wrong
                event = ProcessedEvent(event_type=event_type, reference_id=ref_id)
                db.add(event)
                db.commit()
                
                await processor.process(event_type, payload, db)
            except Exception as e:
                logger.error(f"Error processing mention {ref_id}: {e}")
                db.rollback()

    except Exception as e:
        logger.error(f"Failed to handle thread for notification {note_id}: {e}", exc_info=True)


def run_polling_task():
    """Synchronous wrapper for APScheduler."""
    asyncio.run(poll_notifications())
