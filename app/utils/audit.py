"""Security audit logging utilities."""

import json
import logging
from typing import Optional
from sqlalchemy.orm import Session
from ..models import AuditLog

logger = logging.getLogger(__name__)


# Action constants
ACTION_ADMIN_LOGIN = "ADMIN_LOGIN"
ACTION_ADMIN_LOGOUT = "ADMIN_LOGOUT"
ACTION_ADMIN_PASSWORD_CHANGE = "ADMIN_PASSWORD_CHANGE"
ACTION_ADMIN_TOTP_ENABLE = "ADMIN_TOTP_ENABLE"
ACTION_ADMIN_TOTP_DISABLE = "ADMIN_TOTP_DISABLE"
ACTION_WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"
ACTION_WEBHOOK_FAILED = "WEBHOOK_FAILED"
ACTION_CONFIG_CHANGED = "CONFIG_CHANGED"
ACTION_OAUTH_AUTHORIZED = "OAUTH_AUTHORIZED"
ACTION_OAUTH_FAILED = "OAUTH_FAILED"
ACTION_INSTANCE_CREATED = "INSTANCE_CREATED"
ACTION_INSTANCE_DELETED = "INSTANCE_DELETED"
ACTION_ACCOUNT_DELETED = "ACCOUNT_DELETED"


def log_audit(
    db: Session,
    action: str,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    status: str = "SUCCESS",
    details: Optional[dict] = None
) -> None:
    """Log a security audit event to the database.
    
    Args:
        db: Database session
        action: The action type (use ACTION_* constants)
        username: Username who performed the action
        ip_address: Client IP address
        status: "SUCCESS" or "FAILURE"
        details: Additional details as a dictionary
    """
    try:
        details_json = json.dumps(details) if details else None
        
        audit_log = AuditLog(
            action=action,
            username=username,
            ip_address=ip_address,
            status=status,
            details=details_json
        )
        db.add(audit_log)
        db.commit()
        
        # Also log to application logger for real-time monitoring
        log_message = f"AUDIT: {action} | user={username} | ip={ip_address} | status={status}"
        if details:
            log_message += f" | details={details_json}"
        
        if status == "SUCCESS":
            logger.info(log_message)
        else:
            logger.warning(log_message)
            
    except Exception as e:
        # Never let audit logging break the application
        logger.error(f"Failed to write audit log: {e}")


def log_webhook_event(
    db: Session,
    event_type: str,
    status: str,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None
) -> None:
    """Log a webhook-related event.
    
    Args:
        db: Database session
        event_type: The Gitea event type (e.g., "issue_comment")
        status: "SUCCESS" or "FAILURE"
        details: Additional details
        ip_address: Client IP address
    """
    action = ACTION_WEBHOOK_RECEIVED if status == "SUCCESS" else ACTION_WEBHOOK_FAILED
    
    log_audit(
        db=db,
        action=action,
        username=None,  # Webhooks don't have usernames
        ip_address=ip_address,
        status=status,
        details=details
    )


def log_admin_action(
    db: Session,
    action: str,
    username: str,
    ip_address: Optional[str] = None,
    status: str = "SUCCESS",
    details: Optional[dict] = None
) -> None:
    """Log an admin action.
    
    Args:
        db: Database session
        action: The action type
        username: Admin username
        ip_address: Client IP address
        status: "SUCCESS" or "FAILURE"
        details: Additional details
    """
    log_audit(
        db=db,
        action=action,
        username=username,
        ip_address=ip_address,
        status=status,
        details=details
    )
