# Clerk authentication

Set `CLERK_JWT_KEY` to the PEM public key from the Clerk Dashboard API keys page.
The service verifies Clerk session tokens from the `Authorization: Bearer <token>` header.

Optional validation settings:

- `CLERK_ISSUER` checks the JWT `iss` claim.
- `CLERK_AUTHORIZED_PARTIES` accepts a comma-separated list checked against `azp`.
- `CLERK_AUDIENCE` checks the JWT `aud` claim.

After verification, the JWT `sub` claim becomes the server-owned `user_id`.
The service rejects requests that try to provide another `user_id`.
It also rejects access to threads whose checkpoint metadata belongs to another user.

This applies to vanilla invoke and stream endpoints, history, thread listing, and AG-UI.

When `CLERK_JWT_KEY` is not set, the existing `AUTH_SECRET` behavior remains available for
local development and single-owner deployments. That mode does not provide per-user identity.
