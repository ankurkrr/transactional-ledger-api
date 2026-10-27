"""Unit tests for Pydantic v2 schemas: validation, serialization, and constraints."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ledger.schemas.account import (
    AccountCreateRequest,
    AccountResponse,
    BalanceResponse,
)
from ledger.schemas.error import (
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
)
from ledger.schemas.transaction import (
    PaginationMetadata,
    PaginationParams,
    TransactionCreateRequest,
    TransactionHistoryResponse,
    TransactionResponse,
    paise_to_rupees,
    rupees_to_paise,
)
from ledger.schemas.transfer import (
    TransferCreateRequest,
    TransferResponse,
)


class TestConversionFunctions:
    """Tests for exact and safe INR rupee-to-paise and paise-to-rupee conversions."""

    @pytest.mark.parametrize(
        ("input_rupees", "expected_paise"),
        [
            ("1", 100),
            ("1.1", 110),
            ("1.10", 110),
            ("450.30", 45030),
            ("450.5", 45050),
            ("450.50", 45050),
            ("5000.00", 500000),
            ("0.01", 1),
            ("92233720368547758.07", 9_223_372_036_854_775_807),
        ],
    )
    def test_rupees_to_paise_exact(self, input_rupees: str, expected_paise: int) -> None:
        """Standard rupee string representations convert exactly to integer paise."""
        assert rupees_to_paise(input_rupees) == expected_paise

    @pytest.mark.parametrize(
        ("input_paise", "expected_rupees"),
        [
            (100, "1.00"),
            (110, "1.10"),
            (45030, "450.30"),
            (500000, "5000.00"),
            (0, "0.00"),
            (5, "0.05"),
            (9_223_372_036_854_775_807, "92233720368547758.07"),
        ],
    )
    def test_paise_to_rupees_exact(self, input_paise: int, expected_rupees: str) -> None:
        """Integer paise format into fixed two-decimal-place strings."""
        assert paise_to_rupees(input_paise) == expected_rupees

    @pytest.mark.parametrize(
        "invalid_decimal",
        [
            "1.001",
            "450.301",
            "0.0001",
            "100.999",
        ],
    )
    def test_rupees_to_paise_rejects_more_than_two_decimals(self, invalid_decimal: str) -> None:
        """Monetary values with more than two decimal places must be rejected."""
        with pytest.raises(ValueError, match="at most 2 decimal places"):
            rupees_to_paise(invalid_decimal)

    @pytest.mark.parametrize(
        "non_positive",
        [
            "0",
            "0.00",
            "-1",
            "-1.00",
            "-0.01",
            "-5000.00",
        ],
    )
    def test_rupees_to_paise_rejects_zero_and_negative(self, non_positive: str) -> None:
        """Zero and negative monetary amounts must be rejected."""
        with pytest.raises(ValueError):
            rupees_to_paise(non_positive)

    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "   ",
            "1.",
            ".50",
            "abc",
            "1e2",
            "NaN",
            "Inf",
            "-Inf",
            " 450.30 ",
            "450.30\x00",
        ],
    )
    def test_rupees_to_paise_rejects_malformed_strings(self, malformed: str) -> None:
        """Malformed, non-finite, empty, or whitespace strings must be rejected."""
        with pytest.raises(ValueError):
            rupees_to_paise(malformed)

    @pytest.mark.parametrize(
        "overflow_rupees",
        [
            "92233720368547758.08",
            "100000000000000000.00",
            "999999999999999999999999.00",
        ],
    )
    def test_rupees_to_paise_rejects_bigint_overflow(self, overflow_rupees: str) -> None:
        """Amounts exceeding signed 64-bit integer limit must be rejected."""
        with pytest.raises(ValueError, match="exceeds maximum supported"):
            rupees_to_paise(overflow_rupees)

    @pytest.mark.parametrize(
        "non_string",
        [
            1,
            1.1,
            100.0,
            True,
            False,
            None,
        ],
    )
    def test_rupees_to_paise_rejects_non_string_types(self, non_string: object) -> None:
        """Non-string inputs to rupees_to_paise must raise ValueError."""
        with pytest.raises(ValueError, match="must be a decimal string"):
            rupees_to_paise(non_string)  # pyright: ignore[reportArgumentType]


class TestAccountSchemas:
    """Tests for Account request and response schemas."""

    def test_account_create_valid(self) -> None:
        """Valid account creation payload succeeds with trimmed whitespace."""
        payload = {"name": "  Jane Doe  "}
        req = AccountCreateRequest.model_validate(payload)
        assert req.name == "Jane Doe"

    def test_account_create_rejects_empty_and_whitespace(self) -> None:
        """Account name cannot be empty string or whitespace-only."""
        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": ""})
        assert "name" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "   "})
        assert "name" in str(exc_info.value)

    def test_account_create_rejects_excessive_length(self) -> None:
        """Account name cannot exceed 255 characters."""
        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "A" * 256})
        assert "name" in str(exc_info.value)

    def test_account_create_forbids_extra_fields(self) -> None:
        """Client cannot supply initial balance, identifier, currency, or arbitrary extra fields."""
        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "Jane", "balance": "100.00"})
        assert "extra_forbidden" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "Jane", "balance_minor": 10000})
        assert "extra_forbidden" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "Jane", "currency": "USD"})
        assert "extra_forbidden" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": "Jane", "id": str(uuid.uuid4())})
        assert "extra_forbidden" in str(exc_info.value)

    def test_account_response_serialization(self) -> None:
        """AccountResponse serializes balance as fixed 2-decimal rupee string."""
        account_id = uuid.uuid4()
        now = datetime.now(UTC)
        res = AccountResponse(
            id=account_id,
            name="Jane Doe",
            balance="0.00",
            currency="INR",
            created_at=now,
            updated_at=now,
        )
        data = res.model_dump(mode="json")
        assert data["id"] == str(account_id)
        assert data["name"] == "Jane Doe"
        assert data["balance"] == "0.00"
        assert "balance_minor" not in data
        assert data["currency"] == "INR"
        assert "created_at" in data
        assert "updated_at" in data

    def test_balance_response_alias_resolution(self) -> None:
        """BalanceResponse supports both 'account_id' and ORM 'id', and formats balance."""
        account_id = uuid.uuid4()
        now = datetime.now(UTC)

        # Direct account_id with rupee balance
        res1 = BalanceResponse.model_validate(
            {
                "account_id": account_id,
                "balance": "150.00",
                "currency": "INR",
                "updated_at": now,
            }
        )
        assert res1.account_id == account_id
        assert res1.balance == "150.00"
        assert res1.model_dump(mode="json")["balance"] == "150.00"

        # From ORM-like structure where attribute is 'id' and 'balance_minor'
        class MockAccountORM:
            def __init__(self) -> None:
                self.id = account_id
                self.balance_minor = 20000
                self.currency = "INR"
                self.updated_at = now

        res2 = BalanceResponse.model_validate(MockAccountORM())
        assert res2.account_id == account_id
        assert res2.balance == "200.00"
        assert res2.model_dump(mode="json")["account_id"] == str(account_id)
        assert res2.model_dump(mode="json")["balance"] == "200.00"


class TestTransactionSchemas:
    """Tests for Transaction request, response, and pagination schemas."""

    @pytest.mark.parametrize("tx_type", ["credit", "debit"])
    def test_transaction_create_valid(self, tx_type: str) -> None:
        """Valid credit and debit transaction payloads succeed with rupee string."""
        req = TransactionCreateRequest.model_validate(
            {
                "type": tx_type,
                "amount": "450.30",
                "description": "  Invoice payment  ",
                "reference_id": "  REF-001  ",
            }
        )
        assert req.type == tx_type
        assert req.amount == "450.30"
        assert req.amount_minor == 45030
        assert req.description == "Invoice payment"
        assert req.reference_id == "REF-001"

    def test_transaction_create_rejects_invalid_type(self) -> None:
        """Transaction type must be strictly 'credit' or 'debit'."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "refund",
                    "amount": "50.00",
                    "description": "Valid narrative",
                    "reference_id": "REF-001",
                }
            )
        assert "type" in str(exc_info.value)

    @pytest.mark.parametrize("invalid_amount", ["0", "0.00", "-1", "-1.00", "-50.00"])
    def test_transaction_create_rejects_non_positive_amounts(self, invalid_amount: str) -> None:
        """Transaction amount must be strictly greater than zero."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": invalid_amount,
                    "description": "Payment",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    @pytest.mark.parametrize("invalid_type_amount", [12.50, 100.0, 5000, 100])
    def test_transaction_create_rejects_floats_and_integers_strict(
        self, invalid_type_amount: object
    ) -> None:
        """Strict validation rejects JSON numbers; strings are required."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": invalid_type_amount,
                    "description": "Payment",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    @pytest.mark.parametrize("invalid_decimal", ["1.001", "450.301", "10.123"])
    def test_transaction_create_rejects_more_than_two_decimals(self, invalid_decimal: str) -> None:
        """Amounts with more than two decimal places are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": invalid_decimal,
                    "description": "Payment",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    @pytest.mark.parametrize("bool_val", [True, False])
    def test_transaction_create_rejects_booleans(self, bool_val: bool) -> None:
        """Strict validation rejects boolean types as monetary amounts."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": bool_val,
                    "description": "Payment",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    @pytest.mark.parametrize("exceeding_val", ["92233720368547758.08", "100000000000000000.00"])
    def test_transaction_create_rejects_exceeding_signed_bigint_max(
        self, exceeding_val: str
    ) -> None:
        """Amounts exceeding PostgreSQL signed BIGINT max are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": exceeding_val,
                    "description": "Payment",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("valid_bound", "expected_paise"),
        [
            ("0.01", 1),
            ("1.00", 100),
            ("92233720368547758.07", 9_223_372_036_854_775_807),
        ],
    )
    def test_transaction_create_accepts_valid_boundary_amounts(
        self, valid_bound: str, expected_paise: int
    ) -> None:
        """Boundary amounts pass validation and map to exact integer paise."""
        req = TransactionCreateRequest.model_validate(
            {
                "type": "credit",
                "amount": valid_bound,
                "description": "Boundary Payment",
                "reference_id": f"REF-BOUND-{expected_paise}",
            }
        )
        assert req.amount == valid_bound
        assert req.amount_minor == expected_paise

    def test_transaction_create_rejects_whitespace_fields(self) -> None:
        """Description, reference_id, and amount cannot be whitespace-only."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "description": "   ",
                    "reference_id": "REF-001",
                }
            )
        assert "description" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "description": "Valid description",
                    "reference_id": "   ",
                }
            )
        assert "reference_id" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "   ",
                    "description": "Valid description",
                    "reference_id": "REF-001",
                }
            )
        assert "amount" in str(exc_info.value)

    def test_transaction_create_rejects_excessive_lengths(self) -> None:
        """Description (max 1000) and reference_id (max 128) enforce limits."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "description": "X" * 1001,
                    "reference_id": "REF-001",
                }
            )
        assert "description" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "description": "Valid description",
                    "reference_id": "R" * 129,
                }
            )
        assert "reference_id" in str(exc_info.value)

    def test_transaction_create_forbids_extra_fields(self) -> None:
        """Client cannot supply amount_minor, balance_after, id, or other internal fields."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "description": "Valid description",
                    "reference_id": "REF-001",
                    "balance_after": "500.00",
                }
            )
        assert "extra_forbidden" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "10.00",
                    "amount_minor": 1000,
                    "description": "Valid description",
                    "reference_id": "REF-001",
                }
            )
        assert "extra_forbidden" in str(exc_info.value)

    def test_transaction_response_serialization(self) -> None:
        """TransactionResponse serializes with amount, balance_after, and currency in INR."""
        tx_id = uuid.uuid4()
        account_id = uuid.uuid4()
        now = datetime.now(UTC)

        res = TransactionResponse(
            id=tx_id,
            account_id=account_id,
            type="credit",
            amount="50.00",
            balance_after="150.00",
            currency="INR",
            description="Initial deposit",
            reference_id="REF-DEP-01",
            transfer_id=None,
            created_at=now,
        )
        data = res.model_dump(mode="json")
        assert data["id"] == str(tx_id)
        assert data["account_id"] == str(account_id)
        assert data["type"] == "credit"
        assert data["amount"] == "50.00"
        assert data["balance_after"] == "150.00"
        assert data["currency"] == "INR"
        assert "amount_minor" not in data
        assert "balance_after_minor" not in data
        assert data["transfer_id"] is None

    def test_pagination_params_and_history_envelope(self) -> None:
        """Pagination parameters enforce bounds (page>=1, 1<=limit<=100)."""
        # Defaults
        params = PaginationParams()
        assert params.page == 1
        assert params.limit == 20

        # Boundary checks
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

        with pytest.raises(ValidationError):
            PaginationParams(limit=0)

        with pytest.raises(ValidationError):
            PaginationParams(limit=101)

        # History envelope
        meta = PaginationMetadata(
            current_page=1,
            page_size=20,
            total_records=1,
            total_pages=1,
        )
        tx_id = uuid.uuid4()
        account_id = uuid.uuid4()
        now = datetime.now(UTC)
        tx_item = TransactionResponse(
            id=tx_id,
            account_id=account_id,
            type="debit",
            amount="10.00",
            balance_after="90.00",
            currency="INR",
            description="Fee",
            reference_id="FEE-01",
            transfer_id=None,
            created_at=now,
        )
        history = TransactionHistoryResponse(items=[tx_item], pagination=meta)
        assert len(history.items) == 1
        assert history.pagination.total_records == 1
        assert history.items[0].amount == "10.00"
        assert history.items[0].balance_after == "90.00"
        assert history.items[0].currency == "INR"


