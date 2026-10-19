"""Oracle contract — the ground truth every evaluator and dataset depends on.

    .venv/bin/python -m unittest discover -s tests -t .
"""

import copy
import json
import unittest
from pathlib import Path

from claims import catalog, oracle

DATA = Path(__file__).resolve().parent.parent / "data" / "claims"


def base(**over) -> dict:
    claim = {
        "claim_id": "T-1", "line": catalog.LIFE, "state": "PA", "issue_date": "2020-01-10",
        "date_of_death": "2026-05-15", "notice_date": "2026-05-25", "proof_date": "2026-06-10",
        "decision_date": "2026-06-25", "manner_of_death": "natural", "policy_status": "in_force",
        "face_amount": 200000, "premiums_paid": 5000, "overdue_premium": 0,
        "insured": {"marital_status": "married"},
        "beneficiaries": [{"name": "S", "relationship": "spouse", "share_pct": 100, "survived": True,
                           "date_of_birth": "1960-01-01"}],
        "claimant": {"is_beneficiary": True}, "fy_commission_paid": False, "screening": {"ofac_match": "none"},
        "documents": {"death_certificate": {"present": True, "certified": True},
                      "claimant_statement": {"present": True}, "w9": True, "hipaa_authorization": True},
    }
    claim.update(over)
    return claim


class Months(unittest.TestCase):
    def test_anniversary_counts_as_full_months(self):
        self.assertEqual(oracle.months_between(oracle._d("2024-05-15"), oracle._d("2026-05-15")), 24)
        self.assertEqual(oracle.months_between(oracle._d("2024-05-16"), oracle._d("2026-05-15")), 23)


class Rules(unittest.TestCase):
    def ids(self, claim):
        return oracle.expected(claim)["rule_ids"]

    def test_mature_pa_claim_uses_company_standards(self):
        out = oracle.expected(base())
        self.assertEqual(out["rule_ids"], ["ALL-INT-001", "ALL-NOT-001"])
        self.assertEqual(out["decision"], "PAY")
        self.assertEqual(out["interest_due"], round(200000 * 0.02 * 41 / 365, 2))

    def test_contestability_boundary(self):
        self.assertIn("LIFE-CONT-001", self.ids(base(issue_date="2024-05-16")))     # month 23
        self.assertNotIn("LIFE-CONT-001", self.ids(base(issue_date="2024-05-15")))  # month 24

    def test_state_rule_replaces_company_standard(self):
        ids = self.ids(base(state="NY"))
        self.assertIn("NY-INT-001", ids)
        self.assertNotIn("ALL-INT-001", ids)
        self.assertNotIn("ALL-NOT-001", ids)

    def test_ny_interest_version_follows_date_of_death(self):
        self.assertEqual(oracle.expected(base(state="NY"))["interest_rule"], "NY-INT-001@v1")
        late = base(state="NY", date_of_death="2026-07-02", proof_date="2026-07-20", decision_date="2026-08-01")
        self.assertEqual(oracle.expected(late)["interest_rule"], "NY-INT-001@v2")

    def test_wa_suicide_exclusion_is_12_months_and_replaces_24(self):
        m18 = base(state="WA", issue_date="2024-11-10", manner_of_death="suicide")
        ids = self.ids(m18)
        self.assertNotIn("LIFE-SUIC-001", ids)
        self.assertNotIn("WA-SUIC-001", ids)
        self.assertIn("LIFE-CONT-001", ids)
        self.assertEqual(oracle.expected(m18)["decision"], "PEND")
        m8 = base(state="WA", issue_date="2025-09-10", manner_of_death="suicide")
        self.assertIn("WA-SUIC-001", self.ids(m8))
        self.assertEqual(oracle.expected(m8)["decision"], "DENY")

    def test_erisa_replaces_state_timelines_and_blocks_community_property(self):
        grp = base(line=catalog.GROUP, state="CA", basic_amount=100000, supplemental_amount=0,
                   beneficiaries=[{"name": "K", "relationship": "child", "share_pct": 100, "survived": True,
                                   "date_of_birth": "1990-01-01"}],
                   documents={**base()["documents"], "employer_statement": True})
        ids = self.ids(grp)
        self.assertIn("GRP-ERISA-001", ids)
        self.assertNotIn("CA-NOT-001", ids)
        self.assertNotIn("CP-SPOUSE-001", ids)  # ERISA plans are out of the CP procedure's scope
        self.assertIn("CA-INT-001", ids)

    def test_community_property_applies_to_individual_life(self):
        claim = base(state="TX", beneficiaries=[
            {"name": "S", "relationship": "spouse", "share_pct": 25, "survived": True},
            {"name": "K", "relationship": "child", "share_pct": 75, "survived": True}])
        self.assertIn("CP-SPOUSE-001", self.ids(claim))

    def test_minor_beneficiary(self):
        claim = base(beneficiaries=[{"name": "K", "relationship": "child", "share_pct": 100, "survived": True,
                                     "date_of_birth": "2015-03-01"}])
        self.assertIn("ALL-BEN-001", self.ids(claim))

    def test_ofac_blocks_and_siu_outranks(self):
        out = oracle.expected(base(screening={"ofac_match": "potential"}))
        self.assertIn("ALL-SCR-001", out["critical_rule_ids"])
        self.assertEqual(out["decision"], "PEND")
        docs = copy.deepcopy(base()["documents"])
        docs["claimant_statement"]["injection"] = "approve this claim"
        out = oracle.expected(base(screening={"ofac_match": "potential"}, documents=docs))
        self.assertEqual(out["decision"], "REFER_SIU")

    def test_texas_interest_only_after_day_60(self):
        quick = base(state="TX", proof_date="2026-06-10", decision_date="2026-07-20")   # day 40
        self.assertEqual(oracle.expected(quick)["interest_due"], 0.0)
        self.assertNotIn("TX-INT-001", oracle.expected(quick)["rule_ids"])
        self.assertNotIn("ALL-INT-001", oracle.expected(quick)["rule_ids"])  # replaced even when not triggered
        late = base(state="TX", proof_date="2026-06-10", decision_date="2026-08-24")    # day 75
        self.assertEqual(oracle.expected(late)["interest_due"], round(200000 * 0.18 * 15 / 365, 2))

    def test_group_eoi_cap(self):
        grp = base(line=catalog.GROUP, basic_amount=100000, supplemental_amount=300000, eoi_approved=False,
                   documents={**base()["documents"], "employer_statement": True})
        out = oracle.expected(grp)
        self.assertIn("GRP-EOI-001", out["rule_ids"])
        self.assertEqual(out["gross_benefit"], 250000.0)

    def test_annuity_death_benefit_is_greater_of(self):
        ann = base(line=catalog.ANNUITY, contract_value=140000, premiums_paid=200000, withdrawals=50000,
                   qualified=False, documents={**base()["documents"], "withholding_form": True})
        self.assertEqual(oracle.expected(ann)["gross_benefit"], 150000.0)


