# PhotosArena

PhotosArena is a small public photo comparison app. Each game contains five
pairwise choices. A tap records the vote and immediately advances; the fifth
vote shows the global crowd ranking. The owner view at `/owner` shows the same
ranking at any time and is protected by HTTP Basic authentication.

The service uses Python's standard library and SQLite. Votes are append-only,
and standings are recalculated from the vote history with Elo ratings. Each
round has one ballot token. A repeat of the same request returns the existing
next-round response without adding a vote; reusing that token to choose the
other photo is rejected.

## Local run

Requirements: Python 3.14, Docker with Compose, and macOS for the one-time image
processing tool.

1. Make a private environment file and set long random values for both secrets:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

2. Import the intended photos and update `photo-manifest.json` by following the
   image import steps below.
3. Start the app:

   ```sh
   bash ops/check-host-port.sh 8182 photosarena
   docker compose up --build
   ```

The local app listens at `http://127.0.0.1:8182`. Open `/owner` to see the
owner ranking; the browser asks for username `owner` and the `OWNER_SECRET` set
in `.env`.

## Photo import

The processor reads supported image files recursively, respects orientation,
resizes the longest edge to at most 2048 pixels, converts to sRGB JPEG, and
removes metadata marker segments. It writes into a new or empty output folder
and leaves source files untouched. Its manifest contains only content hashes
and R2 object keys, never source names or EXIF fields.

Choose the exact source folder before running this command. It processes every
supported image below that folder, so do not point it at Downloads until the
requested photo set and time window are resolved.

```sh
swift tools/process_photos.swift \
  --input /path/to/approved-photo-folder \
  --output "$PWD/photos-web" \
  --max-dimension 2048 \
  --quality 0.88
```

Upload the resulting public web copies to an R2 bucket using a token with only
the required R2 object-write permission:

```sh
export CLOUDFLARE_ACCOUNT_ID='your-account-id'
export CLOUDFLARE_API_TOKEN='read-from-your-secret-manager'
export R2_BUCKET_NAME='photosarena'
python3 tools/upload_r2.py "$PWD/photos-web"
cp photos-web/manifest.json photo-manifest.json
```

Set `PHOTO_BASE_URL` in `.env` to the bucket's configured public domain. Do not
commit `.env`, original images, or resized image files. The manifest is safe to
commit: it contains hash-based IDs and object keys only. Verify that the bucket
serves each object publicly before starting the app.

## Tests and checks

```sh
python3 -m pip install -r requirements-dev.txt
ruff check app tests
mypy app
python3 -m unittest discover -s tests -v
docker build --tag photosarena:local .
```

The image-processing tests use synthetic fixtures. The uploader tests use a
local fake HTTP endpoint; they never call Cloudflare.

## Deployment prerequisites

The GitHub workflow validates pull requests, builds a Linux image, and on a
push to `main` publishes the image to GHCR by commit and deploys that digest.
The deployment job targets the GitHub `production` environment. Configure
`main` protection to require a pull request review and the `validate` and
`docker-build` checks before merge. If deployments should also require an
environment approval, add required reviewers and branch restrictions to the
`production` environment in repository settings.

Create these as repository Actions secrets after the repositories are
available and access is configured:

- `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`: the limited Tailscale OAuth client.
- `DEPLOY_SSH_PRIVATE_KEY`: the unique private key for this repository.
- `DEPLOY_SSH_KNOWN_HOSTS`: the pinned SSH host key line for `100.124.202.79`.

Install `compose.yaml`, `photo-manifest.json`, and `ops/` in `/srv/photosarena`
with `appuser` as owner. Restrict this repository's public deploy key in
`appuser`'s `authorized_keys` to the PhotosArena wrapper, for example:

```text
restrict,command="/srv/photosarena/ops/remote-entrypoint.sh" ssh-ed25519 AAAA... photosarena-deploy
```

The wrapper accepts only `deploy ghcr.io/queryplanner/photosarena@sha256:…`
with an optional GHCR username; it rejects shells, tags, and extra arguments.
GHCR credentials travel over SSH stdin and are kept in a temporary Docker
config on the host. Never put them in the app's environment file.

Create the host `.env` with the same runtime settings, and ensure `appuser` can
run Docker Compose and write the named `photosarena_data` volume. The deployment
helper refuses port 8182 conflicts, takes an online SQLite backup before
migration, starts the new image, checks `/healthz`, and returns to the saved
digest if the new image fails.

Add this Caddy site block and validate/reload Caddy using the host's existing
configuration layout:

```caddyfile
photosarena.lordpatil.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8182
}
```

## Organization and account boundary

`queryplanner/photosarena` is a personal-owner repository, while the requested
shared OAuth secrets belong to the `lordpatil` organization. GitHub
organization secrets cannot be shared with a repository outside that
organization. The deploy workflow therefore needs either PhotosArena moved
under `lordpatil`, or explicit authorization to add the limited Tailscale
credentials as repository-level secrets on the public `queryplanner/photosarena`
repository. Keep separate deploy SSH keys for every repository. Do not enable
deployment until this scope is resolved.

The reusable bootstrap skill belongs in the private
`lordpatil/tailscale-cicd-bootstrap` repository. It should be installed locally
and copy workflow templates into consumer repositories rather than being a
runtime workflow dependency. A future private application repository must
pause for a GitHub plan change or a different secret arrangement before it
relies on organization secrets.
