from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import MinLengthValidator, MaxLengthValidator
import uuid


class WhatsAppUser(models.Model):
    """
    Model to store WhatsApp user information and session data
    """
    
    class LanguagePreference(models.TextChoices):
        ARABIC = 'ar', 'Arabic'
        ENGLISH = 'en', 'English'
        FRENCH = 'fr', 'French'
        SPANISH = 'es', 'Spanish'
        GERMAN = 'de', 'German'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        validators=[MinLengthValidator(10), MaxLengthValidator(20)],
        help_text="WhatsApp phone number without whatsapp: prefix"
    )
    profile_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="User's WhatsApp profile name"
    )
    language_preference = models.CharField(
        max_length=10,
        choices=LanguagePreference.choices,
        default=LanguagePreference.ARABIC,
        help_text="User's preferred language"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this user is active"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last message received"
    )
    message_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of messages exchanged"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional user metadata"
    )
    
    class Meta:
        db_table = 'whatsapp_users'
        verbose_name = 'WhatsApp User'
        verbose_name_plural = 'WhatsApp Users'
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['is_active']),
            models.Index(fields=['language_preference']),
            models.Index(fields=['last_message_at']),
        ]
        constraints = []

    def __str__(self):
        name = self.profile_name or "Unknown"
        return f"{name} ({self.phone_number})"
    
    def increment_message_count(self):
        """Increment message count"""
        self.message_count += 1
        self.save(update_fields=['message_count'])
    
    def update_last_message_time(self):
        """Update last message timestamp"""
        self.last_message_at = timezone.now()
        self.save(update_fields=['last_message_at'])


class WhatsAppSession(models.Model):
    """
    Model to store user session context for conversations
    """
    
    class SessionType(models.TextChoices):
        RAG_CHAT = 'rag_chat', 'RAG Chat'
        SIMPLE_CHAT = 'simple_chat', 'Simple Chat'
        ASK_QUESTIONS = 'ask_questions', 'Ask Questions'
        ASK_UNA = 'ask_una', 'Ask UNA'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        WhatsAppUser,
        on_delete=models.CASCADE,
        related_name='sessions',
        help_text="WhatsApp user this session belongs to"
    )
    session_type = models.CharField(
        max_length=20,
        choices=SessionType.choices,
        default=SessionType.ASK_QUESTIONS,
        help_text="Type of chat session"
    )
    context_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Store session context like conversation history, current question tree node, etc."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this session is active"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this session expires"
    )
    message_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of messages in this session"
    )
    
    class Meta:
        db_table = 'whatsapp_sessions'
        verbose_name = 'WhatsApp Session'
        verbose_name_plural = 'WhatsApp Sessions'
        ordering = ['-updated_at', '-created_at']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['session_type']),
            models.Index(fields=['is_active']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Session {self.id} - {self.user.phone_number} ({self.session_type})"
    
    def is_expired(self):
        """Check if session has expired"""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at
    
    def extend_session(self, hours=24):
        """Extend session expiration time"""
        self.expires_at = timezone.now() + timezone.timedelta(hours=hours)
        self.save(update_fields=['expires_at', 'updated_at'])
    
    def increment_message_count(self):
        """Increment message count in session"""
        self.message_count += 1
        self.save(update_fields=['message_count', 'updated_at'])


class WhatsAppMessage(models.Model):
    """
    Model to log all WhatsApp messages for analytics and debugging
    """
    
    class MessageType(models.TextChoices):
        INCOMING = 'incoming', 'Incoming'
        OUTGOING = 'outgoing', 'Outgoing'
    
    class MessageStatus(models.TextChoices):
        SENT = 'sent', 'Sent'
        DELIVERED = 'delivered', 'Delivered'
        READ = 'read', 'Read'
        FAILED = 'failed', 'Failed'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        WhatsAppUser,
        on_delete=models.CASCADE,
        related_name='messages',
        help_text="WhatsApp user who sent/received this message"
    )
    session = models.ForeignKey(
        WhatsAppSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='messages',
        help_text="Session this message belongs to"
    )
    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        help_text="Whether this is an incoming or outgoing message"
    )
    message_text = models.TextField(
        validators=[MinLengthValidator(1)],
        help_text="The actual message content"
    )
    whatsapp_message_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Meta WhatsApp message ID"
    )
    status = models.CharField(
        max_length=20,
        choices=MessageStatus.choices,
        default=MessageStatus.SENT,
        help_text="Delivery status of the message"
    )
    api_endpoint_used = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Which chat API was used to generate response"
    )
    response_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Response time in milliseconds"
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if message failed"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional message metadata"
    )
    
    class Meta:
        db_table = 'whatsapp_messages'
        verbose_name = 'WhatsApp Message'
        verbose_name_plural = 'WhatsApp Messages'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['session']),
            models.Index(fields=['message_type']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.message_type} - {self.user.phone_number} - {self.created_at}"


class WhatsAppAnalytics(models.Model):
    """
    Model to store aggregated analytics data
    """
    date = models.DateField(unique=True, db_index=True)
    total_messages = models.PositiveIntegerField(default=0)
    incoming_messages = models.PositiveIntegerField(default=0)
    outgoing_messages = models.PositiveIntegerField(default=0)
    unique_users = models.PositiveIntegerField(default=0)
    api_usage = models.JSONField(
        default=dict,
        help_text="Usage count per API endpoint"
    )
    error_count = models.PositiveIntegerField(default=0)
    avg_response_time = models.FloatField(
        default=0.0,
        help_text="Average response time in milliseconds"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'whatsapp_analytics'
        verbose_name = 'WhatsApp Analytics'
        verbose_name_plural = 'WhatsApp Analytics'
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date']),
        ]
    
    def __str__(self):
        return f"Analytics - {self.date}"