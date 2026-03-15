import enum


class UserRole(str, enum.Enum):
    PASSENGER = "passenger"
    AGENT = "agent"
    DRIVER = "driver"
    FLEET_MANAGER = "fleet_manager"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NO_SHOW = "no_show"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    WALLET = "wallet"
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"


class VehicleStatus(str, enum.Enum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class TripStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    BOARDING = "boarding"
    DEPARTED = "departed"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    DELAYED = "delayed"


class SeatStatus(str, enum.Enum):
    AVAILABLE = "available"
    LOCKED = "locked"
    BOOKED = "booked"


class GenderType(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class WalletTxType(str, enum.Enum):
    TOP_UP = "top_up"
    PAYMENT = "payment"
    REFUND = "refund"
    REFERRAL_BONUS = "referral_bonus"
    PROMO_CREDIT = "promo_credit"


class OTPPurpose(str, enum.Enum):
    EMAIL_VERIFICATION = "email_verification"
    PHONE_VERIFICATION = "phone_verification"
    PASSWORD_RESET = "password_reset"
    LOGIN = "login"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    IN_APP = "in_app"


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class PricingRuleType(str, enum.Enum):
    SURGE = "surge"
    DISCOUNT = "discount"
    TIME_BASED = "time_based"
    DEMAND_BASED = "demand_based"


class PricingModifierType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


BOOKING_REFERENCE_PREFIX = "ET"
BOOKING_REFERENCE_LENGTH = 6
DEFAULT_CURRENCY = "NGN"
DEFAULT_COUNTRY = "Nigeria"