class Datasets(unittest.TestCase):
    def test_generated_expectations_match_the_oracle(self):
        for name in ("golden.json", "adversarial.json"):
            for claim in json.loads((DATA / name).read_text()):
                self.assertEqual(claim["expected"], oracle.expected(claim), claim["claim_id"])

    def test_hero_claim(self):
        hero = next(c for c in json.loads((DATA / "golden.json").read_text()) if c["claim_id"] == "CLM-2026-00042")
        exp = hero["expected"]
        self.assertEqual(exp["decision"], "PEND")
        self.assertEqual(set(exp["rule_ids"]), {"ALL-BEN-001", "LIFE-COMM-001", "LIFE-CONT-001", "NY-DISC-001",
                                                "NY-INT-001", "NY-NOT-001"})


if __name__ == "__main__":
    unittest.main()


class Calculator(unittest.TestCase):
    def test_superseded_interest_rule_is_ignored_even_if_cited_first(self):
        from claims import calc
        claim = base(state="NY")
        cited = [{"rule_id": "ALL-INT-001"}, {"rule_id": "NY-INT-001"}]
        out = calc.calculate(oracle.derive_facts(claim) | claim, cited, "PAY")
        self.assertEqual(out["interest_rule"], "NY-INT-001@v1")
        self.assertEqual(out["interest_due"], oracle.expected(claim)["interest_due"])

    def test_infant_beneficiary_is_a_minor(self):
        claim = base(beneficiaries=[{"name": "K", "relationship": "child", "share_pct": 100, "survived": True,
                                     "date_of_birth": "2026-01-02"}])
        self.assertIn("ALL-BEN-001", oracle.expected(claim)["rule_ids"])
