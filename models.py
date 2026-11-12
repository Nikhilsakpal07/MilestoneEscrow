import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text, JSON, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    firebase_uid = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    paypal_email = Column(String, nullable=True)  # Dedicated PayPal payout routing address
    wallet_address = Column(String, unique=True, index=True, nullable=False)
    wallet_private_key = Column(Text, nullable=True)  # Securely stored Ethereum private key
    me_id = Column(String, unique=True, index=True, nullable=False)  # Strictly immutable identifier
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, default=datetime.utcnow, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "firebase_uid": self.firebase_uid,
            "email": self.email,
            "paypal_email": self.paypal_email or self.email,
            "wallet_address": self.wallet_address,
            "me_id": self.me_id,
            "created_at": self.created_at.isoformat() if hasattr(self.created_at, "isoformat") else str(self.created_at),
            "last_login": self.last_login.isoformat() if hasattr(self.last_login, "isoformat") else (str(self.last_login) if self.last_login else None),
        }


class KYCStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class EscrowProvider(str, enum.Enum):
    PAYPAL = "PAYPAL"
    STRIPE = "STRIPE"
    USDC_SMART_CONTRACT = "USDC_SMART_CONTRACT"


class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    me_id = Column(String, unique=True, index=True, nullable=False)
    firebase_uid = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, index=True, nullable=True)
    github_username = Column(String, nullable=True)
    stripe_account_id = Column(String, nullable=True, index=True)
    kyc_status = Column(
        SAEnum(KYCStatus, name="kyc_status_enum", native_enum=False),
        default=KYCStatus.PENDING,
        nullable=False,
    )
    name = Column(String, nullable=True)
    created_at = Column(String, nullable=True)

    bounties = relationship("Bounty", back_populates="worker", foreign_keys="Bounty.worker_me_id")


class Sponsor(Base):
    __tablename__ = "sponsors"

    me_id = Column(String, primary_key=True, index=True)
    email = Column(String, nullable=True)
    name = Column(String, nullable=True)
    created_at = Column(String, nullable=True)

    bounties = relationship("Bounty", back_populates="sponsor")


class Bounty(Base):
    __tablename__ = "bounties"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    acceptance_criteria = Column(Text)
    amount_usd = Column(Float)
    remaining_amount_usd = Column(Float, nullable=True)  # Tracks remaining funds in escrow
    freelancer_email = Column(String)
    status = Column(String, default="AWAITING_FUNDS")  # AWAITING_FUNDS, LOCKED, IN_PROGRESS, PENDING_ADMIN, COMPLETED, REJECTED
    requires_human_approval = Column(Boolean, default=False)  # Hybrid Multi-Sig (> $5,000 threshold)
    freelancer_phone = Column(String, nullable=True, index=True)  # WhatsApp Field-Worker Integration
    
    # Escrow Provider (Multi-currency Stripe Connect / PayPal / USDC)
    escrow_provider = Column(
        SAEnum(EscrowProvider, name="escrow_provider_enum", native_enum=False),
        default=EscrowProvider.PAYPAL,
        nullable=False,
    )
    
    # Dual-Mode Studio ME-ID Foreign Keys
    worker_me_id = Column(String, ForeignKey("workers.me_id"), nullable=True, index=True)
    sponsor_me_id = Column(String, ForeignKey("sponsors.me_id"), nullable=True, index=True)

    tracking_number = Column(String, nullable=True, index=True)  # External Data Oracle (Logistics)
    shipping_status = Column(String, default="NOT_SHIPPED")  # NOT_SHIPPED, SHIPPED, IN_TRANSIT, DELIVERED
    
    # Predictive Risk Scoring
    risk_score = Column(String, default="LOW")  # LOW, MEDIUM, HIGH
    risk_factors = Column(JSON, default=list)
    risk_percentage = Column(Integer, default=15)
    risk_updated_at = Column(String, nullable=True)

    worker = relationship("Worker", back_populates="bounties", foreign_keys=[worker_me_id])
    sponsor = relationship("Sponsor", back_populates="bounties", foreign_keys=[sponsor_me_id])
    escrow = relationship("Escrow", back_populates="bounty", uselist=False)
    audit_logs = relationship("AuditLog", back_populates="bounty")
    milestones = relationship("Milestone", back_populates="bounty", cascade="all, delete-orphan", order_by="Milestone.id")


