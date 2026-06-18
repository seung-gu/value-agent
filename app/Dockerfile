# --- Build stage: export Expo web to static files (./dist) ---
FROM node:22-alpine AS build
WORKDIR /app

# Install deps first -> this layer is cached unless the lockfile changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Expo inlines EXPO_PUBLIC_* at BUILD time, so the backend URL must exist here
# (not at runtime). Railway exposes service variables to the build as build args.
ARG EXPO_PUBLIC_API_URL
ENV EXPO_PUBLIC_API_URL=$EXPO_PUBLIC_API_URL
RUN npx expo export --platform web   # outputs the static site to ./dist

# --- Serve stage: tiny static file server on $PORT ---
FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
# Railway injects $PORT; bind 0.0.0.0 so it's reachable externally.
CMD ["sh", "-c", "serve -s dist -l tcp://0.0.0.0:${PORT:-3000}"]
