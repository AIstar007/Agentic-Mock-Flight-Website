from __future__ import annotations

from typing import Any
from app.rules import rule_catalog


class RuleGraph:
    """Optional Neo4j index for rule provenance and entity relationships.

    JSON catalog + Python validators remain the deterministic source of truth in the POC.
    Neo4j is an explainer/discovery graph, not an authority for live travel eligibility.
    """
    def __init__(self, uri: str = "", username: str = "neo4j", password: str = ""):
        self.uri, self.username, self.password = uri, username, password
        self.driver = None

    def initialize(self) -> None:
        if not self.uri:
            return
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password), connection_timeout=3)
        self.driver.verify_connectivity()

    def close(self) -> None:
        if self.driver:
            self.driver.close()
            self.driver = None

    def list_rules(self) -> list[dict[str, Any]]:
        if not self.driver:
            return rule_catalog()
        query = "MATCH (r:Rule) RETURN r.rule_id AS rule_id, r.name AS name, r.source_type AS source_type, r.source_file AS source_file, r.source_url AS source_url, r.severity AS severity, r.emirates_status AS emirates_status ORDER BY r.rule_id"
        with self.driver.session() as session:
            rows = [dict(row) for row in session.run(query)]
        return rows or rule_catalog()

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        if not self.driver:
            return next((rule for rule in rule_catalog() if rule["rule_id"] == rule_id), None)
        # User-supplied values are parameters; no query string concatenation.
        query = "MATCH (r:Rule {rule_id: $rule_id}) RETURN r.rule_id AS rule_id, r.name AS name, r.source_type AS source_type, r.source_file AS source_file, r.source_url AS source_url, r.severity AS severity, r.emirates_status AS emirates_status"
        with self.driver.session() as session:
            row = session.run(query, rule_id=rule_id).single()
            return dict(row) if row else None
