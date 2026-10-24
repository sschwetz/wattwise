FROM node:24-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# The build context has no .git, so the commit comes in as a build arg.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod -R a+rX /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
