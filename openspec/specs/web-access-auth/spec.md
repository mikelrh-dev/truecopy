# web-access-auth Specification

## Purpose

Give operators LAN browser access (mobile/tablet) behind code+password login, with all operator-facing text in Spanish and manual content in English.

## Requirements

### Requirement: LAN-only browser access

The system SHALL be reachable only from the local network and MUST run in a browser without app installation, on mobile and tablet viewports.

#### Scenario: Operator opens the UI on a tablet

- GIVEN a device on the LAN
- WHEN the operator opens the web URL
- THEN the interface loads without installation

#### Scenario: Request from outside the LAN

- GIVEN a request originating off-LAN
- WHEN it reaches the service
- THEN it is not served

### Requirement: Code + password login

Access SHALL require operator code and password. Unauthenticated requests SHALL land on a login page; failed logins MUST show a Spanish error and grant no access. SSO and fine-grained roles are out of scope.

#### Scenario: Valid credentials

- GIVEN a registered operator code and password
- WHEN the operator submits the login form
- THEN the application opens

#### Scenario: Invalid credentials

- GIVEN an unknown code or a wrong password
- WHEN the form is submitted
- THEN a Spanish error appears and access is denied

#### Scenario: Password with surrounding spaces

- GIVEN an account whose password contains leading or trailing spaces
- WHEN the operator submits that exact password
- THEN login succeeds (the secret is compared byte-for-byte, never trimmed); the trimmed form is a different secret and is rejected

### Requirement: No fixed bootstrap credential

The first boot SHALL create the owner account with a RANDOM password generated at install time (`secrets.token_urlsafe`), printed once to the server console; no password literal may ship in the source, and a second boot MUST NOT reset an existing owner. The owner rotates it from the UI (Owner > Códigos).

#### Scenario: First boot prints a random owner password

- GIVEN no stored operator codes
- WHEN the app boots
- THEN an `admin` owner is created with a random password shown once on the console
- AND that password authenticates while the code literal from an older release does not

### Requirement: Owner self-operation

The authenticated owner SHALL be able to upload the current manual, trigger reindex, and manage operator codes from the UI, with status messages in Spanish.

#### Scenario: Owner uploads and reindexes

- GIVEN an authenticated owner
- WHEN a manual PDF is uploaded and reindex is triggered
- THEN ingestion starts and progress/status is reported in Spanish

### Requirement: Spanish UI, English manual content

All operator-facing UI text (labels, messages, errors) MUST be in Spanish; manual excerpts shown to operators remain in their original English.

#### Scenario: Language split

- GIVEN a logged-in operator viewing an answer
- WHEN the screen renders
- THEN UI chrome is Spanish and quoted manual content is English
