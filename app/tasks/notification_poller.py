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
    """Process a single notification using reactions-based tracking.

    Uses 'eyes' reaction to mark processed items:
    - Before processing: check if bot already has 'eyes' reaction → skip if yes
    - After processing: add 'eyes' reaction to the processed comment/issue
    """
    note_id = note.get("id")
    subject = note.get("subject", {})
    subject_type = subject.get("type")  # "Issue", "PullRequest"
    repository = note.get("repository", {})
    bot_username = account.gitea_username

    logger.info(f"handle_notification: note_id={note_id}, subject_type={subject_type}, repository={repository.get('full_name')}")

    if not note_id or not subject_type or not repository:
        logger.warning(f"Missing required fields: note_id={note_id}, subject_type={subject_type}")
        return

    if subject_type not in ["Issue", "PullRequest", "Pull"]:
        # For non-issue/PR notifications, just mark as read
        logger.info(f"Skipping notification {note_id}: type={subject_type} (not Issue/PullRequest/Pull)")
        await client.mark_notification_as_read(note_id)
        return

    # 1. Mark as read immediately to clear Gitea state
    await client.mark_notification_as_read(note_id)
    logger.info(f"Marked notification {note_id} as read, analyzing thread...")

    try:
        owner = repository.get("owner", {}).get("login") or repository.get("full_name", "").split("/")[0]
        repo_name = repository.get("name") or repository.get("full_name", "").split("/")[1]
        issue_url = subject.get("url", "")
        logger.info(f"Parsing: owner={owner}, repo={repo_name}, issue_url={issue_url}")

        if not issue_url:
            logger.warning(f"No issue_url in notification subject")
            return

        issue_number = int(issue_url.split("/")[-1])
        logger.info(f"Fetching comments for #{issue_number} in {owner}/{repo_name}")

        # 2. Fetch all comments for this thread
        comments = await client.get_issue_comments(owner, repo_name, issue_number)
        logger.info(f"Found {len(comments)} comments in thread #{issue_number}")

        # 3. Fetch issue/PR details
        issue = await client.get_issue(owner, repo_name, issue_number)

        # 4. Collect all @mentions that haven't been processed (no 'eyes' reaction)
        to_process = []

        # Check issue body for mentions
        issue_body = issue.get("body") or ""
        if f"@{bot_username}" in issue_body:
            # Check if bot already processed this issue (has 'eyes' reaction on issue)
            has_reaction = await client.has_bot_reaction(owner, repo_name, issue_number, None, "eyes", bot_username)
            if not has_reaction:
                logger.info(f"Found unprocessed mention in issue/PR body")
                is_pr = subject_type in ["Pull", "PullRequest"] or issue.get("pull_request")
                event_type = "pull_request" if is_pr else "issues"
                to_process.append({
                    "type": event_type,
                    "ref": f"subject_{subject_type}_{issue_number}",
                    "item": issue,
                    "sender": issue.get("user", {}),
                    "is_issue_body": True  # Mark as issue body for reaction placement
                })
            else:
                logger.info(f"Issue body already processed (has eyes reaction)")

        # Check comments for mentions
        comments.sort(key=lambda x: x.get("created_at", ""))
        for comment in comments:
            comment_user = comment.get("user", {}).get("login", "")
            comment_body = comment.get("body") or ""
            comment_id = comment.get("id")

            # Skip bot's own comments (also check hooray reaction as extra protection)
            if comment_user == bot_username:
                continue

            # Extra check: skip if comment has hooray reaction from bot (bot's posted comment)
            has_hooray = await client.has_bot_reaction(owner, repo_name, issue_number, comment_id, "hooray", bot_username)
            if has_hooray:
                logger.info(f"Comment #{comment_id} is bot's posted comment (has hooray reaction), skipping")
                continue

            if f"@{bot_username}" in comment_body:
                # Check if bot already processed this comment (has 'eyes' reaction)
                has_eyes = await client.has_bot_reaction(owner, repo_name, issue_number, comment_id, "eyes", bot_username)
                if not has_eyes:
                    logger.info(f"Found unprocessed mention in comment #{comment_id} by @{comment_user}")
                    to_process.append({
                        "type": "issue_comment",
                        "ref": f"comment_{comment_id}",
                        "item": comment,
                        "sender": comment.get("user", {}),
                        "comment_id": comment_id  # For adding reaction after processing
                    })
                else:
                    logger.info(f"Comment #{comment_id} already processed (has eyes reaction)")

        if not to_process:
            logger.info(f"No new mentions for @{bot_username} found in thread #{issue_number}")
            return

        logger.info(f"Found {len(to_process)} new mentions to process in thread #{issue_number}")

        # 5. Batch add 'eyes' reactions BEFORE processing any of them
        # This prevents re-processing by next poll if AI takes long time
        for task in to_process:
            ref_id = f"acc{account.id}_{task['ref']}"
            # Skip if already processed (db check)
            existing = db.query(ProcessedEvent).filter(
                ProcessedEvent.event_type == task["type"],
                ProcessedEvent.reference_id == ref_id
            ).first()
            if existing:
                continue
            try:
                if task.get("is_issue_body"):
                    await client.add_issue_reaction(owner, repo_name, issue_number, "eyes")
                    logger.info(f"Added eyes reaction to issue #{issue_number}")
                elif task.get("comment_id"):
                    await client.add_comment_reaction(owner, repo_name, task["comment_id"], "eyes")
                    logger.info(f"Added eyes reaction to comment #{task['comment_id']}")
            except Exception as e:
                logger.warning(f"Failed to add eyes reaction: {e}")

        # 6. Now process each mention (all have eyes reactions already)
        processor = EventProcessor(instance, account, db)
        for task in to_process:
            event_type = task["type"]
            ref_id = f"acc{account.id}_{task['ref']}"

            # Idempotency check (secondary safety)
            existing = db.query(ProcessedEvent).filter(
                ProcessedEvent.event_type == event_type,
                ProcessedEvent.reference_id == ref_id
            ).first()

            if existing:
                logger.info(f"Skipping already processed (db): {ref_id}")
                continue

            logger.info(f"Processing mention: {ref_id}")

            # Construct payload
            payload = {
                "action": "created" if event_type == "issue_comment" else "opened",
                "repository": repository,
                "sender": task["sender"]
            }

            if event_type == "issue_comment":
                payload["comment"] = task["item"]
                if subject_type in ["Pull", "PullRequest"] or issue.get("pull_request"):
                    payload["pull_request"] = issue
                else:
                    payload["issue"] = issue
            else:
                if subject_type in ["Issue"]:
                    payload["issue"] = task["item"]
                else:
                    payload["pull_request"] = task["item"]

            try:
                event = ProcessedEvent(event_type=event_type, reference_id=ref_id)
                db.add(event)
                db.commit()

                await processor.process(event_type, payload, db)

                # Add hooray reaction to trigger comment/issue to indicate "processing completed"
                try:
                    if task.get("comment_id"):
                        await client.add_comment_reaction(owner, repo_name, task["comment_id"], "hooray")
                        logger.info(f"Added hooray reaction to comment #{task['comment_id']} (completed)")
                    elif task.get("is_issue_body"):
                        await client.add_issue_reaction(owner, repo_name, issue_number, "hooray")
                        logger.info(f"Added hooray reaction to issue #{issue_number} (completed)")
                except Exception as e:
                    logger.warning(f"Failed to add hooray reaction: {e}")

            except Exception as e:
                logger.error(f"Error processing mention {ref_id}: {e}")
                db.rollback()

    except Exception as e:
        logger.error(f"Failed to handle thread for notification {note_id}: {e}", exc_info=True)


def run_polling_task():
    """Synchronous wrapper for APScheduler."""
    asyncio.run(poll_notifications())
