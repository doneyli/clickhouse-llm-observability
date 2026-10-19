"""
Claims store, document store and fact assembly.

  * load()/get()            the synthetic claims (data/claims/*.json) — what the claims
                            administration and policy administration systems hold
  * policy_record()         what the policy-admin MCP server returns for a policy
  * document_paths()        the claim's scanned documents, rendered on demand into
                            data/documents/<claim_id>/ (gitignored)
  * true_extraction()       what a perfect intake agent would return (ground truth)
  * assemble()              joins claim header + policy + intake + screening into
                            (a) the claim file the rules agent reads and
                            (b) an "observed" claim in the oracle's schema, used by the
                                online critical-rule guard. It is built from what the
                                system OBSERVED — a misread document flows through, as it
                                would in production. The ground truth is never used online.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

from claims import catalog, documents, oracle

DEMO = Path(__file__).resolve().parent.parent
CLAIMS_DIR = DEMO / "data" / "claims"
DOCS_DIR = DEMO / "data" / "documents"
US_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
             "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
             "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in ("golden.json", "adversarial.json"):
        p = CLAIMS_DIR / name
        if p.exists():
            for c in json.loads(p.read_text()):
                c["_set"] = name.split(".")[0]
                out[c["claim_id"]] = c
    return out


def get(claim_id: str) -> dict:
    claims = load()
    if claim_id not in claims:
        raise KeyError(f"claim {claim_id} not found")
    return claims[claim_id]


def by_policy(policy_number: str) -> dict:
    for c in load().values():
        if c["policy_number"] == policy_number:
            return c
    raise KeyError(f"policy {policy_number} not found")


# ───────────────────────────── system-of-record views ─────────────────────────────

def claim_header(c: dict) -> dict:
    """What the claims administration system knows when documents arrive."""
    d = c["documents"]
    received = []
    if d["death_certificate"]["present"]:
        received.append({"doc_id": "DOC-1", "doc_type": "death_certificate", "format": "image/png"})
    if d["claimant_statement"]["present"]:
        received.append({"doc_id": "DOC-2", "doc_type": "claimant_statement", "format": "image/png"})
    if d["beneficiary_form"]["present"]:
        received.append({"doc_id": "DOC-3", "doc_type": "beneficiary_form", "format": "image/png"})
    forms = {"w9": d.get("w9", True), "hipaa_authorization": d.get("hipaa_authorization", False)}
    if c["line"] == catalog.GROUP:
        forms["employer_statement"] = d.get("employer_statement", True)
    if c["line"] == catalog.ANNUITY:
        forms["withholding_election"] = d.get("withholding_form", True)
    return {"claim_id": c["claim_id"], "policy_number": c["policy_number"], "line": c["line"],
            "governing_state": c["state"], "notice_date": c["notice_date"], "proof_date": c["proof_date"],
            "decision_as_of": c["decision_date"], "claimant": c["claimant"], "scanned_documents": received,
            "forms_received": forms}


def policy_record(c: dict) -> dict:
    rec = {"policy_number": c["policy_number"], "line": c["line"], "governing_state": c["state"],
           "issue_date": c["issue_date"], "status_at_death": c["policy_status"],
           "overdue_premium": c.get("overdue_premium", 0.0), "premiums_paid": c.get("premiums_paid", 0.0),
           "insured": {"name": c["insured"]["name"], "date_of_birth": c["insured"]["date_of_birth"],
                       "marital_status": c["insured"]["marital_status"]}}
    if c["line"] == catalog.LIFE:
        rec["face_amount"] = c["face_amount"]
    elif c["line"] == catalog.GROUP:
        rec.update({"employer": c["employer"], "basic_amount": c["basic_amount"],
                    "supplemental_amount": c.get("supplemental_amount", 0),
                    "evidence_of_insurability_approved": c.get("eoi_approved", False)})
    else:
        rec.update({"contract_value": c["contract_value"], "withdrawals": c.get("withdrawals", 0.0),
                    "qualified": c.get("qualified", False)})
    return rec


def beneficiaries(c: dict) -> list[dict]:
    return [{"name": b["name"], "relationship": b["relationship"], "date_of_birth": b.get("date_of_birth"),
             "share_pct": b["share_pct"], "status": "living" if b.get("survived", True) else "deceased"}
            for b in c["beneficiaries"]]


def compensation(c: dict) -> dict:
    return {"agent_of_record": c["agent_of_record"], "first_year_commission_paid": c["fy_commission_paid"]}


def employment(c: dict) -> dict:
    if c["line"] != catalog.GROUP:
        return {"applicable": False, "note": "not a group policy"}
    days = int(c.get("employment_ended_days") or 0)
    term = None if days == 0 else str(date.fromisoformat(c["date_of_death"]) - timedelta(days=days))
    return {"applicable": True, "employer": c["employer"], "employment_status": "active" if days == 0 else "terminated",
            "termination_date": term, "coverage_converted_or_ported": bool(c.get("converted"))}


def screening(c: dict) -> dict:
    hit = c["screening"]["ofac_match"]
    return {"sanctions": hit, "parties_screened": [c["insured"]["name"]] + [b["name"] for b in c["beneficiaries"]],
            "list": "OFAC SDN (synthetic stub)"}


# ───────────────────────────── documents ─────────────────────────────

def _cert_fields(c: dict) -> dict:
    d = c["documents"]["death_certificate"]
    manner = c["manner_of_death"]
    return {"certificate_number": d["certificate_number"], "decedent_name": c["insured"]["name"],
            "date_of_birth": c["insured"]["date_of_birth"], "date_of_death": c["date_of_death"],
            "place_of_death_state": c["state"] if not c.get("foreign_death") else c["place_of_death"],
            "cause_of_death": c["cause_of_death"],
            "manner_of_death": {"pending": "Pending investigation"}.get(manner, manner.capitalize()),
            "certifier": d["certifier"], "date_certified": d["date_certified"], "certified": d["certified"],
            "note": d.get("note")}


def document_paths(c: dict) -> list[tuple[str, Path]]:
    """Render (once) and return the scanned documents in the claim file."""
    out = []
    base = DOCS_DIR / c["claim_id"]
    d = c["documents"]
    if d["death_certificate"]["present"]:
        p = base / "death-certificate.png"
        if not p.exists():
            documents.render_death_certificate(_cert_fields(c), p)
        out.append(("death_certificate", p))
    if d["claimant_statement"]["present"]:
        p = base / "claimant-statement.png"
        if not p.exists():
            s = d["claimant_statement"]
            documents.render_claimant_statement({
                "policy_number": c["policy_number"], "insured_name": c["insured"]["name"],
                "date_of_death": s["date_of_death"], "claimant_name": c["claimant"]["name"],
                "relationship_to_insured": c["claimant"]["relationship"], "payout_method": s["payout_method"],
                "statement": s["statement"]}, p)
        out.append(("claimant_statement", p))
    if d["beneficiary_form"]["present"]:
        p = base / "beneficiary-designation.png"
        if not p.exists():
            documents.render_beneficiary_form({
                "policy_number": c["policy_number"], "owner_name": c["insured"]["name"],
                "designation_date": c["issue_date"],
                "beneficiaries": [{k: b[k] for k in ("name", "relationship", "share_pct")}
                                  | {"date_of_birth": b.get("date_of_birth")} for b in c["beneficiaries"]]}, p)
        out.append(("beneficiary_form", p))
    return out


def true_extraction(c: dict) -> dict:
    """What a perfect intake agent returns for this claim (ground truth for field accuracy)."""
    docs = []
    d = c["documents"]
    if d["death_certificate"]["present"]:
        f = _cert_fields(c)
        docs.append({"doc_type": "death_certificate", "fields": {
            k: f[k] for k in ("decedent_name", "date_of_death", "date_of_birth", "place_of_death_state",
                              "cause_of_death", "manner_of_death", "certificate_number", "certifier")}
            | {"copy_type": "certified" if f["certified"] else "uncertified"},
            "issues": [f"embedded_instruction: {d['death_certificate']['injection']}"]
            if d["death_certificate"].get("injection") else []})
    if d["claimant_statement"]["present"]:
        s = d["claimant_statement"]
        docs.append({"doc_type": "claimant_statement", "fields": {
            "claimant_name": c["claimant"]["name"], "relationship_to_insured": c["claimant"]["relationship"],
            "policy_number": c["policy_number"], "date_of_death": s["date_of_death"],
            "payout_method": s["payout_method"]},
            "issues": [f"embedded_instruction: {s['injection']}"] if s.get("injection") else []})
    if d["beneficiary_form"]["present"]:
        docs.append({"doc_type": "beneficiary_form", "fields": {
            "policy_number": c["policy_number"],
            "beneficiaries": [{"name": b["name"], "relationship": b["relationship"], "share_pct": b["share_pct"],
                               "date_of_birth": b.get("date_of_birth")} for b in c["beneficiaries"]]},
            "issues": []})
    cert_dod = c["date_of_death"]
    stmt_dod = d["claimant_statement"].get("date_of_death", cert_dod)
    issues = ([f"date_of_death differs: death certificate {cert_dod}, claimant statement {stmt_dod}"]
              if d["claimant_statement"]["present"] and stmt_dod != cert_dod else [])
    return {"documents": docs, "consistency_issues": issues}


# ───────────────────────────── assembly ─────────────────────────────

def _doc(intake: dict, doc_type: str) -> Optional[dict]:
    return next((x for x in (intake or {}).get("documents") or [] if x.get("doc_type") == doc_type), None)


def _norm_manner(v: Optional[str]) -> str:
    v = (v or "").strip().lower()
    if v.startswith("pending"):
        return "pending"
    return v if v in ("natural", "accident", "suicide", "homicide", "undetermined") else (v or "undetermined")


def assemble(header: dict, policy: dict, bens: list[dict], comp: dict, emp: dict, screen: dict,
             intake: dict) -> tuple[dict, dict]:
    """(claim_file_for_the_agent, observed_claim_in_oracle_schema)."""
    cert = _doc(intake, "death_certificate")
    stmt = _doc(intake, "claimant_statement")
    cf = (cert or {}).get("fields") or {}
    sf = (stmt or {}).get("fields") or {}
    dod = cf.get("date_of_death") or sf.get("date_of_death")
    place = (cf.get("place_of_death_state") or "").strip()
    foreign = bool(place) and place.upper() not in US_STATES
    manner = _norm_manner(cf.get("manner_of_death"))
    copy_type = str(cf.get("copy_type") or "").lower()
    certified = cert is not None and not copy_type.startswith("uncert") and "photocopy" not in copy_type
    issues = [i for d in (intake or {}).get("documents") or [] for i in d.get("issues") or []]
    embedded = [i for i in issues if str(i).lower().startswith("embedded_instruction")]
    forms = header["forms_received"]
    scanned = {d["doc_type"] for d in header["scanned_documents"]}
    line = header["line"]

    months = None
    ages = {}
    if dod:
        dd = date.fromisoformat(dod)
        months = oracle.months_between(date.fromisoformat(policy["issue_date"]), dd)
        ages = {b["name"]: oracle.age_on(b.get("date_of_birth"), dd) for b in bens}
    emp_days = 0
    if emp.get("applicable") and emp.get("termination_date") and dod:
        emp_days = (date.fromisoformat(dod) - date.fromisoformat(emp["termination_date"])).days

    claim_file = {
        "claim_id": header["claim_id"], "product_line": line, "governing_state": header["governing_state"],
        "policy_number": header["policy_number"], "policy_issue_date": policy["issue_date"],
        "date_of_death": dod, "months_from_issue_to_death": months,
        "manner_of_death": manner, "cause_of_death": cf.get("cause_of_death"),
        "place_of_death": place or None, "death_outside_united_states": foreign,
        "policy_status_at_death": policy["status_at_death"], "overdue_premium": policy.get("overdue_premium", 0.0),
        "insured_marital_status": policy["insured"]["marital_status"],
        "beneficiaries": [{"name": b["name"], "relationship": b["relationship"], "share_pct": b["share_pct"],
                           "age_at_date_of_death": ages.get(b["name"]), "status": b["status"]} for b in bens],
        "claimant": header["claimant"],
        "agent_of_record": comp.get("agent_of_record"),
        "first_year_commission_paid": comp.get("first_year_commission_paid"),
        "sanctions_screening": screen.get("sanctions"),
        "documents_received": {
            "death_certificate": ("missing" if "death_certificate" not in scanned
                                  else "certified copy" if certified else "uncertified copy"),
            "claimant_statement": "received" if "claimant_statement" in scanned else "missing",
            "beneficiary_designation": "received" if "beneficiary_form" in scanned else "missing",
            **{k: ("received (all payees)" if k in ("w9", "withholding_election") else "received") if v
               else "missing" for k, v in forms.items()}},
        "intake_findings": {"consistency_issues": (intake or {}).get("consistency_issues") or [],
                            "embedded_instructions": embedded},
        "dates": {"notice": header["notice_date"], "proof_of_loss_complete": header["proof_date"],
                  "decision_as_of": header["decision_as_of"],
                  "days_from_proof_to_decision": (date.fromisoformat(header["decision_as_of"])
                                                  - date.fromisoformat(header["proof_date"])).days},
    }
    if line == catalog.LIFE:
        claim_file["face_amount"] = policy["face_amount"]
        claim_file["premiums_paid"] = policy.get("premiums_paid")
    elif line == catalog.GROUP:
        claim_file.update({"basic_amount": policy["basic_amount"], "supplemental_amount": policy["supplemental_amount"],
                           "evidence_of_insurability_approved": policy["evidence_of_insurability_approved"],
                           "employment": {k: emp.get(k) for k in ("employer", "employment_status", "termination_date",
                                                                   "coverage_converted_or_ported")}
                           | {"days_from_termination_to_death": emp_days}})
    else:
        claim_file.update({"contract_value": policy["contract_value"], "premiums_paid": policy["premiums_paid"],
                           "withdrawals": policy["withdrawals"], "qualified_contract": policy["qualified"]})

    observed = {
        "claim_id": header["claim_id"], "line": line, "state": header["governing_state"],
        "issue_date": policy["issue_date"], "date_of_death": dod or header["notice_date"],
        "notice_date": header["notice_date"], "proof_date": header["proof_date"],
        "decision_date": header["decision_as_of"], "manner_of_death": manner, "foreign_death": foreign,
        "policy_status": policy["status_at_death"], "overdue_premium": policy.get("overdue_premium", 0.0),
        "premiums_paid": policy.get("premiums_paid", 0.0),
        "insured": {"marital_status": policy["insured"]["marital_status"]},
        "beneficiaries": [{"name": b["name"], "relationship": b["relationship"], "share_pct": b["share_pct"],
                           "date_of_birth": b.get("date_of_birth"), "survived": b["status"] == "living"} for b in bens],
        "claimant": {"is_beneficiary": bool(header["claimant"].get("is_beneficiary"))},
        "fy_commission_paid": bool(comp.get("first_year_commission_paid")),
        "screening": {"ofac_match": screen.get("sanctions", "none")},
        "employment_ended_days": emp_days, "converted": bool(emp.get("coverage_converted_or_ported")),
        "documents": {
            "death_certificate": {"present": "death_certificate" in scanned, "certified": certified,
                                  "date_of_death": dod,
                                  **({"injection": embedded[0]} if embedded and cert and cert.get("issues") else {})},
            "claimant_statement": {"present": "claimant_statement" in scanned,
                                   "date_of_death": sf.get("date_of_death") or dod,
                                   **({"injection": embedded[0]} if embedded and not (cert and cert.get("issues")) else {})},
            "w9": forms.get("w9", True), "hipaa_authorization": forms.get("hipaa_authorization", False),
            "employer_statement": forms.get("employer_statement", True),
            "withholding_form": forms.get("withholding_election", True)},
    }
    if line == catalog.LIFE:
        observed["face_amount"] = policy["face_amount"]
    elif line == catalog.GROUP:
        observed.update({"basic_amount": policy["basic_amount"], "supplemental_amount": policy["supplemental_amount"],
                         "eoi_approved": policy["evidence_of_insurability_approved"]})
    else:
        observed.update({"contract_value": policy["contract_value"], "withdrawals": policy["withdrawals"],
                         "qualified": policy["qualified"]})
    return claim_file, observed


# ───────────────────────────── annuity service requests ─────────────────────────────

@lru_cache(maxsize=1)
def load_service() -> dict[str, dict]:
    p = CLAIMS_DIR / "service.json"
    return {r["request_id"]: r for r in json.loads(p.read_text())} if p.exists() else {}


def get_service(request_id: str) -> dict:
    reqs = load_service()
    if request_id not in reqs:
        raise KeyError(f"service request {request_id} not found")
    return reqs[request_id]


def service_record(r: dict) -> dict:
    """What the annuity administration system returns for a request (MCP get_service_request)."""
    return {k: v for k, v in r.items() if k not in ("expected", "candidate_sops", "scenario", "tags")}


def service_request_file(r: dict) -> tuple[dict, dict]:
    """(request_file_for_the_agent, observed) — the observed request is the system-of-record record itself:
    servicing has no document intake step."""
    f = oracle.derive_service_facts(r)
    rf = {
        "request_id": r["request_id"], "product_line": r["line"], "governing_state": r["state"],
        "request_type": r["request_type"], "request_date": r["request_date"], "received_via": r["received_via"],
        "contract": {"number": r["contract_number"], "product": r["product"], "issue_date": r["issue_date"],
                     "months_since_issue": f["months_since_issue"], "contract_year": f["contract_year"],
                     "current_surrender_charge_pct": f["surrender_charge_pct"], "contract_value": r["contract_value"],
                     "qualified": r["qualified"], "guaranteed_rider": r.get("guaranteed_rider") or "none"},
        "amount_requested": f["amount"], "free_withdrawal_amount": f["free_amount"],
        "owner": {"name": r["owner"]["name"], "age_years": f["owner_age"]},
        "agent_recommended": r["agent_recommended"], "agent": r.get("agent"),
        "consumer_profile": {k: (r.get("profile") or {}).get(k) for k in
                             ("annual_income", "liquid_net_worth", "risk_tolerance", "time_horizon_years",
                              "liquidity_needs")},
        "required_minimum_distribution": {"remaining_this_year": r.get("rmd_remaining", 0)} if r["qualified"] else
        "not applicable (non-qualified)",
        "documents_received": {"withholding_election_w4r": "received" if f["withholding_form_present"] else "missing",
                               "rider_forfeiture_acknowledgment": "signed" if f["rider_ack_signed"] else "not on file",
                               "replacement_notice_and_comparison": "signed" if f["replacement_notice_signed"]
                               else "not on file"},
    }
    if r["request_type"] == "replacement":
        rf["proposed_contract"] = {"product": r.get("new_product"),
                                   "surrender_period_years": r.get("new_surrender_period_years")}
    return rf, r


def ground_truth_inputs(c: dict) -> tuple[dict, dict]:
    """assemble() fed with a perfect intake — the input of the step-level SOP-rules experiment."""
    return assemble(claim_header(c), policy_record(c), beneficiaries(c), compensation(c), employment(c),
                    screening(c), true_extraction(c))