class TestTransferSchemas:
    """Tests for Transfer request and response schemas."""

    def test_transfer_create_valid(self) -> None:
        """Valid transfer creation payload passes validation with rupee string."""
        src_id = uuid.uuid4()
        dst_id = uuid.uuid4()
        req = TransferCreateRequest.model_validate(
            {
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount": "25.00",
                "description": "  Split lunch bill  ",
                "reference_id": "  XFER-2026-001  ",
            }
        )
        assert req.source_account_id == src_id
        assert req.destination_account_id == dst_id
        assert req.amount == "25.00"
        assert req.amount_minor == 2500
        assert req.description == "Split lunch bill"
        assert req.reference_id == "XFER-2026-001"

    def test_transfer_create_rejects_identical_accounts(self) -> None:
        """Transfer between identical source and destination accounts is rejected."""
        same_id = uuid.uuid4()
        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": same_id,
                    "destination_account_id": same_id,
                    "amount": "25.00",
                    "description": "Self-transfer attempt",
                    "reference_id": "XFER-SAME-01",
                }
            )
        assert "cannot be identical" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "invalid_amount",
        [
            0,
            -100,
            25.50,
            2500,
            "0",
            "0.00",
            "-25.00",
            "25.555",
            "NaN",
            "Inf",
        ],
    )
    def test_transfer_create_rejects_invalid_amounts(self, invalid_amount: object) -> None:
        """Transfer amounts must be strictly positive decimal strings with <= 2 decimals."""
        with pytest.raises(ValidationError):
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": invalid_amount,
                    "description": "Payment",
                    "reference_id": "XFER-001",
                }
            )

    def test_transfer_create_forbids_extra_fields(self) -> None:
        """Client cannot specify status, transfer_id, amount_minor, or timestamps."""
        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": "10.00",
                    "description": "Valid",
                    "reference_id": "XFER-001",
                    "status": "completed",
                }
            )
        assert "extra_forbidden" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": "10.00",
                    "amount_minor": 1000,
                    "description": "Valid",
                    "reference_id": "XFER-001",
                }
            )
        assert "extra_forbidden" in str(exc_info.value)

    def test_transfer_response_alias_resolution(self) -> None:
        """TransferResponse supports both transfer_id and ORM id, and formats amount."""
        t_id = uuid.uuid4()
        src_id = uuid.uuid4()
        dst_id = uuid.uuid4()
        now = datetime.now(UTC)

        # From dict with transfer_id and amount
        res1 = TransferResponse.model_validate(
            {
                "transfer_id": t_id,
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount": "50.00",
                "currency": "INR",
                "status": "completed",
                "reference_id": "XFER-01",
                "description": "Reimbursement",
                "created_at": now,
            }
        )
        assert res1.transfer_id == t_id
        assert res1.amount == "50.00"
        assert res1.currency == "INR"
        assert res1.model_dump(mode="json")["transfer_id"] == str(t_id)
        assert res1.model_dump(mode="json")["amount"] == "50.00"

        # From ORM-like object where primary key attribute is 'id' and 'amount_minor'
        class MockTransferORM:
            def __init__(self) -> None:
                self.id = t_id
                self.source_account_id = src_id
                self.destination_account_id = dst_id
                self.amount_minor = 5000
                self.status = "completed"
                self.reference_id = "XFER-01"
                self.description = "Reimbursement"
                self.created_at = now

        res2 = TransferResponse.model_validate(MockTransferORM())
        assert res2.transfer_id == t_id
        assert res2.amount == "50.00"
        assert res2.currency == "INR"
        assert res2.model_dump(mode="json")["transfer_id"] == str(t_id)
        assert res2.model_dump(mode="json")["amount"] == "50.00"


