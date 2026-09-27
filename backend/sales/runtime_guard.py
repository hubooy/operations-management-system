"""Lightweight dynamic safety checks shared by writer readiness and transactions."""

from __future__ import annotations

import hashlib
import re
import uuid

from django.db import connection

from .cutover_attestation import (
    SalesCutoverAttestationError,
    require_valid_cutover_attestation,
)


HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


class WriterRuntimeGuardError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_erp_reference_runtime_state(cursor=None) -> None:
    owns_cursor = cursor is None
    if owns_cursor:
        cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT revision, source_digest FROM sales_data_revisions WHERE domain='erp'"
        )
        revision_row = cursor.fetchone()
        if revision_row is None:
            raise WriterRuntimeGuardError("erp_reference_revision_missing")
        revision = int(revision_row[0])
        revision_digest = str(revision_row[1] or "")
        if revision < 1 or not HEX_64_RE.fullmatch(revision_digest):
            raise WriterRuntimeGuardError("erp_reference_revision_invalid")
        cursor.execute(
            "SELECT status, authority_epoch, cutover_id, migration_verify_run_id "
            "FROM erp_reference_write_authority WHERE id = 1"
        )
        authority = cursor.fetchone()
        try:
            authority_epoch = uuid.UUID(str(authority[1])) if authority is not None else None
        except (ValueError, TypeError, AttributeError):
            authority_epoch = None
        if (
            authority is None
            or str(authority[0] or "") != "postgres"
            or authority_epoch is None
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", str(authority[2] or ""))
            or not re.fullmatch(r"erp-reference-[0-9a-f]{32}", str(authority[3] or ""))
        ):
            raise WriterRuntimeGuardError("erp_reference_authority_invalid")
        cursor.execute(
            "SELECT h.source_key,h.scope_key,h.status,h.owner_token,h.current_batch_id,"
            "b.status,b.content_hash,b.row_count "
            "FROM erp_reference_import_scope_heads h "
            "LEFT JOIN erp_reference_import_batches_pg b "
            "ON b.id=h.current_batch_id AND b.source_key=h.source_key "
            "ORDER BY h.source_key"
        )
        heads = cursor.fetchall()
        if len(heads) != 2 or [str(row[0]) for row in heads] != ["combos", "products"]:
            raise WriterRuntimeGuardError("erp_reference_scope_heads_invalid")
        expected_counts: dict[str, int] = {}
        expected_hashes: dict[str, str] = {}
        for row in heads:
            source = str(row[0])
            expected_scope = hashlib.sha256(
                (f'{{"source":"{source}"}}').encode("utf-8")
            ).hexdigest()
            if (
                str(row[1]) != expected_scope
                or str(row[2]) != "ready"
                or str(row[3] or "")
                or not str(row[4] or "")
                or str(row[5] or "") != "completed"
                or not HEX_64_RE.fullmatch(str(row[6] or ""))
                or int(row[7]) < 0
            ):
                raise WriterRuntimeGuardError("erp_reference_scope_heads_invalid")
            expected_counts[source] = int(row[7])
            expected_hashes[source] = str(row[6])
        # Import locally so sales model initialization cannot form an import
        # cycle. Readiness independently re-hashes both complete ERP scopes;
        # the revision row is a checkpoint, not proof by itself.
        from erp_reference.import_service import (
            combined_database_digest,
            combo_rows_from_database,
            content_hash,
            product_rows_from_database,
        )

        products = product_rows_from_database()
        combos = combo_rows_from_database()
        product_count = len(products)
        combo_count = len(combos)
        if (
            product_count <= 0
            or product_count != expected_counts["products"]
            or combo_count != expected_counts["combos"]
            or content_hash("products", products) != expected_hashes["products"]
            or content_hash("combos", combos) != expected_hashes["combos"]
            or combined_database_digest() != revision_digest
        ):
            raise WriterRuntimeGuardError("erp_reference_facts_invalid")
    finally:
        if owns_cursor:
            cursor.close()


def validate_writer_runtime_state(*, cutover_id: str, cursor=None) -> None:
    try:
        # Runtime verification is intentionally lightweight: exact immutable
        # payload/hash/column binding only. Activation performs the one-time
        # full baseline scan; legal later sales writes must not invalidate it.
        require_valid_cutover_attestation(cutover_id=cutover_id)
    except SalesCutoverAttestationError as error:
        raise WriterRuntimeGuardError("sales_cutover_attestation_invalid") from error
    validate_erp_reference_runtime_state(cursor)
