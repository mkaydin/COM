# Council of Minds — frontend
#
# nginx serves the static site and reverse-proxies /api and /ws to the
# backend. That is what makes the app reachable at http://localhost:8080
# without a file:// workaround: the pages call window.COM.api(), which needs
# a real origin, and the WebSocket needs a same-origin upgrade.

FROM nginx:1.27-alpine

RUN apk add --no-cache curl

COPY com_website/ /usr/share/nginx/html/
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=4s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost/forum.html || exit 1

CMD ["nginx", "-g", "daemon off;"]