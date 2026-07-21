## ADDED Requirements

### Requirement: Knowledge base management page
The system SHALL provide a standalone KB management page at the root path /.
- SHALL display a list of all knowledge bases with names
- SHALL support creating new knowledge bases via a form
- SHALL support deleting existing knowledge bases
- SHALL display documents within a selected knowledge base
- SHALL support uploading documents via file picker
- SHALL show upload progress as spinner + result message

#### Scenario: Create and delete knowledge base
- **WHEN** user enters a name and clicks create
- **THEN** the KB appears in the list
- **WHEN** user clicks delete on a KB
- **THEN** the KB is removed from the list

### Requirement: Chat page
The system SHALL provide a chat page at /chat path.
- SHALL include a knowledge base selector dropdown
- SHALL display a modern chat-style message list (User/AI bubbles)
- SHALL support streaming text display (typewriter effect via EventSource)
- SHALL display citations as styled source references below the answer
- SHALL use EventSource API to consume SSE from /api/chat/stream

#### Scenario: Ask a question
- **WHEN** user selects a KB, types a question, and clicks send
- **THEN** the answer appears token by token in a chat bubble
- **THEN** citations appear below the answer

### Requirement: Visual design
The frontend SHALL present a modern, professional visual appearance suitable for financial industry demos.
- SHALL use a clean color scheme (dark sidebar / light content area)
- SHALL be responsive to different screen sizes
- SHALL use a consistent design system (via Tailwind CSS or Admin template)
