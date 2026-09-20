## MODIFIED Requirements

### Requirement: Entity injection into chunk metadata

The system SHALL inject extracted entities into every chunk's metadata of that document before storing it into the `chunks` table.

The system SHALL also aggregate entities into `document.meta_info` (`{"entities": {...}}`) in the relational store as the document-level authoritative store.

#### Scenario: All chunks carry document entities
- **WHEN** a document with 50 chunks is ingested
- **THEN** all 50 chunk metadata records SHALL contain the same `company` / `report_period` / `sec_code` fields

#### Scenario: Document meta_info aggregation
- **WHEN** a document finishes ingestion
- **THEN** `document.meta_info` SHALL contain an `entities` key with the document-level entity dict
