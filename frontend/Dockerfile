# syntax=docker/dockerfile:1

# ---- deps: install node_modules once, keyed only on the lockfile ----------
FROM node:22-alpine AS deps
# Next's precompiled binaries expect glibc symbols; libc6-compat provides the
# shim Alpine otherwise lacks.
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` (not `install`): installs exactly the lockfile, and fails loudly
# if package.json and the lock have drifted - which is what a build should do.
RUN npm ci

# ---- builder: compile the standalone server ------------------------------
FROM node:22-alpine AS builder
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined into the client bundle at BUILD time, not
# read at runtime - so they must be present here. Passing them only to
# `docker run` would silently produce a bundle with `undefined` baked in and
# a frontend that cannot reach the API or Supabase.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ---- runner: ship only the standalone output -----------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# The standalone bundle carries its own minimal server.js plus only the
# node_modules it actually needs.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# `public/` and `.next/static/` are deliberately NOT included in the
# standalone output (Next.js assumes a CDN serves them). Without these two
# lines the app boots but every static asset and font 404s.
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

# server.js, not `next start`: the standalone output is a self-contained
# server and `next` isn't installed in this stage.
CMD ["node", "server.js"]
