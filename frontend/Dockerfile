# TravelGuideMaster front end, served by Caddy.
#
# Two stages: Node builds the static bundle, and only the bundle is carried
# into the Caddy image. The final image holds no node_modules and no
# source — Caddy serves files and proxies /api, and needs nothing else.

FROM node:22-alpine AS build
WORKDIR /src

# Installed inside the image rather than copied from the host: a
# node_modules built on Windows carries Windows binaries for esbuild and
# Rollup, and the build then fails on Linux.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# VITE_ACCESS_CODE is deliberately not passed in. The client only reads it
# in development builds (see src/api/client.ts); in production the
# traveller types the code, and it never appears in the bundle.
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
