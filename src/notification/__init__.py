"""Notification integrations for TradingBrain."""

from src.notification.email_sender import EmailSendResult, load_email_config, send_review_email

__all__ = ["EmailSendResult", "load_email_config", "send_review_email"]
