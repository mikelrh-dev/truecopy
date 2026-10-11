# Delta for web-access-auth

## MODIFIED Requirements

### Requirement: Code + password login

Access SHALL require operator code and password. Unauthenticated requests SHALL
land on a login page; failed logins MUST show a Spanish error and grant no
access. The password SHALL be compared byte-for-byte against the stored secret:
surrounding whitespace is part of the credential and MUST NOT be trimmed before
verification (trimming is only for the emptiness check). SSO and fine-grained
roles are out of scope.

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
- THEN login succeeds, and the trimmed form is rejected as a different secret

## ADDED Requirements

### Requirement: No fixed bootstrap credential

The first boot SHALL create the owner account with a RANDOM password generated at
install time (`secrets.token_urlsafe`), printed once to the server console; no
password literal may ship in the source, and a second boot MUST NOT reset an
existing owner. The owner rotates it from the UI (Owner > Códigos).

#### Scenario: First boot prints a random owner password

- GIVEN no stored operator codes
- WHEN the app boots
- THEN an `admin` owner is created with a random password shown once on the console
- AND the code literal from an older release does not authenticate
