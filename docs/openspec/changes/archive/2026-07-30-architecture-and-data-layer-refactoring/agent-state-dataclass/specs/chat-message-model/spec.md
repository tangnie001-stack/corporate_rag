## ADDED Requirements

### Requirement: ChatMessage SHALL be a dataclass for message exchange

The system SHALL define a `ChatMessage` dataclass with `role: str` and `content: str` to replace `list[dict]` throughout the chat message pipeline.

#### Scenario: ChatManager returns list[ChatMessage]

- **WHEN** `ChatManager.get_history_async()` is called
- **THEN** it SHALL return `list[ChatMessage]` instead of `list[dict]`

#### Scenario: Retrieval and prompt accept list[ChatMessage]

- **WHEN** `rewrite_query()`, `build_prompt()`, or `build_simple_prompt()` is called
- **THEN** the `history` parameter SHALL be typed as `list[ChatMessage]`
- **AND** callers SHALL access `msg.role` and `msg.content` instead of `msg["role"]` and `msg["content"]`

### Requirement: Redis serialization SHALL remain compatible

The system SHALL continue to serialize ChatMessage to JSON for Redis storage, and deserialize back to ChatMessage on retrieval.

#### Scenario: Message round-trips through Redis

- **WHEN** a message is written to Redis via `json.dumps({"role": ..., "content": ...})`
- **AND** retrieved via `json.loads()`
- **THEN** ChatManager SHALL convert the dict back to `ChatMessage(role=..., content=...)`
