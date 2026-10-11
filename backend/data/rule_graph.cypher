// Synthetic explanatory graph; no passenger PII belongs in this graph.
CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (r:Rule) REQUIRE r.rule_id IS UNIQUE;
CREATE CONSTRAINT source_id_unique IF NOT EXISTS FOR (s:Source) REQUIRE s.source_id IS UNIQUE;

UNWIND [
 {rule_id:'BOOKING_CONFIRMED',name:'Booking must be confirmed',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'must be confirmed by airline owner'},
 {rule_id:'PAYMENT_COMPLETE',name:'Payment is complete and balance due is zero',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'must be confirmed by airline owner'},
 {rule_id:'JOURNEY_PRESENT',name:'At least one journey and segment exist',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'generic structural requirement'},
 {rule_id:'PASSENGER_NAME_COMPLETE',name:'Required passenger name fields are present',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'field length constraints must be confirmed'},
 {rule_id:'ADULT_ACCOMPANIMENT',name:'Adult required for reference minor rule',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'do not treat as universal legal or airline rule'},
 {rule_id:'CODESHARE_RESTRICTION',name:'Codeshare restriction in reference workflow',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'synthetic EK-only demo rule; confirm before any Emirates use'},
 {rule_id:'QUEUE_EMPTY',name:'Booking must not be queued',source_type:'reference_derived_mock_policy',source_file:'DomesticCodified / release 1.0.7',severity:'BLOCK',emirates_status:'must be confirmed by airline owner'},
 {rule_id:'SSR_REQUIREMENTS_VALID',name:'Special service requirements must be valid',source_type:'reference_derived_mock_policy',source_file:'release 1.0.7',severity:'BLOCK',emirates_status:'requires provider/airline semantics'},
 {rule_id:'CHECKIN_WINDOW',name:'Check-in occurs inside configured window',source_type:'official_public_reference_plus_mock',source_file:'Emirates public FAQ + mock policy',severity:'BLOCK',emirates_status:'public summary used only in mock'},
 {rule_id:'TIMATIC_REVIEW_GATE',name:'Unknown international document case must be escalated',source_type:'POC_SAFETY_POLICY',source_file:'SECURITY_GOVERNANCE.md',severity:'BLOCK',emirates_status:'must not be bypassed by an agent'}
] AS row
MERGE (r:Rule {rule_id: row.rule_id}) SET r += row;

MERGE (booking:DomainEntity {entity_id:'Booking',name:'Booking'})
MERGE (passenger:DomainEntity {entity_id:'Passenger',name:'Passenger'})
MERGE (journey:DomainEntity {entity_id:'Journey',name:'Journey'})
MERGE (passport:DomainEntity {entity_id:'Passport',name:'Passport'})
MERGE (travelRule:DomainEntity {entity_id:'TravelRule',name:'Travel requirement rule'})
MERGE (providerNav:Provider {provider_id:'MOCK_NAVITAIRE',name:'Mock Navitaire adapter',mode:'MOCK_ONLY'})
MERGE (providerTim:Provider {provider_id:'MOCK_TIMATIC',name:'Mock Timatic adapter',mode:'MOCK_ONLY'})
MERGE (booking)-[:HAS_PASSENGER]->(passenger)
MERGE (booking)-[:CONTAINS]->(journey)
MERGE (passenger)-[:PRESENTS]->(passport)
MERGE (journey)-[:ASSESSED_AGAINST]->(travelRule)
MERGE (booking)-[:RETRIEVED_BY]->(providerNav)
MERGE (passport)-[:ASSESSED_BY]->(providerTim);