class TestErrorSchemas:
    """Tests for Standardized Error envelope schemas."""

    def test_error_response_envelope(self) -> None:
        """ErrorResponse conforms to standardized error format."""
        now = datetime.now(UTC)
        error_body = ErrorBody(
            code="INSUFFICIENT_FUNDS",
            message="Account has insufficient balance.",
            details=[ErrorDetail(field="amount", message="Amount exceeds balance").model_dump()],
            request_id="req_test_123",
            timestamp=now,
        )
        response = ErrorResponse(error=error_body)
        data = response.model_dump(mode="json")

        assert "error" in data
        assert data["error"]["code"] == "INSUFFICIENT_FUNDS"
        assert data["error"]["message"] == "Account has insufficient balance."
        assert len(data["error"]["details"]) == 1
        assert data["error"]["request_id"] == "req_test_123"


class TestNullByteRejection:
    """Regression tests for V-01: Null-byte injection rejection across all string fields."""

    @pytest.mark.parametrize(
        "invalid_name",
        ["\x00John Doe", "John\x00Doe", "John Doe\x00"],
    )
    def test_account_create_rejects_null_bytes(self, invalid_name: str) -> None:
        """Account name rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            AccountCreateRequest.model_validate({"name": invalid_name})
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_desc",
        ["\x00Payment", "Pay\x00ment", "Payment\x00"],
    )
    def test_transaction_create_rejects_null_bytes_in_description(self, invalid_desc: str) -> None:
        """Transaction description rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "1.00",
                    "description": invalid_desc,
                    "reference_id": "REF-VALID-01",
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_ref",
        ["\x00REF-01", "REF\x0001", "REF-01\x00"],
    )
    def test_transaction_create_rejects_null_bytes_in_reference_id(self, invalid_ref: str) -> None:
        """Transaction reference_id rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": "1.00",
                    "description": "Valid description",
                    "reference_id": invalid_ref,
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_amount",
        ["\x001.00", "1.\x0000", "1.00\x00"],
    )
    def test_transaction_create_rejects_null_bytes_in_amount(self, invalid_amount: str) -> None:
        """Transaction amount rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreateRequest.model_validate(
                {
                    "type": "credit",
                    "amount": invalid_amount,
                    "description": "Valid description",
                    "reference_id": "REF-VALID-01",
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_desc",
        ["\x00Transfer", "Trans\x00fer", "Transfer\x00"],
    )
    def test_transfer_create_rejects_null_bytes_in_description(self, invalid_desc: str) -> None:
        """Transfer description rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": "1.00",
                    "description": invalid_desc,
                    "reference_id": "XFER-VALID-01",
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_ref",
        ["\x00XFER-01", "XFER\x0001", "XFER-01\x00"],
    )
    def test_transfer_create_rejects_null_bytes_in_reference_id(self, invalid_ref: str) -> None:
        """Transfer reference_id rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": "1.00",
                    "description": "Valid transfer narrative",
                    "reference_id": invalid_ref,
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_amount",
        ["\x001.00", "1.\x0000", "1.00\x00"],
    )
    def test_transfer_create_rejects_null_bytes_in_amount(self, invalid_amount: str) -> None:
        """Transfer amount rejects null bytes at start, middle, and end."""
        with pytest.raises(ValidationError) as exc_info:
            TransferCreateRequest.model_validate(
                {
                    "source_account_id": uuid.uuid4(),
                    "destination_account_id": uuid.uuid4(),
                    "amount": invalid_amount,
                    "description": "Valid transfer narrative",
                    "reference_id": "XFER-VALID-01",
                }
            )
        assert "cannot contain null bytes" in str(exc_info.value)