class Milestone(Base):
    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True, index=True)
    bounty_id = Column(Integer, ForeignKey("bounties.id"), index=True)
    description = Column(Text)
    deadline = Column(String)  # e.g., '2026-12-01'
    percentage = Column(Float)  # e.g., 50.0 (%)
    amount_usd = Column(Float)  # e.g., 250.00 ($)
    status = Column(String, default="LOCKED")  # LOCKED, AUDITING, PENDING_ADMIN, COMPLETED, REJECTED
    paypal_payout_batch_id = Column(String, nullable=True)
    stripe_transfer_id = Column(String, nullable=True)
    
    # JSONB columns for dynamic criteria strings and PayPal authorization IDs
    criteria_strings = Column(JSONB, default=list)  # Dynamic acceptance criteria strings
    dynamic_criteria = Column(JSONB, default=list)  # Alias for dynamic criteria strings
    paypal_authorization_ids = Column(JSONB, default=list)  # Array of PayPal authorization IDs (JSONB)
    authorization_ids = Column(JSONB, default=list)  # Retain backward compatibility

    requires_human_approval = Column(Boolean, default=False)  # Hybrid Multi-Sig (> $5,000)
    tracking_number = Column(String, nullable=True, index=True)  # Logistics tracking number
    shipping_status = Column(String, default="NOT_SHIPPED")  # NOT_SHIPPED, SHIPPED, IN_TRANSIT, DELIVERED
    
    # Automated Compliance Certificate (PDF)
    certificate_id = Column(String, nullable=True, index=True)
    certificate_pdf_path = Column(String, nullable=True)

    # Real Milestone Submission & Persisted AI Audit Dossier
    submitted_github_url = Column(String, nullable=True)
    submission_notes = Column(Text, nullable=True)
    audit_result = Column(JSONB, nullable=True)
    audited_at = Column(DateTime, nullable=True)

    bounty = relationship("Bounty", back_populates="milestones")
    audit_logs = relationship("AuditLog", back_populates="milestone")


class CodeFingerprint(Base):
    __tablename__ = "code_fingerprints"

    id = Column(Integer, primary_key=True, index=True)
    freelancer_email = Column(String, index=True)
    bounty_id = Column(Integer, ForeignKey("bounties.id"))
    milestone_id = Column(Integer, nullable=True)
    github_url = Column(String, nullable=True)
    ast_hash = Column(String, index=True)
    naming_style = Column(JSON, default=dict)
    identifiers = Column(JSON, default=list)
    code_summary = Column(Text, nullable=True)
    created_at = Column(String)


class Escrow(Base):
    __tablename__ = "escrows"

    id = Column(Integer, primary_key=True, index=True)
    bounty_id = Column(Integer, ForeignKey("bounties.id"))
    paypal_order_id = Column(String, unique=True, index=True, nullable=True)
    paypal_authorization_id = Column(String, unique=True, nullable=True)
    paypal_capture_id = Column(String, unique=True, nullable=True)
    
    # Multi-Sponsor Pooling
    paypal_order_ids = Column(JSON, default=list)
    paypal_authorization_ids = Column(JSON, default=list)
    paypal_capture_ids = Column(JSON, default=list)

    # Stripe Escrow & Multi-Currency
    stripe_payment_intent_id = Column(String, unique=True, index=True, nullable=True)
    stripe_transfer_id = Column(String, nullable=True)
    currency = Column(String, default="USD")

    bounty = relationship("Bounty", back_populates="escrow")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    bounty_id = Column(Integer, ForeignKey("bounties.id"))
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=True)
    trajectory_id = Column(String)
    passed = Column(Boolean)
    ai_feedback = Column(Text)

    bounty = relationship("Bounty", back_populates="audit_logs")
    milestone = relationship("Milestone", back_populates="audit_logs")
