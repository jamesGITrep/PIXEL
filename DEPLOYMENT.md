# Deployment

## Architecture

```
users ──► frontend (nginx)  ── the only public endpoint
              │ /api/*
              ▼
          API Gateway        ── the only path to data: API, image reads, exports, uploads
              │
   private storage buckets ◄── workers (Spot, autoscaled) ── assembler
```

- Every microservice is its own image and runs on its own VMs, configured only through
  environment variables. Nothing relies on being co-located with another service.
- Users can reach only the frontend. It forwards `/api/*` to the API Gateway.
- Storage is private. Browsers never contact it: uploads are relayed by the gateway
  (single-use, size- and type-checked) and reads go through an allow-listed proxy.
- Workers and the assembler accept no inbound traffic except health checks. They pull work
  from the task-coordination bucket.

## Search engines and the admin console

- **The landing page is what search engines index.**
  - Every app URL declares `/` as its canonical page.
  - `sitemap.xml` lists only `/`, and the API is marked `noindex`.
  - `index.html` carries the title, description, social-share tags and structured data, plus the
    landing content as plain HTML, so crawlers read it without running JavaScript.
- **The admin console is unlisted and lives at `/admin1101`.** It must be typed manually:
  - Nothing links to it, and it is not in the sitemap or `robots.txt`.
  - Responses carry `noindex, no-store`, so it is never indexed or cached.
  - The path is not stored in the public JavaScript, and the console's code only loads on that URL.
  - `/admin` behaves like any unknown page (404, then the landing page).

  Hidden is not the same as protected: anyone who learns the URL can open it. Add a login before
  sharing access widely.

## Community moderation (admin accounts)

The admin console has a **Community Pool Moderation** section for listing and deleting
community photos. Telemetry on that page is read-only and still works for anyone with the URL.
Deleting photos is different: it needs a signed-in account whose username is in the gateway's
`ADMIN_USERNAMES` setting. Other accounts get 403 and guests get 401.

- **Local:** both compose files set `ADMIN_USERNAMES=pm_admin`. Sign up as `pm_admin` to moderate.
- **Google Cloud:**
  1. Sign up on the live site first.
  2. Add the username to `terraform/admins.auto.tfvars`.
  3. Redeploy.

  Never list a username before it exists: anyone who signed up with that name would become an
  admin.
- **What deleting removes:** the raw original, the 256px tile, the thumbnail, the colour index and
  the tag markers. New jobs stop using the photo at once. Finished artboards keep their pixels,
  and the tile inspector reports the photo as removed.

## Accounts and the personal vault

- **Accounts are stored in buckets, with no database.** Each account is one object in the
  task-coordination bucket:
  - `users/{username}.json` holds the profile and a bcrypt password hash.
  - `user-emails/{sha256(email)}.json` maps the email to the username.
  - Sign-up claims both with create-if-absent writes, so a username or email can't be taken twice.
- **The bucket's 30-day cleanup rule only covers short-lived data.** It applies to `jobs/`,
  `upload-tickets/`, `ingest-locks/`, `telemetry/` and `login-failures/`; `users/`, `user-emails/` and `auth/` never
  expire.
- **Sessions are signed cookies.**
  - The cookie is HttpOnly and SameSite=Lax, and lasts 30 days.
  - The signing key is created once at `auth/signing-key.json`, so every gateway VM can verify
    every session.
  - A password reset signs out all of that account's existing sessions.
- **Failed logins lock the account.** 10 failed logins within 15 minutes lock it. Each failure is
  its own object under `login-failures/`, because GCS allows only about one write per second to
  one object.
- **The personal vault belongs to the account.** It lives at `personal/{user_id}/`, so it is the
  same on every device. The gateway takes the vault from the session cookie and ignores any id
  the browser sends. Guests can still make mosaics from the community pool.
- **Each vault has a storage quota.** It defaults to 500 MB and is set with `VAULT_QUOTA_MB` on the
  gateway. Once a vault is full, new upload URLs are refused with 413.
- **`/account` lets signed-in users manage their own data.** It shows an account summary and
  storage used, can delete selected vault photos, and has a "Delete All My Data" danger zone.
  - The danger zone calls `DELETE /api/v1/users/me/purge` and requires the username or password.
  - It deletes the vault first, then the account records, then sweeps again for uploads that were
    still in flight.
  - Every session ends because the user record is gone, and the username and email become free
    again.
  - Mosaics the user already made are kept.
  - An admin who deletes their account should also be removed from `admin_usernames`.
- **Forgot password is a stub.** With no mail service yet, the reset link (valid for 30 minutes)
  only goes to the gateway log. In GCP, find it in Logs Explorer by searching for "Password reset
  link". The local stacks set `AUTH_DEV_RETURN_RESET_LINK=true` so the link is also returned in
  the response; never set this in GCP.

## Local (Docker Compose)

```powershell
docker compose up -d --build        # http://localhost:3000 is the only published port
```

The compose networks mirror the cloud firewall: the frontend can reach only the gateway,
and storage, workers and the assembler are on a private network.

### GCP parity test (no cloud account needed)

Runs every service in production mode (`USE_GCP=true`, the google-cloud-storage code path)
against a local GCS emulator:

```powershell
docker compose -p pixelgcp -f docker-compose.gcp-parity.yml up -d --build   # http://localhost:3001
docker compose -p pixelgcp -f docker-compose.gcp-parity.yml down -v
```

## Google Cloud

What `terraform/` creates:

| Service   | Runs on                                     | Reachable from                       |
|-----------|---------------------------------------------|--------------------------------------|
| frontend  | regional MIG, 2 × e2-small                  | internet, via external HTTP(S) LB    |
| gateway   | regional MIG, 2 × e2-standard-2             | frontend VMs only, via internal LB   |
| worker    | regional MIG, Spot e2-highcpu-4, autoscaled | nothing (health checks only)         |
| assembler | zonal MIG, 1 × e2-standard-4                | nothing (health checks only)         |

The setup also includes:
- a private VPC with no external VM IPs, plus Cloud NAT;
- four private buckets;
- Artifact Registry;
- one least-privilege service account per service;
- optional HTTPS with a managed certificate.

### Deploy

> **This creates billable resources.** At the default sizes, expect roughly **$300/month**
> while running (LB, NAT, 7 VMs). For a demo, reduce the sizing in `terraform.tfvars`, and run
> `-Destroy` when you are done.

1. Log in. Both commands are interactive:
   ```powershell
   gcloud auth login
   gcloud auth application-default login
   ```
2. Optional: preview what will be created:
   ```powershell
   .\scripts\deploy_gcp.ps1 -ProjectId <your-project> -PlanOnly
   ```
3. Deploy. This builds and pushes the four images, applies Terraform, and waits for the site:
   ```powershell
   .\scripts\deploy_gcp.ps1 -ProjectId <your-project>
   ```
   Add `-Domain mosaic.example.com` for HTTPS, then point that domain's DNS A record at the
   `public_ip` output.

Re-running the script ships a new image tag. Each instance group then does a rolling update
without downtime.

### Tear down

```powershell
.\scripts\deploy_gcp.ps1 -ProjectId <your-project> -Destroy
```

Buckets that still contain data are kept unless `force_destroy_buckets = true`.

Terraform is optional on your machine: if it is missing, the script runs the official
`hashicorp/terraform` container.
