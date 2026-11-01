"""Shared-identity ring detector (spec 9.3, FR-16).

Constructs a bipartite graph of student records and contact tokens (phone, email, address).
Finds connected components and applies Louvain community detection on multi-record
components. Flags clusters where a contact identifier is shared by > k distinct
APAAR IDs (default k=3) across institutions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import networkx as nx

logger = logging.getLogger("apaar.ml.rings")


@dataclass
class ContactRecord:
    record_id: int
    student_id: int
    apaar_id: str | None
    institution_id: int
    institution_code: str
    contact_token: str | None  # phone token
    email_token: str | None
    address_token: str | None


@dataclass
class RingDetectionResult:
    ring_id: str
    token_type: str  # "phone" | "email" | "address"
    masked_token: str
    record_ids: list[int]
    student_ids: list[int]
    institution_ids: list[int]
    institution_codes: list[str]
    size: int
    institutions_involved_count: int
    evidence: dict[str, Any]


def mask_token(token: str | None, token_type: str) -> str:
    if not token:
        return "UNKNOWN"
    prefix = token[:6]
    suffix = token[-4:] if len(token) >= 10 else ""
    return f"{token_type}:{prefix}...{suffix}"


class RingDetector:
    """Graph-based shared-identity ring detection using Louvain community clustering."""

    def __init__(self, min_shared_students: int = 3) -> None:
        self.min_shared_students = min_shared_students

    def detect_rings(self, records: list[ContactRecord]) -> list[RingDetectionResult]:
        """Detect shared-identity rings across records."""
        if not records:
            return []

        # 1. Map tokens to records
        token_to_records: dict[str, list[ContactRecord]] = {}
        for r in records:
            if r.contact_token:
                key = f"phone:{r.contact_token}"
                token_to_records.setdefault(key, []).append(r)
            if r.email_token:
                key = f"email:{r.email_token}"
                token_to_records.setdefault(key, []).append(r)
            if r.address_token:
                key = f"addr:{r.address_token}"
                token_to_records.setdefault(key, []).append(r)

        # 2. Filter candidate tokens shared by > k distinct students across multiple institutions
        suspicious_tokens = {}
        for token_key, recs in token_to_records.items():
            distinct_students = {rec.student_id for rec in recs}
            distinct_insts = {rec.institution_id for rec in recs}
            # Spec 9.3: Rings must span multiple institutions
            if len(distinct_students) >= self.min_shared_students and len(distinct_insts) > 1:
                suspicious_tokens[token_key] = recs

        if not suspicious_tokens:
            return []

        # 3. Build graph between records connected by suspicious tokens
        G = nx.Graph()
        for token_key, recs in suspicious_tokens.items():
            token_node = f"TOKEN:{token_key}"
            G.add_node(token_node, type="token", key=token_key)
            for rec in recs:
                rec_node = f"REC:{rec.record_id}"
                G.add_node(
                    rec_node,
                    type="record",
                    record=rec,
                )
                G.add_edge(rec_node, token_node)

        # 4. Find connected components or Louvain communities
        components = list(nx.connected_components(G))
        results: list[RingDetectionResult] = []

        for c_idx, comp in enumerate(components):
            subg = G.subgraph(comp)
            rec_nodes = [n for n in comp if n.startswith("REC:")]
            token_nodes = [n for n in comp if n.startswith("TOKEN:")]

            if len(rec_nodes) < self.min_shared_students:
                continue

            # Sub-cluster with Louvain if component is large
            if len(rec_nodes) >= 15:
                # Project bipartite graph to record nodes for Louvain
                record_subgraph = nx.bipartite.projected_graph(subg, rec_nodes)
                try:
                    communities = list(nx.community.louvain_communities(record_subgraph, seed=42))
                except Exception:
                    communities = [set(rec_nodes)]
            else:
                communities = [set(rec_nodes)]

            for comm_idx, comm in enumerate(communities):
                comm_recs = [subg.nodes[n]["record"] for n in comm]
                student_ids = sorted({r.student_id for r in comm_recs})
                if len(student_ids) < self.min_shared_students:
                    continue

                rec_ids = sorted([r.record_id for r in comm_recs])
                inst_ids = sorted({r.institution_id for r in comm_recs})
                inst_codes = sorted({r.institution_code for r in comm_recs})

                # Determine primary shared token for this community
                comm_tokens = []
                for tn in token_nodes:
                    tok_recs = set(subg.neighbors(tn))
                    intersect = tok_recs.intersection(comm)
                    if len(intersect) >= 2:
                        comm_tokens.append((subg.nodes[tn]["key"], len(intersect)))
                comm_tokens.sort(key=lambda t: t[1], reverse=True)

                primary_key = comm_tokens[0][0] if comm_tokens else "unknown"
                tok_type = primary_key.split(":", 1)[0] if ":" in primary_key else "phone"
                masked = mask_token(primary_key, tok_type)

                ring_id = f"RING_{c_idx + 1}_{comm_idx + 1}"
                evidence = {
                    "ring_id": ring_id,
                    "primary_token_type": tok_type,
                    "masked_token": masked,
                    "distinct_students_count": len(student_ids),
                    "total_records_count": len(rec_ids),
                    "institutions_involved": inst_codes,
                    "institutions_count": len(inst_ids),
                }

                results.append(
                    RingDetectionResult(
                        ring_id=ring_id,
                        token_type=tok_type,
                        masked_token=masked,
                        record_ids=rec_ids,
                        student_ids=student_ids,
                        institution_ids=inst_ids,
                        institution_codes=inst_codes,
                        size=len(rec_ids),
                        institutions_involved_count=len(inst_ids),
                        evidence=evidence,
                    )
                )

        results.sort(key=lambda r: r.size, reverse=True)
        return results


default_ring_detector = RingDetector()
