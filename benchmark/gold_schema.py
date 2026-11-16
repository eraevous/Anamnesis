"""Validate a fixture's ``gold.json`` (fixture plan, "Shared format"; design note sections 3-5).

Reviewer guide
--------------
- The vocabularies below are copied from the design note's evidence table (section 4) and the
  fixture plan's placements. If the design note changes one, change it here in the same PR.
- What this checks is structure and internal consistency: every unit a gold field names exists,
  islands are contiguous ranges on the active path that don't overlap, alternates are off the
  active path, evidence quotes occur verbatim in their unit (the quote guard's first half, applied
  to the gold itself), and speakers match the unit's role.
- What it does not check: whether the gold labels are *right*. That is a review question (the
  fixture plan asks for at least one regression fixture written blind by Kate or a fresh-context
  agent, so the gold is not only the design author's reading).
"""

from __future__ import annotations

SCHEMA = "anamnesis.fixture-gold.v1"

OBJECTIVE_KINDS = {"find_concept", "find_unit", "find_alternate", "resolve_term", "recover_rejection"}
PLACEMENTS = {"core", "ancestor", "application", "conflict", "rejection", "adjacency"}
SPEAKERS = {"user", "assistant", "tool", "system"}
ARTIFACT_CLASSES = {"user_statement", "assistant_reply", "imported_draft", "instruction",
                    "generated_summary", "code_or_log", "unclassified"}
STANCES = {"asserts", "proposes", "questions", "rejects", "quotes", "instructs"}
ACCEPTANCE = {"accepted", "rejected", "unreviewed", "superseded"}
CLAIM_STATUSES = {"EXPLICIT", "INFERRED", "UNRESOLVED", "CONFLICTING"}
CHECK_KINDS = {"hit_resolves_to_unit", "alternate_findable", "snippet_single_unit"}
MUST_NOT_KINDS = {"claim", "merge", "placement", "label", "quote_span", "chronology"}
FAILURE_METHODS = {"lexical_only", "naive_baseline"}
# Design note section 4: never eligible, whatever their acceptance (rules 3, 4 and 6).
NEVER_ELIGIBLE = {"instruction", "generated_summary", "unclassified"}


class GoldError(ValueError):
    pass


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise GoldError(msg)


