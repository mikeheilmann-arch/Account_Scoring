from __future__ import annotations

from certifid_account_scoring.pipeline.sales_prioritization_v1_1_entity_controls import (
    is_foreign_country_domain,
    normalized_domain,
    resolve_shared_domain_survivor,
)


def test_normalized_domain_removes_scheme_www_and_path() -> None:
    assert normalized_domain("https://www.example.com/path") == "example.com"


def test_country_code_domain_detection_is_generalized() -> None:
    assert is_foreign_country_domain("firm.com.au")
    assert is_foreign_country_domain("firm.co.uk")
    assert is_foreign_country_domain("firm.co")
    assert not is_foreign_country_domain("firm.com")
    assert not is_foreign_country_domain("firm.us")


def test_parent_child_evidence_selects_parent_survivor() -> None:
    ids = ["parent", "child"]
    survivor, reason = resolve_shared_domain_survivor(
        ids,
        accounts={"parent": {"ParentId": ""}, "child": {"ParentId": "parent"}},
        memberships={
            "parent": {"suggested_survivor_id": "parent", "confidence": "Low"},
            "child": {"suggested_survivor_id": "parent", "confidence": "Medium"},
        },
        bindings={"parent": {}, "child": {}},
        audit_by_id={"parent": {"alta_confirmed": "true"}, "child": {"alta_confirmed": "false"}},
    )
    assert survivor == "parent"
    assert reason == "resolved_parent_child_or_suggested_survivor"


def test_tied_unresolved_members_have_no_survivor() -> None:
    ids = ["one", "two"]
    survivor, reason = resolve_shared_domain_survivor(
        ids,
        accounts={"one": {"Name": "Homeland Title"}, "two": {"Name": "Homeland Title Agency"}},
        memberships={
            "one": {"suggested_survivor_id": "one", "confidence": "Low"},
            "two": {"suggested_survivor_id": "two", "confidence": "Low"},
        },
        bindings={
            "one": {"status": "bound", "confidence": "High", "citations": "[]"},
            "two": {"status": "bound", "confidence": "High", "citations": "[]"},
        },
        audit_by_id={"one": {"alta_confirmed": "false"}, "two": {"alta_confirmed": "false"}},
    )
    assert survivor == ""
    assert reason == "unresolved_shared_domain"
