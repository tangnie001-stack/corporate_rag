## ADDED Requirements

### Requirement: User registration and login
The system SHALL support automatic registration on first login. When a user provides account and password that do not exist in the `users` table, the system SHALL create a new user record and return a token.

#### Scenario: First-time login (auto-register)
- **WHEN** user sends POST /api/auth/login with {account, password} for a non-existing account
- **THEN** system creates a new user record with sha256(password), generates a token (UUID), stores token in Redis with TTL 30 days and MySQL, returns {token, user_id}

#### Scenario: Existing user login
- **WHEN** user sends POST /api/auth/login with {account, password} for an existing account
- **THEN** system verifies sha256(password), regenerates token, stores token in Redis and MySQL, returns {token, user_id}

#### Scenario: Wrong password
- **WHEN** user sends POST /api/auth/login with incorrect password
- **THEN** system returns 401

### Requirement: Token verification
The system SHALL verify tokens via GET /api/auth/verify. It SHALL read the token from the Cookie header, look it up in Redis, and return the user_id if valid.

#### Scenario: Valid token
- **WHEN** GET /api/auth/verify is called with a valid token Cookie
- **THEN** system returns {user_id, valid: true}

#### Scenario: Expired or invalid token
- **WHEN** GET /api/auth/verify is called with an invalid or expired token Cookie
- **THEN** system returns {valid: false}

### Requirement: API token enforcement
The system SHALL enforce token validation on all `/api/kbs/*` endpoints. Requests without a valid token SHALL receive 401 Unauthorized.

#### Scenario: Knowledge base API with valid token
- **WHEN** GET /api/kbs is called with a valid token Cookie
- **THEN** system returns knowledge bases filtered by the token's user_id

#### Scenario: Knowledge base API without token
- **WHEN** GET /api/kbs is called without a token Cookie
- **THEN** system returns 401 Unauthorized

### Requirement: Anonymous user
The system SHALL support anonymous usage on `/api/chat/*` and `/api/sessions/*` endpoints. Anonymous users SHALL be identified by a `user_id` Cookie set by the backend on first visit. Priority: `token` Cookie (logged-in) > `user_id` Cookie (anonymous) > generate new anonymous user_id.

#### Scenario: Anonymous first visit
- **WHEN** GET /api/chat/stream is called without `token` or `user_id` Cookie
- **THEN** backend generates a new UUID as anonymous user_id, sets `Set-Cookie: user_id=<uuid>; path=/; max-age=31536000`, and processes the chat request with that user_id

#### Scenario: Returning anonymous user
- **WHEN** GET /api/chat/stream is called with `user_id` Cookie but no `token` Cookie
- **THEN** backend uses the existing user_id from the Cookie

#### Scenario: Logged-in chat with token
- **WHEN** GET /api/chat/stream is called with a valid `token` Cookie
- **THEN** backend uses the token's user_id, ignores any `user_id` Cookie

### Requirement: Login page redirect
The frontend login page SHALL support a `redirect` query parameter. After successful login, the page SHALL redirect to the URL specified in the parameter, or to `/` if not provided.

#### Scenario: Login with redirect
- **WHEN** user logs in from /login.html?redirect=%2Fkbs
- **THEN** after successful login, frontend redirects to /kbs

#### Scenario: Knowledge base page without token
- **WHEN** user visits the knowledge base page without a valid token Cookie
- **THEN** frontend redirects to /login.html?redirect=%2F
