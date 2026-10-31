FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

EXPOSE 5173
# Vite dev server; /api is proxied to the api service (see vite.config.ts).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
