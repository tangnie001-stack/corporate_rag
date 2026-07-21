# citation-enhancement Specification

## ADDED Requirements

### Requirement: Query-biased snippet extraction
The system SHALL use jieba segmentation to extract meaningful keywords (>1 character, excluding stop words) from the user query, find their positions in the chunk text, and return a context window around the first keyword match.

The query-biased snippet SHALL include highlight regions marking keyword match positions.

#### Scenario: Keywords found in chunk text
- **WHEN** query keywords are found in the chunk text
- **THEN** the system SHALL return a snippet with focus on the keyword region and a list of highlight positions

#### Scenario: No keywords match
- **WHEN** no query keywords are found in the chunk text
- **THEN** the system SHALL return a fallback snippet (first 200 characters) with `fallback: true`

### Requirement: Citation HTML with <mark> highlights
The system SHALL convert the query-biased snippet into HTML with `<mark>` tags for keyword highlights, suitable for direct embedding in the frontend.

#### Scenario: Highlighted citation HTML
- **WHEN** a snippet with highlights is built
- **THEN** the HTML SHALL contain `<mark>` tags around matched keyword regions

#### Scenario: Fallback citation returns plain text
- **WHEN** the snippet is a fallback (no keyword matches)
- **THEN** the HTML SHALL return plain escaped text without any `<mark>` tags

### Requirement: Inline citation numbering in LLM prompt
The system prompt SHALL include an instruction requiring the LLM to mark citations with inline numbered references in the format `[1][2]` at sentence end.

#### Scenario: Inline citation instruction in prompt
- **WHEN** a system prompt is built
- **THEN** it SHALL include the inline citation instruction at the end
