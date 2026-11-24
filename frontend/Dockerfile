# Imagen del frontend: compila la SPA y la sirve con nginx, que redirige /api al backend.
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

FROM nginx:1.28-alpine
# nginx sustituye las variables de entorno en las plantillas al arrancar.
ENV CHATBOT_BACKEND_URL=http://backend:8000 \
    CHATBOT_MAX_BODY_SIZE=60m \
    CHATBOT_TRUSTED_PROXIES= \
    NGINX_ENVSUBST_FILTER=^CHATBOT_
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
# Configuración de los proxies de confianza (IP real del cliente), generada al arrancar.
COPY --chmod=755 docker/25-proxies-de-confianza.sh /docker-entrypoint.d/
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
