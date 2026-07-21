## ADDED Requirements

### Requirement: Nginx reverse proxy
The system SHALL include an Nginx container as the entry point for all HTTP traffic.
- SHALL listen on port 80
- SHALL serve static HTML/CSS/JS files at root path /
- SHALL proxy /api/* requests to FastAPI backend at http://app:8000
- SHALL disable proxy buffering for SSE streaming support
- SHALL use nginx:alpine base image

#### Scenario: Static file serving
- **WHEN** user visits http://localhost/
- **THEN** Nginx serves the frontend index.html

#### Scenario: API proxy
- **WHEN** user sends request to http://localhost/api/kbs
- **THEN** Nginx forwards to FastAPI backend and returns the response

### Requirement: SSE proxy support
The reverse proxy SHALL support Server-Sent Events streaming.
- SHALL set proxy_buffering off
- SHALL set proxy_cache off
- SHALL set appropriate proxy_http_version and headers for streaming