def validate(gold: dict, texts: dict[str, str] | None = None, roles: dict[str, str] | None = None) -> None:
    """Raise GoldError on the first problem. ``texts``/``roles`` map unit id -> text/role when known."""
    fid = gold.get("fixture", "?")
    _need(gold.get("schema") == SCHEMA, f"{fid}: schema must be {SCHEMA}")
    units = set(gold["labels"].values())
    active = gold["active_path"]
    pos = {u: i for i, u in enumerate(active)}
    _need(set(active) <= units, f"{fid}: active_path names unknown units")

    def unit(u: str, where: str) -> None:
        _need(u in units, f"{fid}: {where} names unknown unit {u}")

    objectives = gold["objectives"]
    ids = [o["id"] for o in objectives]
    _need(len(ids) == len(set(ids)), f"{fid}: duplicate objective ids")
    for o in objectives:
        _need(o["kind"] in OBJECTIVE_KINDS, f"{fid}: objective {o['id']} has unknown kind {o['kind']}")
        _need(bool(o.get("query", "").strip()), f"{fid}: objective {o['id']} has no query")

    for oid, anchors in gold["anchors"].items():
        _need(oid in ids, f"{fid}: anchors for unknown objective {oid}")
        _need(len(anchors) > 0, f"{fid}: objective {oid} has no gold anchors")
        for u in anchors:
            unit(u, f"anchors[{oid}]")

    island_ids = set()
    spans = []
    for isl in gold["islands"]:
        iid = isl["id"]
        _need(iid not in island_ids, f"{fid}: duplicate island {iid}")
        island_ids.add(iid)
        _need(isl["branch"] == "active", f"{fid}: island {iid}: only active-branch islands are supported")
        _need(isl["placement"] in PLACEMENTS, f"{fid}: island {iid} has unknown placement {isl['placement']}")
        for end in ("first", "last"):
            _need(isl[end] in pos, f"{fid}: island {iid} {end} is not on the active path")
        a, b = pos[isl["first"]], pos[isl["last"]]
        _need(a <= b, f"{fid}: island {iid} ends before it starts")
        spans.append((a, b, iid))
    spans.sort()
    for (a1, b1, i1), (a2, b2, i2) in zip(spans, spans[1:]):
        _need(b1 < a2, f"{fid}: islands {i1} and {i2} overlap; gold islands must be distinct")

    for u, status in gold.get("alternate_units", {}).items():
        unit(u, "alternate_units")
        _need(u not in pos, f"{fid}: alternate unit {u} is on the active path")
        _need(status == "alternate", f"{fid}: alternate unit {u} must be labelled alternate")

    ev_ids = [e["id"] for e in gold.get("evidence", [])]
    _need(len(ev_ids) == len(set(ev_ids)), f"{fid}: duplicate evidence ids")
    for e in gold.get("evidence", []):
        u = e["unit"]
        unit(u, "evidence")
        _need(e["speaker"] in SPEAKERS, f"{fid}: evidence on {u} has unknown speaker")
        if roles is not None:
            _need(roles[u] == e["speaker"], f"{fid}: evidence on {u} says {e['speaker']}, unit role is {roles[u]}")
        _need(e["artifact_class"] in ARTIFACT_CLASSES, f"{fid}: evidence on {u} has unknown artifact_class")
        _need(e["local_stance"] in STANCES, f"{fid}: evidence on {u} has unknown local_stance")
        elig = e["canonical_eligibility"]
        _need(elig == "eligible" or elig.startswith("ineligible:"), f"{fid}: evidence on {u}: bad eligibility {elig}")
        acc = e.get("acceptance_status")
        if e["artifact_class"] != "user_statement":
            _need(acc in ACCEPTANCE, f"{fid}: non-user evidence on {u} needs an acceptance_status")
        if acc in ("accepted", "rejected"):
            _need("acceptance_unit" in e, f"{fid}: {acc} evidence on {u} must cite its acceptance_unit")
            unit(e["acceptance_unit"], "acceptance_unit")
            # Section 4: the acceptance unit is where the *user* accepts or rejects; an assistant
            # can never accept its own proposal.
            if roles is not None:
                _need(roles[e["acceptance_unit"]] == "user",
                      f"{fid}: evidence {e['id']} cites acceptance_unit {e['acceptance_unit']}, which is not a user unit")
        # Design note section 4, rule 1: core eligibility is user statements or accepted evidence.
        if elig == "eligible":
            _need(e["artifact_class"] == "user_statement" or acc == "accepted",
                  f"{fid}: evidence on {u} is eligible but neither a user statement nor accepted")
            _need(e["artifact_class"] not in NEVER_ELIGIBLE,
                  f"{fid}: evidence {e['id']} is {e['artifact_class']}, which is never eligible")
        _need(bool(e["quote"].strip()), f"{fid}: evidence on {u} has an empty quote")
        if texts is not None:
            _need(e["quote"] in texts[u], f"{fid}: evidence quote is not verbatim in {u}: {e['quote']!r}")

    # Claims cite evidence spans by id, not units: one unit can hold an accepted and a rejected span.
    evidence = {e["id"]: e for e in gold.get("evidence", [])}
    for c in gold.get("claims", []):
        _need(c["status"] in CLAIM_STATUSES, f"{fid}: claim {c['id']} has unknown status")
        _need(c["placement"] in PLACEMENTS, f"{fid}: claim {c['id']} has unknown placement")
        _need(len(c["support"]) > 0, f"{fid}: claim {c['id']} has no support")
        for ref in c["support"]:
            _need(ref in evidence, f"{fid}: claim {c['id']} cites unknown evidence {ref}")
            if c["placement"] == "core":
                _need(evidence[ref]["canonical_eligibility"] == "eligible",
                      f"{fid}: core claim {c['id']} cites ineligible evidence {ref}")

    for chk in gold.get("checks", []):
        _need(chk["kind"] in CHECK_KINDS, f"{fid}: unknown check kind {chk['kind']}")
        _need(chk["objective"] in ids, f"{fid}: check names unknown objective {chk['objective']}")

    for m in gold.get("must_not", []):
        _need(m["kind"] in MUST_NOT_KINDS, f"{fid}: unknown must_not kind {m['kind']}")
        for u in m.get("units", []) + ([m["unit"]] if "unit" in m else []):
            unit(u, "must_not")
        for i in m.get("islands", []) + ([m["island"]] if "island" in m else []):
            _need(i in island_ids, f"{fid}: must_not names unknown island {i}")

    for f in gold.get("expected_failures", []):
        _need(f["method"] in FAILURE_METHODS, f"{fid}: unknown expected-failure method {f['method']}")
        _need(f["objective"] in ids, f"{fid}: expected failure names unknown objective")

    nst = gold.get("no_shared_terms")
    if nst:
        _need(nst["objective"] in ids, f"{fid}: no_shared_terms names unknown objective")
        for i in nst["islands"]:
            _need(i in island_ids, f"{fid}: no_shared_terms names unknown island {i}")

    for oid, terms in gold.get("alternate_only_terms", {}).items():
        _need(oid in ids, f"{fid}: alternate_only_terms names unknown objective {oid}")
        _need(len(terms) > 0, f"{fid}: alternate_only_terms[{oid}] is empty")
